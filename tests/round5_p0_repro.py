# Round-5 P0 reproducible counterexample (scratch, NOT in run_all.py).
# A channel that is NOT the creator (a fan-club / reupload account) is reported
# as `confirmed` with ITS OWN channel id emitted as the original creator's id.
#
#   python tests/round5_p0_repro.py
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T           # noqa: E402
import trace_net as TN      # noqa: E402

# A well-formed UC id belonging to the FAN CLUB, not to POMEchan.
FANCLUB_CID = "UCFANCLUB000000000000000"      # UC + 22 chars
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

# Real archive meta shape, but the channel declares ITSELF as a fan club.
DESC = ("Watch all 25 ASMR videos from pomechan-fan-club on The ASMR Index")
CARDS = [f"【4KASMR】睡眠導入 太もも 触发音 {i}" for i in range(25)]


class Resp:
    status = 200

    def __init__(self, body):
        self.body = body.encode()


def page():
    a = "".join(f'<a href="x" title="Watch {t}">' for t in CARDS[:12])
    b = "".join(f'<div class="video-card__title"><a>{t}</a></div>'
                for t in CARDS[12:24])
    return (f'<html><head><meta property="og:description" content="{DESC}">'
            f'</head><body>{a}{b}</body></html>')


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
             "pic": "http://i0.hdslb.com/x.jpg"} for r in rows]}}

    def websearch(q):
        return [{"url": f"https://example.invalid/p/{FANCLUB_CID}", "title": q,
                 "snippet": f"fan club archive mirror {FANCLUB_CID}"}]

    def archive_channel(cid, pages=20):
        return [{"id": f"vid{i:03d}", "title": t}
                for i, t in enumerate(CARDS)]

    return {"view": view, "space": space, "related": related,
            "search": search, "ytsearch": lambda q, n=20: [],
            "websearch": websearch, "archive_channel": archive_channel,
            "resolve_channel": lambda name: FANCLUB_CID,
            "verify_channel": TN.verify_channel}


# Show the exact normalization that makes the wrong channel verify.
def norm(x):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff\u3040-\u30ff]", "",
                  (x or "").lower().replace("asmr", ""))

m = re.search(r"\d+\s*ASMR\s*videos\s*from\s+([^.;,]{2,80})", DESC, re.I)
owner = norm(m.group(1))
print("round4 'from <X>' capture :", repr(m.group(1)))
print("norm(owner)              :", repr(owner))
print("norm(query name)         :", repr(norm("pomechan")))
print("key in owner             :", norm("pomechan") in owner)

orig = TN.http
TN.http = lambda *a, **k: Resp(page())
try:
    out = T.run(ITEM, net=net(), budget_s=6, max_requests=80,
                gaps={"api.bilibili.com": 0.01, "www.youtube.com": 0.01,
                      "theasmrindex.com": 0.01, "yandex.com": 0.01,
                      "i0.hdslb.com": 0.01})
finally:
    TN.http = orig

cc = out["creator_conclusion"]
print()
print("creator_conclusion.level            :", cc["level"])
print("creator_conclusion.name             :", cc["name"])
print("creator_conclusion.ids.youtube_channel:", cc["ids"]["youtube_channel"])
print("creator_conclusion.witness          :",
      json.dumps(cc["witness"], ensure_ascii=False))
arch = [c for c in out["candidates"] if c.get("archive_of")]
print("archived rows / channel_verified    :", len(arch),
      sorted({c.get("channel_verified") for c in arch}, key=str))
print()
verdict = (cc["level"] == "confirmed"
           and cc["ids"]["youtube_channel"] == [FANCLUB_CID])
print("P0 REPRODUCED (confirmed + wrong channel id):", verdict)
sys.exit(0 if verdict else 1)
