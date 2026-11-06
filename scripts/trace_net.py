#!/usr/bin/env python
"""Network channels for trace.py, plus the injection point tests use.

Every channel is a plain callable so the whole engine can run offline against
stubs. `live_net()` returns the real implementations; `stub_net()` in the tests
returns fixtures with the same signatures.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
import urllib.request

from _common import http, note

DESKTOP = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}
# space/arc/search answers 412 to a desktop UA and 200 to a mobile one. This is
# measured, not guessed: desktop 412 / mobile 200 / android -799.
MOBILE = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
                  "Mobile/15E148 Safari/604.1",
    "Referer": "https://m.bilibili.com/",
}

API = "https://api.bilibili.com"


_BUVID: dict = {}


def buvid() -> str:
    """A device id bilibili hands out anonymously; search 412s without it."""
    if "v" in _BUVID:
        return _BUVID["v"]
    try:
        req = urllib.request.Request(f"{API}/x/frontend/finger/spi",
                                     headers=DESKTOP)
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        _BUVID["v"] = (j.get("data") or {}).get("b_3") or ""
    except Exception:                               # noqa: BLE001
        _BUVID["v"] = ""
    return _BUVID["v"]


def _json(url: str, headers: dict) -> dict:
    h = dict(headers)
    b3 = buvid()
    if b3:
        h["Cookie"] = f"buvid3={b3}"
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode("utf-8"))


def _bili(path: str, headers: dict = DESKTOP, **params) -> dict:
    q = urllib.parse.urlencode(params)
    return _json(f"{API}{path}?{q}", headers)


# ------------------------------------------------------------------ channels
def view(bvid: str) -> dict:
    return _bili("/x/web-interface/view", bvid=bvid)


def related(bvid: str) -> dict:
    return _bili("/x/web-interface/archive/related", bvid=bvid)


def space(mid: int, pn: int = 1) -> dict:
    return _bili("/x/space/arc/search", MOBILE,
                 mid=mid, ps=50, pn=pn, order="pubdate")


def search(kw: str, page: int = 1, order: str = "pubdate") -> dict:
    """Search, sorted by publication date by default.

    The default relevance sort hides the answer: measured on the reference
    case it returned 50 rows with neither copy present, while order=pubdate
    returned 6 rows containing both. A trace wants the oldest upload, so date
    order is the correct default -- relevance optimises for popularity.
    """
    params = dict(search_type="video", page=page, page_size=50, keyword=kw)
    if order:
        params["order"] = order
    return _bili("/x/web-interface/search/type", MOBILE, **params)


def comments(aid: int) -> dict:
    return _bili("/x/v2/reply/main", MOBILE, type=1, oid=aid, mode=3)


def ytsearch(q: str, n: int = 20) -> list[dict]:
    """YouTube search via yt-dlp. Returns a flat list, no metadata fetch."""
    p = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--playlist-end", str(n),
         "--print", "%(id)s\t%(title)s\t%(channel)s\t%(duration)s",
         f"ytsearch{n}:{q}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for line in (p.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or not parts[0]:
            continue
        dur = parts[3] if len(parts) > 3 else ""
        # yt-dlp writes "NA" for a live stream or an unknown length. Coercing
        # that to int raised and killed the whole channel.
        out.append({"id": parts[0], "title": parts[1],
                    "channel": parts[2],
                    "duration": dur if dur.isdigit() else None})
    return out


def ytchannel(cid: str, limit: int = 400) -> list[dict]:
    p = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--playlist-end", str(limit),
         "--print", "%(id)s\t%(title)s\t%(duration)s",
         f"https://www.youtube.com/channel/{cid}/videos"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = []
    for line in (p.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0]:
            out.append({"id": parts[0], "title": parts[1],
                        "duration": parts[2] if len(parts) > 2 else None})
    return out


def ytoembed(vid: str) -> dict:
    u = ("https://www.youtube.com/oembed?url="
         f"https://www.youtube.com/watch?v={vid}&format=json")
    return _json(u, DESKTOP)


def reverse_image(url: str) -> list[dict]:
    """Yandex two-step reverse image search. No browser required.

    Step 1 returns a cbirId as JSON; step 2 returns the result page whose
    CbirSitesList carries the matched pages.
    """
    enc = urllib.parse.quote(url, safe="")
    body = json.dumps({"blocks": [{"block": "b-page_type_search-by-image__link"}]})
    step1 = (f"https://yandex.com/images/search?rpt=imageview&url={enc}"
             f"&format=json&request={urllib.parse.quote(body)}")
    j = _json(step1, DESKTOP)
    cbir = None
    for blk in (j.get("blocks") or []):
        params = blk.get("params") or {}
        cbir = params.get("cbir_id") or cbir
    if not cbir:
        return []
    step2 = f"https://yandex.com/images/search?cbir_id={urllib.parse.quote(cbir)}"
    page = http(step2, timeout=30, headers=DESKTOP).body.decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'"(https?://[^"]+)"', page):
        u = m.group(1)
        if any(k in u for k in ("youtube.com", "youtu.be", "bilibili.com",
                                "douyin.com", "tiktok.com")):
            out.append({"url": u})
    return out[:50]


class LiveNet:
    """Bundles the real channels and the input probe."""

    def channels(self) -> dict:
        return {"view": view, "related": related, "space": space,
                "search": search, "comments": comments, "ytsearch": ytsearch,
                "ytchannel": ytchannel, "ytoembed": ytoembed,
                "reverse_image": reverse_image}

    def probe(self, target: str) -> dict:
        """Resolve a URL or bare id into the item dict trace.run() expects."""
        m = re.search(r"(BV[0-9A-Za-z]{10})", target)
        if not m:
            raise SystemExit(f"cannot find a bilibili id in {target!r}")
        bvid = m.group(1)
        j = view(bvid)
        if j.get("code") != 0:
            raise SystemExit(f"view failed for {bvid}: code {j.get('code')}")
        d = j.get("data") or {}
        owner = d.get("owner") or {}
        return {
            "platform": "bilibili", "id": d.get("bvid") or bvid,
            "aid": d.get("aid"), "title": re.sub(r"<[^>]+>", "",
                                                 d.get("title") or ""),
            "desc": d.get("desc") or "", "pubdate": d.get("pubdate") or 0,
            "duration": d.get("duration") or 0,
            "author": owner.get("name"), "mid": owner.get("mid"),
            "cover": d.get("pic"),
        }


def live_net() -> LiveNet:
    return LiveNet()
