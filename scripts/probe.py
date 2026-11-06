#!/usr/bin/env python
"""Stage 1a — single-item metadata probe, per platform, anonymous by default.

Every endpoint used here was verified to answer WITHOUT authentication:
  bilibili  x/web-interface/view            (bvid -> title/owner/pubdate/pic)
            x/web-interface/card            (mid -> fans/posts/sign)
            x/series/recArchivesByKeywords  (mid -> full upload list)
  youtube   /oembed                         (authoritative title + channel)
            /feeds/videos.xml?channel_id=   (exact upload dates)
            i.ytimg.com/vi/<id>/...         (thumbnails)

Usage:
    python probe.py BV1TrxQ64EXo
    python probe.py PnAKqVB9PPo
    python probe.py --mid 410544270          # bilibili uploader profile
"""
from __future__ import annotations

import re
import sys
from urllib.parse import quote

from _common import (cookie_arg, emit, http, http_json, note, session_state,
                     setup_stdio, strip_opt, throttle, use_cookies)

BILI_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
BILI_CARD = "https://api.bilibili.com/x/web-interface/card?mid={mid}"
BILI_ARCH = (
    "https://api.bilibili.com/x/series/recArchivesByKeywords"
    "?mid={mid}&keywords=&ps=50&pn=1"
)
YT_OEMBED = "https://www.youtube.com/oembed?url={url}&format=json"
YT_RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"


def bili_view(bvid: str) -> dict:
    throttle("api.bilibili.com")
    j = http_json(BILI_VIEW.format(bvid=bvid), headers={"Referer": "https://www.bilibili.com/"})
    if j.get("code") != 0:
        return {"ok": False, "code": j.get("code"), "message": j.get("message")}
    d = j["data"]
    return {
        "ok": True,
        "platform": "bilibili",
        "bvid": d.get("bvid"),
        "aid": d.get("aid"),
        "cid": d.get("cid"),
        "title": d.get("title"),
        "desc": (d.get("desc") or "")[:500],
        "pubdate": d.get("pubdate"),
        "duration": d.get("duration"),
        "dimension": d.get("dimension"),
        "cover": d.get("pic"),
        "copyright": d.get("copyright"),
        "argue": (d.get("argue_info") or {}).get("argue_msg"),
        "owner": {"mid": d["owner"]["mid"], "name": d["owner"]["name"]},
        "stat": {
            k: d["stat"].get(k) for k in ("view", "favorite", "like", "coin", "reply")
        },
    }


def bili_archives(mid: int | str) -> list[dict]:
    throttle("api.bilibili.com")
    try:
        j = http_json(
            BILI_ARCH.format(mid=mid), headers={"Referer": "https://space.bilibili.com/"}
        )
    except Exception as e:  # noqa: BLE001
        note(f"archive list failed for mid={mid}: {e}")
        return []
    if j.get("code") != 0:
        return []
    out = []
    for a in (j.get("data") or {}).get("archives") or []:
        out.append(
            {
                "id": a.get("bvid"),
                "title": a.get("title"),
                "pubdate": a.get("pubdate"),
                "duration": a.get("duration"),
                "views": (a.get("stat") or {}).get("view"),
            }
        )
    return out


def bili_card(mid: int | str) -> dict:
    throttle("api.bilibili.com")
    try:
        j = http_json(BILI_CARD.format(mid=mid))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    if j.get("code") != 0:
        return {"ok": False, "code": j.get("code")}
    c = j["data"]["card"]
    return {
        "ok": True,
        "mid": c.get("mid"),
        "name": c.get("name"),
        "sign": c.get("sign"),
        "fans": c.get("fans"),
        "attention": c.get("attention"),
        "archive_count": j["data"].get("archive_count"),
        "likes": j["data"].get("like_num"),
    }


def yt_probe(vid: str) -> dict:
    url = f"https://www.youtube.com/watch?v={vid}"
    throttle("www.youtube.com", gap=2.0)
    r = http(YT_OEMBED.format(url=quote(url, safe="")))
    out: dict = {"ok": r.status == 200, "platform": "youtube", "video_id": vid}
    if r.status == 200:
        j = r.json()
        out["title"] = j.get("title")
        out["author"] = j.get("author_name")
        out["author_url"] = j.get("author_url")
        m = re.search(r"/@([A-Za-z0-9_.-]+)", j.get("author_url") or "")
        if m:
            out["handle"] = m.group(1)
    else:
        out["oembed_status"] = r.status

    # channel id from the handle page (anonymous), then exact upload date via RSS
    handle = out.get("handle")
    if handle:
        throttle("www.youtube.com", gap=2.0)
        hr = http(f"https://www.youtube.com/@{handle}/videos", timeout=30)
        if hr.status == 200:
            t = hr.text()
            m = re.search(r'"channelId":"(UC[\w-]{22})"', t) or re.search(
                r"channel/(UC[\w-]{22})", t
            )
            if m:
                out["channel_id"] = m.group(1)
    cid = out.get("channel_id")
    if cid:
        throttle("www.youtube.com", gap=2.0)
        rr = http(YT_RSS.format(cid=cid))
        if rr.status == 200:
            for entry in re.findall(r"<entry>.*?</entry>", rr.text(), re.S):
                vm = re.search(r"<yt:videoId>(.*?)</yt:videoId>", entry)
                if vm and vm.group(1) == vid:
                    pm = re.search(r"<published>(.*?)</published>", entry)
                    out["published"] = pm.group(1) if pm else None
                    break
            # the feed only carries the 15 newest entries; older videos are absent
            out["rss_covers_newest"] = 15
    # playability: LOGIN_REQUIRED / age gate is exactly what triggers Stage 4
    throttle("www.youtube.com", gap=2.0)
    wr = http(url, timeout=30)
    if wr.status == 200:
        h = wr.text()
        m = re.search(r'"dateText":\{"simpleText":"(.*?)"', h)
        if m:
            out["date_text"] = m.group(1)
        m = re.search(r'"viewCount":\{"simpleText":"(.*?)"', h)
        if m:
            out["view_text"] = m.group(1)
        out["needs_login"] = "LOGIN_REQUIRED" in h or "Sign in to confirm" in h
    else:
        out["watch_status"] = wr.status
    return out


def main(argv: list[str]) -> int:
    setup_stdio()
    ck = cookie_arg(argv)
    loaded = use_cookies(ck)
    argv = strip_opt(argv, "--cookies")
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--mid":
        out = {"mid": argv[1], "card": bili_card(argv[1]),
               "archives": bili_archives(argv[1]), "session": session_state()}
        emit(out)
        return 0
    key = argv[0]
    if re.fullmatch(r"BV[0-9A-Za-z]{10}", key):
        v = bili_view(key)
        if v.get("ok"):
            mid = v["owner"]["mid"]
            v["card"] = bili_card(mid)
            v["owner_archives_count"] = len(bili_archives(mid))
        emit(v)
        return 0 if v.get("ok") else 1
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", key):
        emit(yt_probe(key))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
