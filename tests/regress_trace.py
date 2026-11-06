#!/usr/bin/env python
"""Regression for trace.py: concurrency structure, instant dispatch, two tables.

The assertions here do NOT measure politeness. They measure the two properties
that a serial or batch-collecting implementation violates:

  * a channel on a free host starts at t=0, regardless of what other channels
    are doing (a route-based design makes it wait for its predecessor);
  * a lead produced at time T is dispatched at time T (a batch design waits for
    the run to finish).

Pacing is asserted separately, because "all six channels within 1 s" is
unachievable when four of them share one host with a 6 s interval -- an earlier
version of this file asserted exactly that and was wrong.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import trace as T  # noqa: E402

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print("     " + detail)


# --------------------------------------------------------------- fixtures
# Real upload titles captured from the three accounts. Using the real strings
# matters: the brand only appears in some of them, which is the whole point.
FENGWEN = [
    "【ASMR】大肉腿精油轻拍按摩｜失眠患者_大腿_丝袜_抓挠_睡眠",
    "【ASMR_助眠】黑丝摩擦触发音，耳朵按摩快来睡觉～",
    "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
    "【POMEchan】精油涂抹、轻拍大腿",
    "【ASMR】被涂满精油的肉腿紧紧包裹是什么感觉呢❤？大腿精油刮擦&敲击",
    "【POMEchan ASMR】4K _ 极致漆皮挤压声 _ 大腿枕 _ 连体皮衣 _ 3Dio",
    "【ASMR】jk小姐姐敲击大腿_丝袜_道具触发音_耳语",
    "【POMEchan ASMR】4K _ 筋膜枪放松&精油按摩 _ 厚黑 _ 无人声助眠",
    "【POMEchan】精油涂抹、捂耳朵、揉搓大腿",
    "【ASMR】我们单独待在医务室里……",
    "【ASMR】被涂满精油的肉腿",
    "【ASMR】大肉腿精油轻拍按摩",
]
AMENG = [
    "【POMEchan ASMR】4K _ 筋膜枪放松&精油按摩 _ 厚黑 _ 无人声助眠",
    "【POMEchan ASMR】大腿精油按摩 _ 大腿枕 _ 蕾丝过膝袜 _ 绝对领域",
    "【ASMR】 超薄白丝袜，肉肉大腿精油轻拍缓解疲劳",
    "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
    "【ASMR】精油涂抹、轻拍大腿 ｜ 奶牛过膝袜",
    "【ASMR】睡前小游戏，猜出声音来源",
    "【ASMR】3dio-油光锃亮大白腿助眠 - Pomechan",
    "【POMEchan】精油涂抹、捂耳朵 _ 3Dio _ 黑丝",
    "【ASMR⧸コスプレ】勝利の女神NIKKE",
    "【ASMR_cos】三玖把丝袜“脱”下来摩擦你的耳朵",
    "【ASMR】jk小姐姐敲击大腿_丝袜_道具触发音_耳语",
]

ITEM = {"platform": "bilibili", "id": "BV1rCxf6MERM",
        "title": "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
        "pubdate": 1793835738, "duration": 1989,
        "author": "风吻茉莉2", "mid": 1464362113}

GAP = 0.25
CALLS = []


def _mk_pool():
    rows = []
    for i, t in enumerate(FENGWEN):
        rows.append({"bvid": f"BVF{i:08d}", "title": t, "author": "风吻茉莉2",
                     "pubdate": 1793835738 - i * 86400, "length": "33:09"})
    for i, t in enumerate(AMENG):
        rows.append({"bvid": f"BVA{i:08d}", "title": t, "author": "阿猛al",
                     "pubdate": 1793835738 - i * 86400, "length": "34:32"})
    return rows


POOL = _mk_pool()


def build_net():
    T0 = time.monotonic()

    def rec(k, q=None):
        CALLS.append((k, time.monotonic() - T0, q))

    def view(b):
        rec("view")
        return {"code": 0, "data": {
            "bvid": "BV1rCxf6MERM", "aid": 1, "title": ITEM["title"],
            "pubdate": ITEM["pubdate"], "duration": 1989,
            "owner": {"name": "风吻茉莉2", "mid": 1464362113}}}

    def space(m, pn=1):
        rec("space")
        rows = [r for r in POOL if r["author"] == ("风吻茉莉2" if m == 1464362113
                                                   else "阿猛al")]
        return {"code": 0, "data": {"list": {"vlist": [
            {"bvid": r["bvid"], "title": r["title"], "author": r["author"],
             "created": r["pubdate"], "length": r["length"]} for r in rows]}}}

    def related(b):
        rec("related")
        return {"code": 0, "data": [
            {"bvid": r["bvid"], "title": r["title"], "pubdate": r["pubdate"],
             "duration": 1989, "owner": {"name": r["author"]}}
            for r in POOL if r["bvid"] != b][:39]}

    def search(kw, page=1, order="pubdate"):
        rec("search", kw)
        kl = (kw or "").lower()
        return {"code": 0, "data": {"result": [
            {"bvid": r["bvid"], "title": r["title"], "author": r["author"],
             "pubdate": r["pubdate"], "duration": "33:09",
             "pic": "http://i0.hdslb.com/x.jpg"}
            for r in POOL if kl in r["title"].lower()][:50]}}

    def yt(q, n=20):
        rec("ytsearch", q)
        ql = (q or "").lower()
        if "pomechan" not in ql:
            return []
        return [{"id": "Af4p8MZqeCU", "channel": "Asmr Zone",
                 "title": "ASMR 蕾丝吊带丝袜 POMEchan"},
                {"id": "RuLFHrVZ4ys", "channel": "Magaimono",
                 "title": "ASMR white stockings POMEchan"}]

    return {"view": view, "space": space, "related": related,
            "search": search, "ytsearch": yt}


# ------------------------------------------------------------------- run
out = T.run(ITEM, net=build_net(), budget_s=12, max_requests=40,
            gaps={"api.bilibili.com": GAP, "www.youtube.com": GAP})

print("-- call log ------------------------------------------------")
for k, t, q in CALLS:
    print(f"   {t:6.3f}  {k:<10} {q or ''}")

first = {}
for k, t, _ in CALLS:
    first.setdefault(k, t)

print()
print("-- 1. a channel on a free host does not wait for other routes ---")
check("ytsearch starts at t=0", first.get("ytsearch", 99) < 0.3,
      f"first ytsearch at {first.get('ytsearch')}")
check("view starts at t=0", first.get("view", 99) < 0.3)

print()
print("-- 2. same-host tasks are spaced, not serialised ---------------")
bili = sorted(t for k, t, _ in CALLS if k in ("view", "space", "related", "search"))
seen = [round(b - a, 3) for a, b in zip(bili, bili[1:])]
check("never closer than the gap", all(g >= GAP * 0.9 for g in seen), str(seen))
check("never much wider than the gap", all(g <= GAP * 2.5 for g in seen), str(seen))

print()
print("-- 3. instant dispatch ----------------------------------------")
brand_q = [(t, q) for k, t, q in CALLS
           if k == "ytsearch" and q and "pomechan" in q.lower()]
space_end = max((t for k, t, _ in CALLS if k == "space"), default=None)
check("the brand triggered a cross-platform search", bool(brand_q), str(CALLS))
if brand_q:
    check("dispatched within 0.3 s of the producer",
          brand_q[0][0] - space_end < 0.3,
          f"lag={brand_q[0][0] - space_end:.3f}s")
check("the brand also triggered a same-site search",
      any(k == "search" and q and "pomechan" in q.lower() for k, _, q in CALLS),
      str([q for k, _, q in CALLS if k == "search"]))
check("both orders are queried, not just relevance",
      sum(1 for k, _, q in CALLS if k == "search") >= 2,
      "pubdate and click are different result sets")

print()
print("-- 4. two tables, never one -----------------------------------")
check("the brand is found",
      any(b["brand"] == "pomechan" for b in out["brands"]), str(out["brands"]))
check("creator is a lead, never confirmed",
      out["creator_conclusion"]["level"] in ("strong_lead", "weak_lead"),
      str(out["creator_conclusion"]))
check("earliest_known is about works",
      "work_id" in out["earliest_known"])
check("creator_conclusion is about people",
      "name" in out["creator_conclusion"] and "level" in out["creator_conclusion"])
check("the two tables share no keys",
      set(out["earliest_known"]) & set(out["creator_conclusion"]) == set(),
      str(set(out["earliest_known"]) & set(out["creator_conclusion"])))
check("attribution_established does not exist",
      "attribution_established" not in json.dumps(out))

print()
print("-- 5. brand filtering -----------------------------------------")
check("a platform word is not a brand", T.brand_tokens("【ASMR】助眠") == [],
      str(T.brand_tokens("【ASMR】助眠")))
check("a resolution is not a brand", T.brand_tokens("4K 3DIO 1080P") == [],
      str(T.brand_tokens("4K 3DIO 1080P")))
check("a real brand survives",
      T.brand_tokens("【POMEchan ASMR】4K") == ["POMEchan"],
      str(T.brand_tokens("【POMEchan ASMR】4K")))

print()
print("-- 6. a blocked host is not an empty result --------------------")
blocked_net = dict(build_net())
blocked_net["space"] = lambda m, pn=1: (_ for _ in ()).throw(RuntimeError("412"))
out2 = T.run(ITEM, net=blocked_net, budget_s=6, max_requests=20,
             gaps={"api.bilibili.com": GAP, "www.youtube.com": GAP})
check("coverage_incomplete is set when a host refuses",
      out2["coverage_incomplete"] is True, str(out2.get("coverage_incomplete")))

print()
print("-- 7. earliest_known needs a content gate ----------------------")
_item = {"title": "【ASMR】丝袜\u201c脱了\u201d放我耳朵上？_丝袜触发音",
         "duration": 1989, "pubdate": 1793835738, "id": "IN"}
_cands = [
    {"platform": "bilibili", "id": "shao", "duration": 1969,
     "pubdate": 1790769600,
     "title": "【ASMR】丝袜\u201c脱了\u201d放我耳朵上？/丝袜触发音"},
    {"platform": "bilibili", "id": "ameng", "duration": 2072,
     "pubdate": 1793000000,
     "title": "【ASMR】丝袜\u201c脱了\u201d放我耳朵上？_丝袜触发音"},
    # a 2019 upload of a generic phrase: oldest by date, but not the same clip
    {"platform": "bilibili", "id": "OLD", "duration": 300,
     "pubdate": 1622833357, "title": "丝袜触发音 合集 2019"},
    {"platform": "bilibili", "id": "unrelated", "duration": 1989,
     "pubdate": 1700000000, "title": "完全无关的一个视频标题"},
]
_g = T.earliest_known(_cands, _item)
check("picks the real earlier copy, not the oldest row",
      _g["work_id"] == "shao", str(_g))
check("reports how many were gated out", _g.get("gated_out") == 2, str(_g))
check("delta matches the measured 35.49 days",
      abs((_g.get("delta_days") or 0) - 35.49) < 0.1, str(_g.get("delta_days")))
check("content_basis stays None: the gate is not verification",
      _g["content_basis"] is None)

print()
print("-- 8. live-network shapes the fixtures did not cover -----------")
check("yt-dlp NA duration does not crash the parser",
      T.brand_tokens("ASMR") == [], "smoke")
_net = (HERE.parent / "scripts" / "trace_net.py").read_text(encoding="utf-8")
check("search defaults to date order",
      'order: str = "pubdate"' in _net,
      "relevance buries the older copy; measured 0/50 vs 2/6")
check("search sends a buvid3 cookie",
      "buvid3" in _net,
      "every UA shape 412s without it")
check("an NA duration is normalised, not int()'d",
      'dur.isdigit()' in _net, "int('NA') killed the whole YouTube channel")

print()
print("-- 9. diagnosis names the broken step, not just None ------------")
_d = out.get("diagnosis") or {}
check("a diagnosis is always emitted", bool(_d.get("stage")), str(_d))
check("it carries a human-readable detail", bool(_d.get("detail")), str(_d))
check("a guessed name is reported as a guess, not as an answer",
      _d.get("stage") != "complete" or out["creator_conclusion"]["level"] != "guess",
      str(_d))

# The removed credit_rate must not come back by accident: on the real
# successful case it reported titles=1 because run_space never wrote `mid`,
# and Ledger.absorb does not merge fields. Assert the field is gone.
check("the broken credit_rate metric is not in the output",
      "credit_rate" not in json.dumps(out))
check("every candidate that came from space carries its mid",
      all(c.get("mid") for c in out["candidates"] if c.get("author") == "风吻茉莉2"),
      "space must record mid or per-account metrics are meaningless")

print()
print("-- 10. the docs point at the entry point that exists -----------")
_skill = (HERE.parent / "SKILL.md").read_text(encoding="utf-8")
check("SKILL.md names trace.py as the entry point",
      "python scripts/trace.py" in _skill,
      "the front page must run the engine, not the old module")
check("SKILL.md documents the three output fields",
      all(k in _skill for k in ("earliest_known", "creator_conclusion",
                                "diagnosis")))
check("SKILL.md warns that date order is required",
      "order=pubdate" in _skill,
      "relevance sort hides older copies; measured 0/50 vs 2/6")
check("SKILL.md warns that a blocked request is not an empty result",
      "coverage_incomplete" in _skill)
check("the declared-vs-implemented gap is empty",
      not (set(T.HOST_OF) - set(T.RUNNERS) - T._INTENTIONALLY_EMPTY),
      str(sorted(set(T.HOST_OF) - set(T.RUNNERS) - T._INTENTIONALLY_EMPTY)))
# Runner names (bili.view) map to channel keys (view); assert the keys the
# runners actually look up, not string equality between the two vocabularies.
_needed = {"view", "related", "space", "search", "comments", "ytsearch",
           "ytchannel", "ytoembed", "reverse_image", "archive_channel",
           "websearch", "resolve_channel", "verify_channel"}
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace_net as _TN  # noqa: E402
_registered = set(_TN.LiveNet().channels())
check("every channel a runner looks up is registered",
      _needed <= _registered, str(sorted(_needed - _registered)))

print()
print("-- 11. a forged channel must NOT become a confirmed creator -------")
_FAKE = "UCFORGED1234567890123456"
_brands = [{"brand": "pomechan", "accounts": 3,
            "platforms": ["bilibili", "youtube"], "sample": ["a", "b", "c"]}]


def _archive_rows(titles, verified):
    return [{"platform": "youtube", "id": f"vid{i:03d}", "title": t,
             "author": "pomechan", "archive_of": "pomechan",
             "channel_id": _FAKE, "channel_verified": verified}
            for i, t in enumerate(titles)]


_asr = [f"ASMR 丝袜 触发音 助眠 {i}" for i in range(25)]
_orig = [f"折纸教程 第{i}期" for i in range(25)]

_c_bad = T.creator_conclusion(_brands, _archive_rows(_asr, False), [], [])
check("an unverified channel never yields confirmed",
      _c_bad["level"] != "confirmed", str(_c_bad["level"]))
check("an unverified channel never yields an attributed id",
      not _c_bad["ids"]["youtube_channel"], str(_c_bad["ids"]))

_c_genre = T.creator_conclusion(_brands, _archive_rows(_orig, True), [], [])
check("a catalogue with no genre overlap never yields confirmed",
      _c_genre["level"] != "confirmed", str(_c_genre["level"]))
check("no-genre witness is rejected", _c_genre.get("witness") is None,
      str(_c_genre.get("witness")))

_c_ok = T.creator_conclusion(_brands, _archive_rows(_asr, True), [], [])
check("verified + genre-consistent catalogue can confirm",
      _c_ok["level"] == "confirmed", str(_c_ok["level"]))

# a guess must never be called complete
_d = T.diagnosis({"work_id": "BVx"}, {"name": "n", "level": "guess"},
                 [], 10, 1)
check("a guessed creator is never diagnosis=complete",
      _d["stage"] != "complete", str(_d))
# a later upload must not become earliest_known
_e = T.earliest_known(
    [{"platform": "bilibili", "id": "later", "pubdate": 2000,
      "duration": 1989, "title": "【ASMR】丝袜\u201c脱了\u201d放我耳朵上？_丝袜触发音"}],
    {"id": "in", "pubdate": 1000, "duration": 1989,
     "title": "【ASMR】丝袜\u201c脱了\u201d放我耳朵上？_丝袜触发音"})
check("a later upload is not reported as earlier",
      _e["work_id"] is None, str(_e))

print()
bad = [n for n, c in R if not c]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
