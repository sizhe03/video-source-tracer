#!/usr/bin/env python
"""Stage 1e - deep search sweep: read the whole 50-result page, not the top 20.

The single most expensive mistake in the reference trace, found by comparing
against another agent's transcript:

    query='甘樂助眠'  page_size=20  -> target ABSENT
    query='甘樂助眠'  page_size=50  -> target at rank 45

The upload we missed for four rounds (`BV1BuNgzREmz`, 877 s, two months earlier,
titled `甘樂助眠 可爱女仆的摩擦哄睡...`) sat at **position 45 of 50**. Every search
this skill ran used the API default of 20 results, so it was structurally
invisible — not "hard to find", impossible to see.

Two lessons, both encoded here:

1. **Always request `page_size=50`.** The default silently truncates.
2. **Query `<alias> + <genre>` explicitly.** Of these probes, only one reached
   the target; the rest of a "sensible" query set all missed:

       甘樂助眠      -> rank 45   <-- the only hit
       甘楽らむ      -> absent
       甘楽          -> absent
       甘樂 白丝     -> absent
       白丝吊带      -> absent
       可爱女仆 摩擦  -> absent
       太もも メイド  -> absent

   `甘楽らむ` (the full correct name!) missed, while the partial `甘樂助眠`
   hit. Ranking is not a proxy for correctness.

Usage:
    python deep_sweep.py BV1cU5C68EEi --alias 甘樂
    python deep_sweep.py BV1cU5C68EEi --alias 甘樂,甘楽,甘乐 --duration 662
"""
from __future__ import annotations

import json
import re
import sys
from urllib.parse import urlencode

from _common import bili_json, emit, note, setup_stdio, throttle

BILI_SEARCH = "https://api.bilibili.com/x/web-interface/search/type?"
BILI_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"

PAGE_SIZE = 50          # never lower this; the default 20 truncates
DEEP_PAGES = 3          # 50 x 3 = up to 150 results per query

GENRE = ["助眠", "ASMR", "asmr", "哄睡", "摩擦", "白丝", "丝袜", "麦克风",
         "大腿", "女仆", "触发音", "睡眠导入"]


def search(query: str, page: int = 1, page_size: int = PAGE_SIZE) -> list[dict]:
    throttle("api.bilibili.com", gap=9.0)
    url = BILI_SEARCH + urlencode({
        "search_type": "video",
        "keyword": query,
        "page": page,
        "page_size": page_size,
    })
    try:
        j = bili_json(url)
    except Exception as e:  # noqa: BLE001
        note(f"search failed {query!r} p{page}: {e}")
        return []
    if j.get("code") != 0:
        note(f"search code {j.get('code')} for {query!r}")
        return []
    out = []
    for rank, r in enumerate((j.get("data") or {}).get("result") or []):
        bv = r.get("bvid")
        if not bv:
            continue
        dur = r.get("duration") or "0:0"
        secs = 0
        for part in str(dur).split(":"):
            if str(part).strip().isdigit():
                secs = secs * 60 + int(part)
        out.append({
            "bvid": bv,
            "title": re.sub(r"<[^>]+>", "", r.get("title") or ""),
            "author": r.get("author"),
            "duration": secs,
            "views": r.get("play"),
            "page": page,
            "rank_in_page": rank + 1,
        })
    return out


def build_queries(aliases: list[str]) -> list[tuple[str, str]]:
    """(query, why) pairs. Alias x genre is the combination that works."""
    qs: list[tuple[str, str]] = []
    for a in aliases:
        for g in GENRE:
            qs.append((f"{a} {g}", f"alias:{a}+genre:{g}"))
        qs.append((a, f"alias:{a}+bare"))
    return qs


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2

    ref_bvid = argv[0]
    aliases: list[str] = []
    duration = 0
    band = (0.5, 1.6)
    pages = DEEP_PAGES

    if "--alias" in argv:
        aliases = [a.strip() for a in
                   argv[argv.index("--alias") + 1].split(",") if a.strip()]
    if "--duration" in argv:
        duration = int(argv[argv.index("--duration") + 1])
    if "--band" in argv:
        lo, hi = argv[argv.index("--band") + 1].split(",")
        band = (float(lo), float(hi))
    if "--pages" in argv:
        pages = int(argv[argv.index("--pages") + 1])

    throttle("api.bilibili.com", gap=2.0)
    v = bili_json(BILI_VIEW.format(bvid=ref_bvid))
    if v.get("code") != 0:
        emit({"ok": False, "error": "view failed"})
        return 1
    ref = v["data"]
    dur = duration or ref["duration"]

    if not aliases:
        emit({"ok": False, "error": "no --alias given; pass the short name(s)"})
        return 1

    queries = build_queries(aliases)
    note(f"{len(queries)} queries x {pages} pages x {PAGE_SIZE} results")

    found: dict[str, dict] = {}
    for q, why in queries:
        for page in range(1, pages + 1):
            hits = search(q, page)
            if not hits:
                break
            for h in hits:
                if h["bvid"] == ref_bvid:
                    continue
                h["via"] = why
                h["query"] = q
                prev = found.get(h["bvid"])
                # keep the SHALLOWEST rank per query: a rank of 45 matters,
                # and being present at all is the signal
                if prev is None or h["rank_in_page"] < prev["rank_in_page"]:
                    found[h["bvid"]] = h
            note(f"{q!r} p{page} -> {len(hits)} (unique {len(found)})")

    # Duration is a RANKING key, not a filter: a re-upload may add an intro,
    # concatenate episodes or slow down, so excluding by length loses real
    # upstreams. Candidates outside the band are kept and pushed to the bottom.
    lo_d, hi_d = dur * band[0], dur * band[1]

    def in_band(x: dict) -> bool:
        return not x["duration"] or lo_d <= x["duration"] <= hi_d

    ranked = sorted(found.values(),
                    key=lambda x: (not in_band(x), x["rank_in_page"],
                                   -x["duration"]))
    note(f"duration band [{lo_d:.0f},{hi_d:.0f}]s is a ranking hint only; "
         f"{sum(1 for x in found.values() if in_band(x))}/{len(found)} inside")
    for r in ranked:
        r["delta_s"] = r["duration"] - dur if r["duration"] else None

    emit({
        "ok": True,
        "reference": {"bvid": ref_bvid, "title": ref["title"],
                      "duration": dur, "author": ref["owner"]["name"]},
        "aliases": aliases,
        "queries_run": len(queries) * pages,
        "page_size": PAGE_SIZE,
        "unique_seen": len(found),
        "in_duration_band": sum(1 for x in found.values() if in_band(x)),
        "ranked_in_band": [r for r in ranked if in_band(r)],
        "ranked_out_of_band": [r for r in ranked if not in_band(r)],
        "note": (
            "nothing is dropped: the ranking is split by duration band only. "
            "Results far down a 50-item page are still real - a rank of 45/50 "
            "is exactly how the reference upload kept being missed, so do not "
            "read only the top of the list either."
        ),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
