# Round-5 FINAL independent adversarial review (not part of run_all.py).
# Drives the FULL T.run() pipeline with the REAL trace_net.verify_channel and
# a forged archive page / web snippet. Target: confirmed + forged/wrong UC id.
from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T           # noqa: E402
import trace_net as TN      # noqa: E402

FAKE = "UCFORGED1234567890123456"          # well-formed UC + 22
REAL = "UC1Y-RkDrol_nyXk7vx8wgUA"

ITEM = {
    "platform": "bilibili", "id": "BV1rCxf6MERM",
    "title": "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
    "pubdate": 1793835738, "duration": 1989,
    "author": "风吻茉莉2", "mid": 1464362113,
    # no aid -> no comments task (keeps the run fully offline)
}

# 3 independent uploaders, several titles carry the POMEchan brand -> brand lead
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


class Resp:
    status = 200

    def __init__(self, body):
        self.body = body.encode()


def make_page(desc, card_titles):
    """Both real card shapes: title="Watch ..." and video-card__title."""
    a = "".join(f'<a href="x" title="Watch {t}">' for t in card_titles[:12])
    b = "".join(f'<div class="video-card__title"><a>{t}</a></div>'
                for t in card_titles[12:24])
    return (f'<html><head><meta property="og:description" content="{desc}">'
            f'</head><body>{a}{b}</body></html>')


def build_net(desc, archive_titles, uc, verifier=None):
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

    def yt(q, n=20):
        return []

    def websearch(q):
        # attacker-controlled web hit injects a well-formed but forged UC id
        return [{"url": f"https://example.invalid/p/{uc}", "title": q,
                 "snippet": f"{q} archive mirror channel {uc}"}]

    def archive_channel(cid, pages=20):
        return [{"id": f"vid{i:03d}", "title": t}
                for i, t in enumerate(archive_titles)]

    def resolve_channel(name):
        return uc

    net = {
        "view": view, "space": space, "related": related, "search": search,
        "ytsearch": yt, "websearch": websearch,
        "archive_channel": archive_channel, "resolve_channel": resolve_channel,
        "verify_channel": verifier or TN.verify_channel,
    }
    return net


def drive(label, desc, archive_titles, card_titles, uc, verifier=None):
    net = build_net(desc, archive_titles, uc, verifier=verifier)
    original_http = TN.http
    TN.http = lambda *a, **k: Resp(make_page(desc, card_titles))
    try:
        out = T.run(ITEM, net=net, budget_s=6, max_requests=80,
                    gaps={"api.bilibili.com": 0.01, "www.youtube.com": 0.01,
                          "theasmrindex.com": 0.01, "yandex.com": 0.01,
                          "i0.hdslb.com": 0.01})
    finally:
        TN.http = original_http
    cc = out["creator_conclusion"]
    arch = [c for c in out["candidates"] if c.get("archive_of")]
    rec = {
        "level": cc["level"],
        "name": cc["name"],
        "youtube_channel": cc.get("ids", {}).get("youtube_channel"),
        "witness_size": (cc.get("witness") or {}).get("catalogue_size"),
        "genre_matches": (cc.get("witness") or {}).get("genre_matches"),
        "channel_verified_flags": sorted({c.get("channel_verified") for c in arch},
                                         key=str),
        "archive_rows": len(arch),
    }
    print(f"  {label}: {json.dumps(rec, ensure_ascii=False)}")
    return rec


ASMR25 = [f"【4KASMR】睡眠導入 太もも 触发音 {i}" for i in range(25)]
TUT25 = [f"Chapter {i}: Learn Python No. {i}, Heart of the Year" for i in range(25)]
MUSIC25 = [f"Pastel Room remix {i} Nyan cat 太鼓の達人" for i in range(25)]
MIX25 = ASMR25[:8] + TUT25[:17]
MASSAGE25 = [f"Massage chair review 2024 edition {i}" for i in range(25)]
ASMR239 = [f"【4KASMR】睡眠導入 太もも {i}" for i in range(239)]

print("=== A-D mandated cases (full T.run + real verify_channel) ===")
A = drive("A  AnotherCreator; see also pomechan + 25 ASMR + forged UC",
          "Watch all 25 ASMR videos from AnotherCreator; see also pomechan",
          ASMR25, ASMR25, FAKE)
B = drive("B  25 english tutorials + forged UC",
          "Watch all 25 ASMR videos from pomechan", TUT25, TUT25, FAKE)
C = drive("C  namesake musician Pome-chan + music",
          "Watch all 25 ASMR videos from Pome-chan", MUSIC25, MUSIC25, FAKE)
D = drive("D  control: real POMEchan + 239 ASMR + real UC",
          "Watch all 239 ASMR videos from POMEchan on The ASMR Index",
          ASMR239, ASMR239, REAL)

print()
print("=== E: round-5 NEW P0 probes (same full chain) ===")
E1 = drive("E1 fan-club superstring 'pomechan-fan-club'",
           "Watch all 25 ASMR videos from pomechan-fan-club on The ASMR Index",
           ASMR25, ASMR25, FAKE)
E2 = drive("E2 different owner but name inside owner",
           "Watch all 25 ASMR videos from AnotherCreator pomechan tribute on The ASMR Index",
           ASMR25, ASMR25, FAKE)
E3 = drive("E3 short owner substring 'pome' (no site suffix)",
           "Watch all 25 ASMR videos from pome",
           ASMR25, ASMR25, FAKE)
E4 = drive("E4 8/25 ASMR + 17 tutorials (1/3 gate)",
           "Watch all 25 ASMR videos from pomechan on The ASMR Index",
           MIX25, MIX25[:24], FAKE)
E5 = drive("E5 non-ASMR 'Massage chair review' x25",
           "Watch all 25 ASMR videos from pomechan on The ASMR Index",
           MASSAGE25, MASSAGE25, FAKE)
E6 = drive("E6 namesake same-name SAME-genre (documented ambiguity)",
           "Watch all 25 ASMR videos from Pome-chan on The ASMR Index",
           ASMR25, ASMR25, FAKE)
E7 = drive("E7 verifier stub returns truthy int 1 (runner bool() coercion)",
           "Watch all 25 ASMR videos from pomechan on The ASMR Index",
           ASMR25, ASMR25, FAKE, verifier=lambda cid, name: 1)

print()
print("=== F: verify_channel unit surface (real function) ===")
def vc(desc, name="pomechan", cid=FAKE):
    o = TN.http
    TN.http = lambda *a, **k: Resp(make_page(desc, ASMR25))
    try:
        return TN.verify_channel(cid, name)
    finally:
        TN.http = o

print("  F1 AnotherCreator; see also pomechan ->",
      vc("Watch all 25 ASMR videos from AnotherCreator; see also pomechan"))
print("  F2 pomechan-fan-club                 ->",
      vc("Watch all 25 ASMR videos from pomechan-fan-club on The ASMR Index"))
print("  F3 'pome' (short substring)          ->",
      vc("Watch all 25 ASMR videos from pome"))
print("  F4 'AnotherCreator pomechan tribute'->",
      vc("Watch all 25 ASMR videos from AnotherCreator pomechan tribute on The ASMR Index"))
print("  F5 real POMEchan                    ->",
      vc("Watch all 239 ASMR videos from POMEchan on The ASMR Index", cid=REAL))

print()
print("=== G: genre gate residual substring hits ===")
for t in ["按摩椅评测 2024 年度盘点", "睡眠监测手环开箱", "耳朵结构医学讲解",
          "丝袜穿搭分享", "Massage chair review 2024", "Chasmrlings tutorial",
          "Learn Python No. 1: Heart of the Year"]:
    print(f"  _genre_hit({t!r}) = {T._genre_hit(t)}")

print()
print("=== H: verdicts ===")
verdicts = {
    "A_must_not_confirm": A["level"] != "confirmed" and not A["youtube_channel"],
    "B_must_not_confirm": B["level"] != "confirmed" and not B["youtube_channel"],
    "C_must_not_confirm": C["level"] != "confirmed" and not C["youtube_channel"],
    "D_must_confirm_real": (D["level"] == "confirmed"
                            and D["youtube_channel"] == [REAL]),
    "E1_P0_confirmed_forged": (E1["level"] == "confirmed"
                               and E1["youtube_channel"] == [FAKE]),
    "E2_P0_confirmed_forged": (E2["level"] == "confirmed"
                               and E2["youtube_channel"] == [FAKE]),
    "E3_P0_confirmed_forged": (E3["level"] == "confirmed"
                               and E3["youtube_channel"] == [FAKE]),
    "E4_P0_confirmed_forged": (E4["level"] == "confirmed"
                               and E4["youtube_channel"] == [FAKE]),
    "E5_P0_confirmed_forged": (E5["level"] == "confirmed"
                               and E5["youtube_channel"] == [FAKE]),
    "E6_same_name_same_genre": (E6["level"] == "confirmed"
                                and E6["youtube_channel"] == [FAKE]),
    "E7_truthy_coercion": (E7["level"] == "confirmed"
                           and E7["youtube_channel"] == [FAKE]),
}
for k, v in verdicts.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")
print()
print("ROUND5_DONE")
