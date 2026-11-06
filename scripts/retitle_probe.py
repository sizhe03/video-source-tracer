#!/usr/bin/env python
"""Stage 3d - find re-uploads that rewrote BOTH title and cover.

Why the obvious approach fails, measured on the reference case:

  A third, EARLIER re-upload existed:
      BV1BuNgzREmz  877 s  2026-03-08  "甘樂助眠 可爱女仆的摩擦哄睡💕白丝吊带抹茶..."
  and every title search missed it:
      用麦克风在姐姐的大腿上摩擦  -> absent
      甘樂助眠 / 甘乐助眠          -> absent
      白丝吊带 / 女仆 摩擦 / 丝袜 哄睡 -> absent

  Its thumbnail is a DIFFERENT picture (new cover art), so pHash through
  thumbnail pools also misses it: measured pool of 48 candidates, closest
  unrelated thumbnail was 112 bits away, the real one never entered the pool.

So neither text nor cover alone finds these. What survives a re-title is:

  * DURATION as a coarse filter (877 s vs our 662 s — inside a 0.5–1.6x band)
  * CONTENT invariants that a re-cut keeps: frame sequence, mirroring,
    a specific prop, a specific action at a specific timestamp

This script therefore does two things and NO more:
  1. widen the candidate set with many weak queries + a duration band
  2. fetch each candidate's real frames and score them against reference frames

It deliberately does not decide anything. Frame scoring is handed to verify.py,
which requires negative controls before it will speak.

Usage:
    python retitle_probe.py BV1cU5C68EEi
    python retitle_probe.py BV1cU5C68EEi --band 0.5,1.6 --frames 3
    python retitle_probe.py BV1cU5C68EEi --queries "白丝,女仆,抹茶,丝袜" --top 25
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
from urllib.parse import quote

from _common import bili_json, die, emit, ffmpeg, note, setup_stdio, throttle

try:
    import cv2
    import numpy as np
except Exception:  # noqa: BLE001
    die("numpy + opencv required")

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}
BILI_SEARCH = (
    "https://api.bilibili.com/x/web-interface/search/type"
    "?search_type=video&keyword={kw}&page={page}&page_size=50&order=pubdate"
)
BILI_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
BILI_PLAY = (
    "https://api.bilibili.com/x/player/playurl"
    "?bvid={bvid}&cid={cid}&qn=32&fnval=1"
)

# generous vocabulary: a re-titled upload may keep none of the original words,
# but it stays inside the genre
GENRE_QUERIES = [
    "白丝 摩擦 助眠", "丝袜 摩擦音 助眠", "女仆 助眠 摩擦",
    "大腿 麦克风 助眠", "白丝 吊带 助眠", "擦麦 助眠",
    "ASMR 丝袜 哄睡", "3dio 大腿 助眠",
]


def fetch(url: str, timeout: int = 20) -> bytes | None:
    try:
        return urllib.request.urlopen(
            urllib.request.Request(url, headers=UA), timeout=timeout
        ).read()
    except Exception:  # noqa: BLE001
        return None


def secs(d) -> int:
    if isinstance(d, int):
        return d
    if not d:
        return 0
    total = 0
    for p in str(d).split(":"):
        if p.strip().isdigit():
            total = total * 60 + int(p)
    return total


def harvest(queries: list[str], pages: int = 2) -> dict[str, dict]:
    pool: dict[str, dict] = {}
    for q in queries:
        for page in range(1, pages + 1):
            throttle("api.bilibili.com", gap=9.0)
            try:
                j = bili_json(BILI_SEARCH.format(kw=quote(q), page=page))
            except Exception as e:  # noqa: BLE001
                note(f"search {q!r} p{page} failed: {e}")
                continue
            if j.get("code") != 0:
                break
            res = (j.get("data") or {}).get("result") or []
            if not res:
                break
            for r in res:
                bv = r.get("bvid")
                if not bv:
                    continue
                pool.setdefault(bv, {
                    "bvid": bv,
                    "title": re.sub(r"<[^>]+>", "", r.get("title") or ""),
                    "author": r.get("author"),
                    "duration": secs(r.get("duration")),
                    "views": r.get("play"),
                    "via": q,
                })
            note(f"{q!r} p{page} -> {len(res)} (pool {len(pool)})")
    return pool


def grab_frames(bvid: str, cid: int, outdir: str,
                count: int = 3) -> list[str] | None:
    """Download a short low-quality stream and pull `count` spread frames."""
    throttle("api.bilibili.com", gap=3.0)
    try:
        j = bili_json(BILI_PLAY.format(bvid=bvid, cid=cid))
    except Exception as e:  # noqa: BLE001
        note(f"playurl {bvid} failed: {e}")
        return None
    if j.get("code") != 0:
        return None
    urls = [d.get("url") for d in ((j.get("data") or {}).get("durl") or [])]
    if not urls or not urls[0]:
        return None
    media = os.path.join(outdir, f"{bvid}.mp4")
    data = fetch(urls[0], timeout=40)
    if not data:
        return None
    with open(media, "wb") as f:
        f.write(data)
    paths = []
    for i in range(count):
        p = os.path.join(outdir, f"{bvid}_f{i}.jpg")
        cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
               "-ss", str(8 + i * 40), "-i", media, "-frames:v", "1",
               "-vf", "scale=480:-1", p]
        subprocess.run(cmd, capture_output=True)
        if os.path.exists(p):
            paths.append(p)
    try:
        os.unlink(media)
    except OSError:
        pass
    return paths or None


def frameprint(path: str):
    img = cv2.imread(path)
    if img is None:
        return None
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(g)[:16, :16]
    return (d > np.median(d[1:, :].flatten())).flatten()


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2

    ref_bvid = argv[0]
    band = (0.5, 1.6)
    frames = 2
    top = 20
    extra: list[str] = []

    if "--band" in argv:
        lo, hi = argv[argv.index("--band") + 1].split(",")
        band = (float(lo), float(hi))
    if "--frames" in argv:
        frames = int(argv[argv.index("--frames") + 1])
    if "--top" in argv:
        top = int(argv[argv.index("--top") + 1])
    if "--queries" in argv:
        extra = [q.strip() for q in
                 argv[argv.index("--queries") + 1].split(",") if q.strip()]

    throttle("api.bilibili.com", gap=2.0)
    v = bili_json(BILI_VIEW.format(bvid=ref_bvid))
    if v.get("code") != 0:
        emit({"ok": False, "error": "view failed"})
        return 1
    ref = v["data"]
    dur = ref["duration"]

    parts = [p for p in re.split(
        r"[\s_\-/|:：,，。、!！?？【】\[\]()（）]+", ref["title"]) if len(p) >= 2]
    queries = [ref["title"]] + parts[:5] + extra + GENRE_QUERIES

    pool = harvest(queries, pages=2)
    lo_d, hi_d = dur * band[0], dur * band[1]
    # keep everything; the band only orders the work queue. Review finding:
    # hard-excluding by duration dropped a same-title 2x-length earlier copy.
    def in_band(x: dict) -> bool:
        return not x["duration"] or lo_d <= x["duration"] <= hi_d

    outside = sum(1 for x in pool.values() if not in_band(x))
    note(f"duration band [{lo_d:.0f},{hi_d:.0f}]s is a hint only; "
         f"{outside} candidate(s) outside it are kept")

    tmp = tempfile.mkdtemp(prefix="retitle_")
    # reference frames
    ref_frames = grab_frames(ref_bvid, ref["cid"], tmp, count=frames)
    if not ref_frames:
        emit({"ok": False, "error": "cannot obtain reference frames"})
        return 1
    ref_prints = [p for p in (frameprint(f) for f in ref_frames) if p is not None]
    note(f"reference frames: {len(ref_prints)}")

    def score(meta):
        bv = meta["bvid"]
        throttle("api.bilibili.com", gap=2.0)
        try:
            vv = bili_json(BILI_VIEW.format(bvid=bv))
        except Exception:  # noqa: BLE001
            return None
        if vv.get("code") != 0:
            return None
        cand = vv["data"]
        cf_frames = grab_frames(bv, cand["cid"], tmp, count=frames)
        if not cf_frames:
            return None
        cps = [p for p in (frameprint(f) for f in cf_frames) if p is not None]
        if not cps:
            return None
        best = min(
            int((a != b).sum())
            for a in ref_prints for b in cps
        )
        return {
            "bvid": bv,
            "title": cand["title"],
            "author": cand["owner"]["name"],
            "duration": cand["duration"],
            "delta_s": cand["duration"] - dur,
            "views": cand["stat"]["view"],
            "resolution": f'{cand["dimension"]["width"]}x{cand["dimension"]["height"]}',
            "best_frameprint_distance": best,
        }

    def prio(meta: dict) -> int:
        return 0 if in_band(meta) else 1

    ordered = sorted(pool.values(), key=prio)
    results = []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
        for r in ex.map(score, ordered):
            if r:
                results.append(r)
    results.sort(key=lambda x: x["best_frameprint_distance"])

    emit({
        "ok": True,
        "reference": {"bvid": ref_bvid, "title": ref["title"],
                      "duration": dur, "author": ref["owner"]["name"]},
        "pool_size": len(pool),
        "outside_duration_band": outside,
        "scored": len(results),
        "ranked": results[:top],
        "next": ("frame distance is a RANKING, not a verdict. Confirm with "
                 "verify.py frames / sift WITH negative controls."),
        "caveat": ("mirrored or heavily re-graded re-uploads score worse than "
                   "they deserve; a poor rank is not proof of a different clip"),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
