# Check whether case A's T.run() output leaks the forged UC anywhere.
import json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T, trace_net as TN

FAKE = "UCFORGED1234567890123456"
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
ASMR = [f"【4KASMR】睡眠導入 太もも 触发音 {i}" for i in range(25)]
DESC = "Watch all 25 ASMR videos from AnotherCreator; see also pomechan"

class Resp:
    status = 200
    def __init__(self, body): self.body = body.encode("utf-8")

def page():
    cards = "".join('<a href="https://www.theasmrindex.com/video/VID%08d" '
                    'title="Watch %s"></a>' % (i, t) for i, t in enumerate(ASMR))
    return ('<html><head><meta property="og:description" content="%s">'
            '</head><body>%s</body></html>' % (DESC, cards))

def net():
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
        kl=(kw or "").lower(); rows=[r for r in POOL if kl in r["title"].lower()] or POOL
        return {"code": 0, "data": {"result": [
            {"bvid": r["bvid"], "title": r["title"], "author": r["author"],
             "pubdate": r["pubdate"], "duration": "33:09", "pic": None} for r in rows]}}
    def websearch(q):
        return [{"url": f"https://example.invalid/p/{FAKE}", "title": q,
                 "snippet": f"mirror {FAKE}"}]
    live = TN.LiveNet().channels()
    return {**live, "view": view, "space": space, "related": related,
            "search": search, "ytsearch": lambda q, n=20: [],
            "websearch": websearch, "resolve_channel": lambda name: FAKE}

orig = TN.http
TN.http = lambda *a, **k: Resp(page())
try:
    out = T.run(ITEM, net=net(), budget_s=8, max_requests=200,
                gaps={"api.bilibili.com": .01, "www.youtube.com": .01,
                      "theasmrindex.com": .01, "yandex.com": .01,
                      "i0.hdslb.com": .01})
finally:
    TN.http = orig
s = json.dumps(out, ensure_ascii=False)
cc = out["creator_conclusion"]
print("level:", cc["level"])
print("ids.youtube_channel:", cc["ids"]["youtube_channel"])
print("FAKE in creator_conclusion:", FAKE in json.dumps(cc, ensure_ascii=False))
print("FAKE in whole T.run output:", FAKE in s, "count:", s.count(FAKE))
print("sample candidate fields with FAKE:",
      [sorted(c.keys()) for c in out["candidates"] if c.get("channel_id") == FAKE][:1])
print("diagnosis:", out["diagnosis"])
