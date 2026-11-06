# Round-5 independent adversarial review. Not part of run_all.py.
# Drives the FULL T.run() pipeline with forged net fixtures.
import sys, pathlib, json
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T
import trace_net as TN

FAKE_UC = "UCFORGED1234567890123456"
REAL_UC = "UC1Y-RkDrol_nyXk7vx8wgUA"

ITEM = {"platform": "bilibili", "id": "BV1rCxf6MERM",
        "title": "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
        "pubdate": 1793835738, "duration": 1989,
        "author": "风吻茉莉2", "mid": 1464362113}

# Three independent uploaders all crediting POMEchan -> strong_lead brand.
TITLES = [
    "【POMEChan ASMR】4K _ 极致漆皮挤压声 _ 大腿枕 _ 3Dio",
    "【POMEchan】精油涂抹、轻拍大腿",
    "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
    "【POMEChan ASMR】4K _ 筋膜枪放松&精油按摩 _ 厚黑",
]
POOL = []
for acct, pref in (("风吻茉莉2", "F"), ("阿猛al", "A"), ("三号搬运", "C")):
    for i, t in enumerate(TITLES):
        POOL.append({"bvid": f"BV{pref}{i:08d}", "title": t, "author": acct,
                     "pubdate": ITEM["pubdate"] - i * 86400})

class Resp:
    status = 200
    def __init__(self, body): self.body = body.encode()


def build_net(desc_html, archive_titles, uc):
    """A complete forged net. verify_channel is the REAL function, fed a
    forged archive page; archive_channel returns forged rows."""
    def view(b):
        return {"code": 0, "data": {"bvid": ITEM["id"], "aid": 1,
                "title": ITEM["title"], "pubdate": ITEM["pubdate"],
                "duration": 1989,
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
    def yt(q, n=20):
        return []
    def websearch(q):
        # a web snippet that hands over the forged UC id
        return [{"url": f"https://x/{uc}", "title": q,
                 "snippet": f"{q} channel {uc}"}]
    def archive_channel(cid, pages=20):
        return [{"id": f"vid{i:03d}", "title": t}
                for i, t in enumerate(archive_titles)]
    def resolve_channel(name):
        return uc
    return {"view": view, "space": space, "related": related,
            "search": search, "ytsearch": yt, "websearch": websearch,
            "archive_channel": archive_channel,
            "resolve_channel": resolve_channel,
            # the REAL verify_channel, with trace_net.http monkeypatched
            "verify_channel": TN.verify_channel}


def page(desc, titles):
    cards = "".join(f'<a href="x" title="Watch {t}">' for t in titles[:12])
    cards += "".join(f'<div class="video-card__title"><a>{t}</a></div>'
                     for t in titles[12:24])
    return (f'<html><head><meta property="og:description" content="{desc}">'
            f'</head><body>{cards}</body></html>')


def drive(label, desc, titles, uc):
    net = build_net(None, titles, uc)
    orig = TN.http
    TN.http = lambda *a, **k: Resp(page(desc, titles))
    try:
        out = T.run(ITEM, net=net, budget_s=8, max_requests=60,
                    gaps={"api.bilibili.com": 0.02, "www.youtube.com": 0.02})
    finally:
        TN.http = orig
    cc = out["creator_conclusion"]
    print(f"\n=== {label} ===")
    print("  level           :", cc["level"])
    print("  name            :", cc["name"])
    print("  youtube_channel :", cc.get("ids", {}).get("youtube_channel"))
    print("  witness         :", json.dumps(cc.get("witness"),
                                            ensure_ascii=False)[:200])
    arch = [c for c in out["candidates"] if c.get("archive_of")]
    print("  archive rows    :", len(arch),
          "verified:", {c.get("channel_verified") for c in arch})
    print("  blocked         :", out["blocked_requests"])
    return out


ASMR25 = [f"【4KASMR】睡眠導入 太もも 触发音 {i}" for i in range(25)]
TUT25 = [f"Chapter {i}: Learn Python No. {i}, Heart of the Year"
         for i in range(25)]
MUSIC25 = [f"Pastel Room remix {i} Nyan cat 太鼓の達人" for i in range(25)]

results = {}

# A. page claims AnotherCreator, merely mentions pomechan
a = drive("A: AnotherCreator + 'see also pomechan' + forged UC + 25 ASMR",
          "Watch all 25 ASMR videos from AnotherCreator; see also pomechan",
          ASMR25, FAKE_UC)
results["A"] = (a["creator_conclusion"]["level"] != "confirmed"
                and not a["creator_conclusion"].get("ids", {}).get("youtube_channel"))

# B. English tutorials + forged UC, page claims pomechan
b = drive("B: 25 'Learn Python No.1 / Heart of the Year' + forged UC",
          "Watch all 25 ASMR videos from pomechan", TUT25, FAKE_UC)
results["B"] = (b["creator_conclusion"]["level"] != "confirmed"
                and not b["creator_conclusion"].get("ids", {}).get("youtube_channel"))

# C. namesake musician, self-declares Pome-chan, music titles
c = drive("C: namesake musician self-declares Pome-chan + music",
          "Watch all 25 ASMR videos from Pome-chan", MUSIC25, FAKE_UC)
results["C"] = (c["creator_conclusion"]["level"] != "confirmed"
                and not c["creator_conclusion"].get("ids", {}).get("youtube_channel"))

# D. control: real POMEchan
d = drive("D: REAL POMEchan self-declares + 239 ASMR",
          "Watch all 239 ASMR videos from POMEchan on The ASMR Index",
          [f"【4KASMR】睡眠導入 太もも {i}" for i in range(239)], REAL_UC)
results["D"] = (d["creator_conclusion"]["level"] == "confirmed"
                and d["creator_conclusion"].get("ids", {}).get("youtube_channel") == [REAL_UC])

# E. round-5 NEW attack: the channel self-declares "pomechan-fan-club",
# a DIFFERENT account, and 'from <X>' substring accepts it as pomechan.
e = drive("E: pomechan-fan-club self-declares + 25 ASMR reuploads",
          "Watch all 25 ASMR videos from pomechan-fan-club on The ASMR Index",
          ASMR25, FAKE_UC)
results["E_fanclub_not_confirmed"] = e["creator_conclusion"]["level"] != "confirmed"
results["E_fanclub_id_not_emitted"] = (
    not e["creator_conclusion"].get("ids", {}).get("youtube_channel"))

print("\n==== SUMMARY ====")
for k, v in results.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")
sys.exit(0 if all(results.values()) else 1)
