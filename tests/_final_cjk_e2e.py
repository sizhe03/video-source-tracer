#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Minimal full-chain (T.run) reproduction of the CJK genre-gate bypass:
a namesake channel whose CJK titles are instructional (数据库触发器教程 /
睡眠医学讲座) passes the genre gate and its channel id is emitted as the
creator's id, because CJK genre substrings return True BEFORE the NONGENRE
guard. This is the CJK analogue of the "PostgreSQL sleep trigger tutorial"
case the commit claims to have fixed.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import trace as T

WRONG_CID = "UCNAMESAKE12345678901234"
ITEM = {"platform": "bilibili", "id": "BV1rCxf6MERM",
        "title": "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
        "pubdate": 1793835738, "duration": 1989,
        "author": "风吻茉莉2", "mid": 1464362113}


def titles_fn():
    # CJK instructional titles. Each contains a CJK genre substring
    # (触发 / 睡眠 / 按摩) AND a NONGENRE word (教程 / 讲座 / 指南).
    return (["数据库触发器教程 第%d讲" % i for i in range(13)]
            + ["睡眠医学讲座 第%d期" % i for i in range(12)])


def build_net():
    def view(b):
        return {"code": 0, "data": {
            "bvid": ITEM["id"], "aid": 1, "title": ITEM["title"],
            "pubdate": ITEM["pubdate"], "duration": 1989,
            "owner": {"name": "风吻茉莉2", "mid": 1464362113}}}

    def space(m, pn=1):
        return {"code": 0, "data": {"list": {"vlist": [
            {"bvid": "BVs1", "title": "【POMEchan】精油涂抹", "author": "风吻茉莉2",
             "created": 1793835738 - 86400, "length": "33:09"},
            {"bvid": "BVs2", "title": "【POMEchan ASMR】4K 大腿枕", "author": "风吻茉莉2",
             "created": 1793835738 - 2 * 86400, "length": "33:09"}]}}}

    def related(b):
        return {"code": 0, "data": [
            {"bvid": "BVr1", "title": "【POMEchan】精油涂抹", "pubdate": 1793835738 - 86400,
             "duration": 1989, "owner": {"name": "阿猛al"}},
            {"bvid": "BVr2", "title": "【POMEchan ASMR】大腿精油按摩", "pubdate": 1793835738 - 2 * 86400,
             "duration": 1989, "owner": {"name": "阿猛al"}}]}

    def search(kw, page=1, order="pubdate"):
        return {"code": 0, "data": {"result": []}}

    def yt(q, n=20):
        return []

    def websearch(q):
        return [{"url": f"https://theasmrindex.com/channel/{WRONG_CID}",
                 "title": "pomechan", "snippet": WRONG_CID}]

    def resolve_channel(name):
        return WRONG_CID

    def verify_channel(cid, name):
        # The namesake page self-declares "pomechan" (identity check passes);
        # its CJK instructional titles then pass the inline genre hit().
        return True

    def archive_channel(cid):
        return [{"id": f"av{i:03d}", "title": t} for i, t in enumerate(titles_fn())]

    def fetch_comments(aid):
        return [], {"declared_all_count": 0}

    return {"view": view, "space": space, "related": related, "search": search,
            "ytsearch": yt, "websearch": websearch, "resolve_channel": resolve_channel,
            "verify_channel": verify_channel, "archive_channel": archive_channel,
            "fetch_comments": fetch_comments}


out = T.run(ITEM, net=build_net(), budget_s=20, max_requests=60,
            gaps={"api.bilibili.com": 0.01, "www.youtube.com": 0.01,
                  "web": 0.01, "theasmrindex.com": 0.01})

cc = out["creator_conclusion"]
print("level           =", cc["level"])
print("name            =", cc["name"])
print("youtube_channel =", cc["ids"]["youtube_channel"])
print("witness_titles  =", (cc.get("witness") or {}).get("title"))
print("catalogue_size  =", (cc.get("witness") or {}).get("catalogue_size"))
print("diagnosis       =", out["diagnosis"]["stage"])

print()
print("genre hit probes:")
for t in ["数据库触发器教程 第1讲", "睡眠医学讲座 第1期",
          "PostgreSQL sleep trigger tutorial"]:
    print(f"  {T._genre_hit(t)!s:5}  {t}")

assert cc["ids"]["youtube_channel"] == [WRONG_CID], "expected wrong cid to leak"
assert cc["level"] == "strong_lead"
print()
print("CONFIRMED: a CJK instructional/medical channel leaks its id as the",
      "creator's channel (strong_lead + wrong youtube_channel).")
