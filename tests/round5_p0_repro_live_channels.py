# Round-5 P0 counterexample using the LIVE confirmation channels.
# No stubbed archive_channel / verify_channel: both are trace_net's real
# functions. Only the HTTP transport is replaced by a local fixture page.
# The page self-declares a FAN-CLUB channel (not POMEchan) and carries 25
# genuine ASMR titles; a web snippet injects that fan-club's well-formed UC id.
#
#   python tests/round5_p0_repro_live_channels.py
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T           # noqa: E402
import trace_net as TN      # noqa: E402

FANCLUB_CID = "UCFANCLUB000000000000000"
assert re.fullmatch(r"UC[A-Za-z0-9_-]{22}", FANCLUB_CID)

ITEM = {
    "platform": "bilibili", "id": "BV1rCxf6MERM",
    "title": "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
    "pubdate": 1793835738, "duration": 1989,
    "author": "风吻茉莉2", "mid": 1464362113,
}

_TITLES = [
    "【POMEChan ASMR】4K _ 极致漆皮挤压声 _ 大腿枕 _ 3Dio",
    "【POMEchan】精油涂抹、轻拍大腿",
    "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
    "【POMEChan ASMR】4K _ 筋膜枪放松&精油按摩 _ 厚黑",
]
POOL = []
for acct, pref in (("风吻茉莉2", "F"), ("阿猛al", "A"), ("三号搬运", "C")):
    for i, t in enumerate(_TITLES):
        POOL.append({"bvid": f"BV{pref}{i:08d}", "title": t, "author": acct,
                     "pubdate": ITEM["pubdate"] - i * 86400})

DESC = "Watch all 25 ASMR videos from pomechan-fan-club on The ASMR Index"
TITLES = [f"【4KASMR】睡眠導入 太もも 触发音 {i}" for i in range(25)]


class Resp:
    status = 200

    def __init__(self, body):
        self.body = body.encode("utf-8")


def fixture_page():
    # Both live parsers read this shape:
    #   verify_channel: title="Watch ..."
    #   archive_channel: absolute href + title="Watch ..."
    cards = "".join(
        '<a href="https://www.theasmrindex.com/video/%s" title="Watch %s"></a>'
        % (f"VID{i:08d}"[:11].ljust(11, "X"), t)
        for i, t in enumerate(TITLES))
    return (f'<html><head><meta property="og:description" content="{DESC}">'
            f'</head><body>{cards}</body></html>')


def net():
    def view(b):
        return {"code": 0, "data": {
            "bvid": ITEM["id"], "aid": 1, "title": ITEM["title"],
            "pubdate": ITEM["pubdate"], "duration": 1989,
            "owner": {"name": "风吻茉莉2", "mid": 1464362113}}}

    def space(m, pn=1):
        return {"code": 0, "data": {"list": {"vlist": [
            {"bvid": r["bvid"], "title": r["title"], "author": r["author"],
             "created": r["pubdate"], "length": "33:09"}
            for r in POOL if r["author"] == "风吻茉莉2"]}}}

    def related(b):
        return {"code": 0, "data": [
            {"bvid": r["bvid"], "title": r["title"], "pubdate": r["pubdate"],
             "duration": 1989, "owner": {"name": r["author"]}}
            for r in POOL if r["bvid"] != b]}

    def search(kw, page=1, order="pubdate"):
        kl = (kw or "").lower()
        rows = [r for r in POOL if kl in r["title"].lower()] or POOL
        return {"code": 0, "data": {"result": [
            {"bvid": r["bvid"], "title": r["title"], "author": r["author"],
             "pubdate": r["pubdate"], "duration": "33:09",
             "pic": None} for r in rows]}}

    def websearch(q):
        return [{"url": f"https://example.invalid/p/{FANCLUB_CID}", "title": q,
                 "snippet": f"fan-club archive mirror {FANCLUB_CID}"}]

    live = TN.LiveNet().channels()
    return {**live,
            "view": view, "space": space, "related": related,
            "search": search, "websearch": websearch,
            "resolve_channel": lambda name: FANCLUB_CID}


print("fixture channel id         :", FANCLUB_CID)
print("fixture meta description   :", DESC)
print("fixture card count         :", len(TITLES))
print("live verify_channel?       :", TN.verify_channel is not None)
print("live archive_channel?      :", TN.archive_channel is not None)

orig_http = TN.http
TN.http = lambda *a, **k: Resp(fixture_page())
try:
    # prove BOTH live parsers see the fixture
    print("verify_channel(fanclub)    :",
          TN.verify_channel(FANCLUB_CID, "pomechan"))
    rows = TN.archive_channel(FANCLUB_CID, pages=1)
    print("archive_channel rows       :", len(rows),
          "sample:", rows[0] if rows else None)
    out = T.run(ITEM, net=net(), budget_s=8, max_requests=120,
                gaps={"api.bilibili.com": 0.01, "www.youtube.com": 0.01,
                      "theasmrindex.com": 0.01, "yandex.com": 0.01,
                      "i0.hdslb.com": 0.01})
finally:
    TN.http = orig_http

cc = out["creator_conclusion"]
print()
print("creator_conclusion.level             :", cc["level"])
print("creator_conclusion.name              :", cc["name"])
print("creator_conclusion.ids.youtube_channel:", cc["ids"]["youtube_channel"])
print("creator_conclusion.witness           :",
      json.dumps(cc["witness"], ensure_ascii=False))
arch = [c for c in out["candidates"] if c.get("archive_of")]
print("archived rows / channel_verified     :", len(arch),
      sorted({c.get("channel_verified") for c in arch}, key=str))
print("requests_spent / tasks_run           :",
      out["requests_spent"], out["tasks_run"])
print()
verdict = (cc["level"] == "confirmed"
           and cc["ids"]["youtube_channel"] == [FANCLUB_CID])
print("P0 REPRODUCED WITH LIVE CHANNELS    :", verdict)
sys.exit(0 if verdict else 1)
