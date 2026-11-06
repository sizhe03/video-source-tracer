# Two more full-chain P0 variants with LIVE verify_channel/archive_channel.
# 1) web.search snippet alone injects the forged id; resolve_channel returns None.
# 2) verify_channel's page has NO title cards (genre self-check skipped), while
#    archive_channel's page has 25 ASMR cards (downstream genre gate passes).
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T           # noqa: E402
import trace_net as TN      # noqa: E402

FAKE = "UCFORGED1234567890123456"
assert re.fullmatch(r"UC[A-Za-z0-9_-]{22}", FAKE)

ITEM = {"platform": "bilibili", "id": "BV1rCxf6MERM",
        "title": "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
        "pubdate": 1793835738, "duration": 1989,
        "author": "风吻茉莉2", "mid": 1464362113}

_T = ["【POMEChan ASMR】4K _ 极致漆皮挤压声 _ 大腿枕 _ 3Dio",
      "【POMEchan】精油涂抹、轻拍大腿",
      "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
      "【POMEChan ASMR】4K _ 筋膜枪放松&精油按摩 _ 厚黑"]
POOL = []
for a, p in (("风吻茉莉2", "F"), ("阿猛al", "A"), ("三号搬运", "C")):
    for i, t in enumerate(_T):
        POOL.append({"bvid": f"BV{p}{i:08d}", "title": t, "author": a,
                     "pubdate": ITEM["pubdate"] - i * 86400})

DESC = "Watch all 25 ASMR videos from pomechan on The ASMR Index"
ASMR = [f"【4KASMR】睡眠導入 太もも 触发音 {i}" for i in range(25)]


class Resp:
    status = 200

    def __init__(self, body):
        self.body = body.encode("utf-8")


def cards():
    return "".join(
        '<a href="https://www.theasmrindex.com/video/VID%08d" title="Watch %s"></a>'
        % (i, t) for i, t in enumerate(ASMR))


def page(with_cards=True):
    return ('<html><head><meta property="og:description" content="%s">'
            '</head><body>%s</body></html>' % (DESC, cards() if with_cards else ""))


def net_factory(resolve_calls):
    def view(b):
        return {"code": 0, "data": {"bvid": ITEM["id"], "aid": 1,
                "title": ITEM["title"], "pubdate": ITEM["pubdate"],
                "duration": 1989, "owner": {"name": "风吻茉莉2",
                "mid": 1464362113}, "pic": None}}

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
             "pubdate": r["pubdate"], "duration": "33:09", "pic": None}
            for r in rows]}}

    def websearch(q):
        return [{"url": f"https://example.invalid/p/{FAKE}", "title": q,
                 "snippet": f"archive mirror {FAKE}"}]

    def resolve_channel(name):
        resolve_calls.append(name)
        return None                    # NO id from name resolution

    live = TN.LiveNet().channels()
    return {**live, "view": view, "space": space, "related": related,
            "search": search, "ytsearch": lambda q, n=20: [],
            "websearch": websearch, "resolve_channel": resolve_channel}


def show(label, out, resolve_calls):
    cc = out["creator_conclusion"]
    arch = [c for c in out["candidates"] if c.get("archive_of")]
    print(f"  {label}: level={cc['level']} name={cc['name']} "
          f"ids={cc['ids']['youtube_channel']} "
          f"witness={(cc.get('witness') or {}).get('catalogue_size')} "
          f"genre_matches={(cc.get('witness') or {}).get('genre_matches')} "
          f"verified={sorted({c.get('channel_verified') for c in arch}, key=str)} "
          f"archive_rows={len(arch)} resolve_calls={len(resolve_calls)} "
          f"web_edges={sum(1 for e in out['provenance'] if e.get('kind') == 'web')}")
    print("    diagnosis:", json.dumps(out["diagnosis"], ensure_ascii=False))


gaps = {"api.bilibili.com": 0.01, "www.youtube.com": 0.01,
        "theasmrindex.com": 0.01, "yandex.com": 0.01, "i0.hdslb.com": 0.01}

print("=== W: web.search-only id injection (resolve_channel returns None) ===")
calls = []
orig = TN.http
TN.http = lambda *a, **k: Resp(page(with_cards=True))
try:
    out_w = T.run(ITEM, net=net_factory(calls), budget_s=8, max_requests=200,
                  gaps=gaps)
finally:
    TN.http = orig
show("W", out_w, calls)

print()
print("=== N: verify page has no cards (self-check skipped), archive has cards ===")
calls2 = []


def discriminating_http(url, *a, **k):
    # verify_channel uses www.theasmrindex.com; archive_channel uses
    # theasmrindex.com (no www). Serve a cardless page only to the verifier.
    if "www.theasmrindex.com/channel/" in url:
        return Resp(page(with_cards=False))
    return Resp(page(with_cards=True))


orig = TN.http
TN.http = discriminating_http
try:
    out_n = T.run(ITEM, net=net_factory(calls2), budget_s=8, max_requests=200,
                  gaps=gaps)
finally:
    TN.http = orig
show("N", out_n, calls2)

print()
for label, out in (("W", out_w), ("N", out_n)):
    cc = out["creator_conclusion"]
    ok = (cc["level"] == "confirmed"
          and cc["ids"]["youtube_channel"] == [FAKE])
    print(f"  {label} P0 reproduced:", ok)
print("ROUND5_WEB_NOCARDS_DONE")
