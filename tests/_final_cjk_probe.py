#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Probe: CJK genre words bypass the NONGENRE guard in _genre_hit and the
inline hit() in verify_channel. Latin tokens go through the tutorial guard,
but CJK tokens return True BEFORE the guard is checked. Does this let a
non-ASMR CJK channel title pass the genre gate?
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import trace as T
import trace_net as TN

FAKE = "UCFORGED1234567890123456"
BRANDS = [{"brand": "pomechan", "accounts": 3,
           "platforms": ["bilibili", "youtube"], "sample": ["a", "b", "c"]}]

print("-- _genre_hit on CJK tutorial / lecture titles ------------------")
cases = [
    "耳朵疾病治疗讲座第1期",      # 耳朵 = CJK genre; 讲座 = NONGENRE
    "耳朵保健按摩教程第1期",      # 耳朵 + 按摩 = CJK genre; 教程 = NONGENRE
    "大腿肌肉解剖医学讲座",       # 大腿 = CJK genre; 讲座 = NONGENRE
    "睡眠医学综述 第一讲",        # 睡眠 = CJK genre; 综述/讲
    "睡眠呼吸暂停综合征诊治指南",  # 睡眠 = CJK genre; 指南 = NONGENRE
    "按摩手法教学视频",           # 按摩 = CJK genre; 教学
    "真正的ASMR助眠",            # control: should be True
    "PostgreSQL sleep trigger tutorial",  # latin control: should be False
]
for t in cases:
    print(f"  {T._genre_hit(t)!s:5}  {t}")

print()
print("-- _genre_overlap over 25 CJK medical-lecture titles -----------")
titles = [f"耳朵疾病治疗讲座第{i}期" for i in range(25)]
print("  overlap =", T._genre_overlap(titles), "/ 25")

print()
print("-- does this let a forged CJK-tutorial channel become the id? --")
rows = [{"platform": "youtube", "id": f"v{i:03d}", "title": t,
         "author": "pomechan", "archive_of": "pomechan",
         "channel_id": FAKE, "channel_verified": True}
        for i, t in enumerate(titles)]
c = T.creator_conclusion(BRANDS, rows, [], [])
print("  level          =", c["level"])
print("  witness        =", c.get("witness"))
print("  youtube_channel=", c["ids"]["youtube_channel"])

print()
print("-- and verify_channel's inline hit() on the same CJK titles -----")
# Replicate the page self-check by monkeypatching http.
_TT = "".join('<a href="x" title="Watch 耳朵疾病治疗讲座第%d期">' % i
              for i in range(12))


def _page():
    return ('<meta property="og:description" content="Watch all 25 ASMR '
            'videos from pomechan on The ASMR Index">') + _TT


class Resp:
    status = 200
    def __init__(self):
        self.body = _page().encode()


_orig = TN.http
TN.http = lambda *a, **k: Resp()
try:
    ver = TN.verify_channel(FAKE, "pomechan")
finally:
    TN.http = _orig
print("  verify_channel  =", ver)
