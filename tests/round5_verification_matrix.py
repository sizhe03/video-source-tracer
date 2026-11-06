# Round-5 verification matrix. Not part of run_all.py.
# 1) A-D mandated cases through FULL T.run() with LIVE verify_channel AND
#    LIVE archive_channel (only the HTTP transport is a local fixture page).
# 2) round-4 item-by-item static / shape checks.
# 3) extra P0 paths on the same live-channel chain.
from __future__ import annotations

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T           # noqa: E402
import trace_net as TN      # noqa: E402

REAL = "UC1Y-RkDrol_nyXk7vx8wgUA"
FAKE = "UCFORGED1234567890123456"
FANCLUB = "UCFANCLUB000000000000000"
for cid in (REAL, FAKE, FANCLUB):
    assert re.fullmatch(r"UC[A-Za-z0-9_-]{22}", cid), cid

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

ASMR25 = [f"【4KASMR】睡眠導入 太もも 触发音 {i}" for i in range(25)]
TUT25 = [f"Chapter {i}: Learn Python No. {i}, Heart of the Year" for i in range(25)]
MUSIC25 = [f"Pastel Room remix {i} Nyan cat 太鼓の達人" for i in range(25)]
MASSAGE25 = [f"Massage chair review 2024 edition {i}" for i in range(25)]
ASMR239 = [f"【4KASMR】睡眠導入 太もも {i}" for i in range(239)]


class Resp:
    status = 200

    def __init__(self, body):
        self.body = body.encode("utf-8")


def fixture_page(desc, titles, *, video_card_titles=()):
    """title="Watch ..." cards are read by BOTH live parsers.
    video-card__title cards are read by verify_channel's genre self-check."""
    a = "".join(
        '<a href="https://www.theasmrindex.com/video/%s" title="Watch %s"></a>'
        % (f"VID{i:08d}", t) for i, t in enumerate(titles))
    b = "".join(f'<div class="video-card__title"><a>{t}</a></div>'
                for t in video_card_titles)
    return (f'<html><head><meta property="og:description" content="{desc}">'
            f'</head><body>{a}{b}</body></html>')


def build_net(desc, titles, uc, *, page_titles=None, video_card_titles=(),
              verifier=None):
    def view(b):
        return {"code": 0, "data": {
            "bvid": ITEM["id"], "aid": 1, "title": ITEM["title"],
            "pubdate": ITEM["pubdate"], "duration": 1989,
            "owner": {"name": "风吻茉莉2", "mid": 1464362113},
            "pic": None}}

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
        return [{"url": f"https://example.invalid/p/{uc}", "title": q,
                 "snippet": f"archive mirror {uc}"}]

    live = TN.LiveNet().channels()
    net = {**live,
           "view": view, "space": space, "related": related,
           "search": search, "ytsearch": lambda q, n=20: [],
           "websearch": websearch,
           "resolve_channel": lambda name: uc}
    if verifier is not None:
        net["verify_channel"] = verifier
    return net


def drive(label, desc, titles, uc, *, page_titles=None, video_card_titles=(),
          verifier=None):
    net = build_net(desc, titles, uc, page_titles=page_titles,
                    video_card_titles=video_card_titles, verifier=verifier)
    html = fixture_page(desc, page_titles or titles,
                        video_card_titles=video_card_titles)
    orig = TN.http
    TN.http = lambda *a, **k: Resp(html)
    try:
        if verifier is None:
            vc = TN.verify_channel(uc, "pomechan")
            rows = TN.archive_channel(uc, pages=1)
        else:
            vc, rows = "custom", []
        out = T.run(ITEM, net=net, budget_s=8, max_requests=200,
                    gaps={"api.bilibili.com": 0.01, "www.youtube.com": 0.01,
                          "theasmrindex.com": 0.01, "yandex.com": 0.01,
                          "i0.hdslb.com": 0.01})
    finally:
        TN.http = orig
    cc = out["creator_conclusion"]
    arch = [c for c in out["candidates"] if c.get("archive_of")]
    rec = {
        "level": cc["level"], "name": cc["name"],
        "ids": cc.get("ids", {}).get("youtube_channel"),
        "witness_size": (cc.get("witness") or {}).get("catalogue_size"),
        "genre_matches": (cc.get("witness") or {}).get("genre_matches"),
        "channel_verified": sorted({c.get("channel_verified") for c in arch},
                                   key=str),
        "archive_rows": len(arch),
        "verify_channel": vc,
        "live_archive_rows": len(rows),
    }
    print(f"  {label}: {json.dumps(rec, ensure_ascii=False)}")
    return rec


print("=== A-D: full T.run() with LIVE verify_channel + LIVE archive_channel ===")
A = drive("A", "Watch all 25 ASMR videos from AnotherCreator; see also pomechan",
          ASMR25, FAKE)
B = drive("B", "Watch all 25 ASMR videos from pomechan", TUT25, FAKE)
C = drive("C", "Watch all 25 ASMR videos from Pome-chan", MUSIC25, FAKE)
D = drive("D", "Watch all 239 ASMR videos from POMEchan on The ASMR Index",
          ASMR239, REAL)

print()
print("=== E: extra P0 paths on the same live-channel chain ===")
E1 = drive("E1 fan-club self-declares pomechan-fan-club",
           "Watch all 25 ASMR videos from pomechan-fan-club on The ASMR Index",
           ASMR25, FANCLUB)
E2 = drive("E2 name appears later inside the 'from <X>' capture",
           "Watch all 25 ASMR videos from AnotherCreator pomechan tribute on The ASMR Index",
           ASMR25, FAKE)
E3 = drive("E3 short owner 'pome' inside the query name",
           "Watch all 25 ASMR videos from pome", ASMR25, FAKE)
E5 = drive("E5 25 'Massage chair review' pass both genre gates",
           "Watch all 25 ASMR videos from pomechan on The ASMR Index",
           MASSAGE25, FAKE)
E7 = drive("E7 custom verifier returns truthy int 1 (bool() coercion)",
           "Watch all 25 ASMR videos from pomechan on The ASMR Index",
           ASMR25, FAKE, verifier=lambda cid, name: 1)

print()
print("=== F: round-4 static item checks ===")
print("  _GENRE_CJK =", sorted(T._GENRE_CJK))
print("  _GENRE_LATIN =", sorted(T._GENRE_LATIN))
print("  'asmr' in _GENRE_CJK ?", "asmr" in T._GENRE_CJK,
      "| 'ASMR' in _GENRE_CJK ?", "ASMR" in T._GENRE_CJK)
print("  'no' in _GENRE_LATIN ?", "no" in T._GENRE_LATIN)
print("  _NAME_PREFIX.pattern =", T._NAME_PREFIX.pattern)
print("  prefix order: '应该是这个' before '应该是' ?",
      T._NAME_PREFIX.pattern.index("应该是这个") <
      T._NAME_PREFIX.pattern.index("应该是"))
print("  _comment_names('应该是这个桑塔阿普亚') =",
      T._comment_names("应该是这个桑塔阿普亚"))
for t in ("Chasmrlings tutorial", "Learn Python No. 1: Heart of the Year",
          "ASMR 丝袜 助眠", "按摩椅评测 2024 年度盘点", "睡眠监测手环开箱",
          "耳朵结构医学讲解", "丝袜穿搭分享", "Massage chair review 2024"):
    print(f"  _genre_hit({t!r}) = {T._genre_hit(t)}")

print()
print("=== G: verify_channel genre self-check parses BOTH card shapes ===")
GOOD_DESC = "Watch all 25 ASMR videos from pomechan on The ASMR Index"


def vc_only(desc, *, watch=(), cards=()):
    html = fixture_page(desc, list(watch), video_card_titles=list(cards))
    o = TN.http
    TN.http = lambda *a, **k: Resp(html)
    try:
        return TN.verify_channel(FAKE, "pomechan")
    finally:
        TN.http = o


print("  only title='Watch ...' x12 tutorials (shape parsed -> reject):",
      vc_only(GOOD_DESC, watch=TUT25[:12]))
print("  only video-card__title x12 tutorials (shape parsed -> reject):",
      vc_only(GOOD_DESC, cards=TUT25[:12]))
print("  12 tutorials title + 12 ASMR video-card (video-card must count):",
      vc_only(GOOD_DESC, watch=TUT25[:12], cards=ASMR25[:12]))
print("  12 ASMR title + 12 tutorials video-card (title must count):",
      vc_only(GOOD_DESC, watch=ASMR25[:12], cards=TUT25[:12]))

print()
print("=== H: verdicts ===")
verdicts = {
    "A_not_confirmed": A["level"] != "confirmed" and not A["ids"],
    "B_not_confirmed": B["level"] != "confirmed" and not B["ids"],
    "C_not_confirmed": C["level"] != "confirmed" and not C["ids"],
    "D_confirmed_real": D["level"] == "confirmed" and D["ids"] == [REAL],
    "E1_P0_live": E1["level"] == "confirmed" and E1["ids"] == [FANCLUB],
    "E2_P0_live": E2["level"] == "confirmed" and E2["ids"] == [FAKE],
    "E3_P0_live": E3["level"] == "confirmed" and E3["ids"] == [FAKE],
    "E5_P0_live": E5["level"] == "confirmed" and E5["ids"] == [FAKE],
    "E7_secondary": E7["level"] == "confirmed" and E7["ids"] == [FAKE],
}
for k, v in verdicts.items():
    print(f"  {k}: {'PASS' if v else 'FAIL'}")
print()
print("ROUND5_MATRIX_DONE")
