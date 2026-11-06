#!/usr/bin/env python
"""Stage 0 — normalize an input link into a platform + native id.

Key rule: short links are expanded by reading the 3xx `Location` header
directly, never by following the redirect to the landing page. The Location
header of b23.tv carries useful query params (up_id, share_source) and is not
behind the landing page's CORS / risk-control layer.

Usage:
    python resolve.py https://b23.tv/kwoerN3
    python resolve.py https://www.youtube.com/watch?v=PnAKqVB9PPo
    python resolve.py --json "BV1TrxQ64EXo"
"""
from __future__ import annotations

import re
import sys

from _common import emit, http, note, setup_stdio

SHORTENERS = {
    "b23.tv": "bilibili",
    "bili2233.cn": "bilibili",
    "youtu.be": "youtube",
    "v.douyin.com": "douyin",
    "xhslink.com": "xiaohongshu",
    "t.cn": "weibo",
    "vt.tiktok.com": "tiktok",
}

BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"av(\d+)", re.I)
YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
YT_URL_ID_RE = re.compile(
    r"(?:v=|/shorts/|/embed/|youtu\.be/|/live/)([A-Za-z0-9_-]{11})"
)
DOUYIN_ID_RE = re.compile(r"/video/(\d{15,25})")
XHS_ID_RE = re.compile(r"/(?:explore|discovery/item)/([0-9a-f]{24})")
PATH_ID_RE = re.compile(r"/video/(\d{15,25})")


def expand(url: str, limit: int = 6) -> tuple[str, list[dict]]:
    """Follow a short-link chain manually, one hop at a time."""
    chain: list[dict] = []
    cur = url
    for _ in range(limit):
        r = http(cur, follow=False, mobile=True)
        hop = {"status": r.status, "url": cur}
        if r.location:
            hop["location"] = r.location
        chain.append(hop)
        if r.status not in (301, 302, 303, 307, 308) or not r.location:
            break
        nxt = r.location
        if nxt.startswith("/"):
            from urllib.parse import urljoin

            nxt = urljoin(cur, nxt)
        cur = nxt
    return cur, chain


def guess_platform(url: str) -> str | None:
    low = url.lower()
    if "bilibili.com" in low or BV_RE.search(url):
        return "bilibili"
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    if "douyin.com" in low:
        return "douyin"
    if "xiaohongshu.com" in low:
        return "xiaohongshu"
    if "t.me" in low:
        return "telegram"
    if "nicovideo.jp" in low:
        return "niconico"
    return None


def native_id(platform: str | None, url: str) -> str | None:
    if platform == "bilibili":
        m = BV_RE.search(url)
        if m:
            return m.group(1)
        m = AV_RE.search(url)
        return f"av{m.group(1)}" if m else None
    if platform == "youtube":
        m = YT_URL_ID_RE.search(url)
        if m:
            return m.group(1)
        tail = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        return tail if YT_ID_RE.match(tail) else None
    if platform == "douyin":
        m = DOUYIN_ID_RE.search(url) or PATH_ID_RE.search(url)
        return m.group(1) if m else None
    if platform == "xiaohongshu":
        m = XHS_ID_RE.search(url)
        return m.group(1) if m else None
    return None


def query_param(url: str, key: str) -> str | None:
    from urllib.parse import parse_qs, urlparse

    vals = parse_qs(urlparse(url).query).get(key)
    return vals[0] if vals else None


def canonical(platform: str | None, nid: str | None, url: str) -> str:
    if platform == "bilibili" and nid and nid.startswith("BV"):
        return f"https://www.bilibili.com/video/{nid}/"
    if platform == "youtube" and nid:
        return f"https://www.youtube.com/watch?v={nid}"
    if platform == "douyin" and nid:
        return f"https://www.douyin.com/video/{nid}"
    return url


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2
    raw = argv[0].strip()
    if not raw.lower().startswith(("http://", "https://")):
        # bare id: BVxxxx or an 11-char YouTube id
        if BV_RE.fullmatch(raw):
            emit(
                {
                    "input": raw,
                    "chain": [],
                    "final_url": canonical("bilibili", raw, raw),
                    "platform": "bilibili",
                    "native_id": raw,
                    "needs_login": False,
                    "canonical_url": canonical("bilibili", raw, raw),
                    "share_params": {},
                }
            )
            return 0
        if YT_ID_RE.match(raw):
            emit(
                {
                    "input": raw,
                    "chain": [],
                    "final_url": canonical("youtube", raw, raw),
                    "platform": "youtube",
                    "native_id": raw,
                    "needs_login": "possibly",
                    "canonical_url": canonical("youtube", raw, raw),
                    "share_params": {},
                }
            )
            return 0
        print(__doc__)
        return 2

    host = raw.split("//", 1)[-1].split("/", 1)[0].lower()
    is_short = host in SHORTENERS
    final, chain = (expand(raw) if is_short else (raw, []))
    if is_short:
        note(f"expanded {len(chain)} hop(s) without following into the landing page")

    platform = guess_platform(final) or SHORTENERS.get(host)
    nid = native_id(platform, final)

    # bilibili share links carry up_id / share_source in the Location header
    share = {}
    for hop in chain:
        loc = hop.get("location")
        if not loc:
            continue
        for key in ("up_id", "share_source", "share_medium", "share_plat", "p"):
            v = query_param(loc, key)
            if v:
                share[key] = v

    out = {
        "input": raw,
        "chain": chain,
        "final_url": final,
        "platform": platform,
        "native_id": nid,
        "canonical_url": canonical(platform, nid, final),
        "share_params": share,
        # filled in by probe.py; here we only flag the obvious cases
        "needs_login": False if platform in ("bilibili", "youtube") else "unknown",
    }
    if platform == "bilibili" and nid:
        note("bilibili metadata endpoints (view / card / reply) are readable anonymously")
    emit(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
