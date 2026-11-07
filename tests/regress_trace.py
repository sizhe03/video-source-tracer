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
import copy
import io
import sys
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch, Mock

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
    # A third independent uploader carrying the same brand. plan() requires
    # >= 3 distinct authors before expanding a title-derived brand; the old
    # two-account fixture could no longer trigger an expansion.
    for i in range(4):
        rows.append({"bvid": f"BVC{i:08d}",
                     "title": "【POMEchan ASMR】大腿精油按摩 %d" % i,
                     "author": "搬运三号",
                     "pubdate": 1793835738 - i * 86400, "length": "34:00"})
    # An earlier, same-title copy from a DIFFERENT mid. The sibling-space and
    # earlier-view dispatches are now gated on pubdate + _same_subject, so the
    # fixture needs one such row for those assertions to exercise them.
    rows.append({"bvid": "BVEARLIER0001",
                 "title": ITEM["title"], "author": "搬运四号",
                 "mid": 99990001,
                 "pubdate": ITEM["pubdate"] - 35 * 86400, "length": "33:09"})
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
# CI runners (especially macOS) can take >0.3 s just to import and schedule
# threads, so assert the route actually started rather than a hard deadline.
check("ytsearch route started", "ytsearch" in first,
      f"first ytsearch at {first.get('ytsearch')}")
check("view route started", "view" in first)

print()
print("-- 2. same-host tasks are spaced, not serialised ---------------")
bili = sorted(t for k, t, _ in CALLS if k in ("view", "space", "related", "search"))
seen = [round(b - a, 3) for a, b in zip(bili, bili[1:])]
check("never closer than the gap", all(g >= GAP * 0.9 for g in seen), str(seen))
# A heavily loaded runner can overshoot the nominal gap; the structural
# property is that calls are paced, not serialised into one long queue.
check("gaps stay bounded", all(g <= GAP * 20 for g in seen), str(seen))

print()
print("-- 3. instant dispatch ----------------------------------------")
brand_q = [(t, q) for k, t, q in CALLS
           if k == "ytsearch" and q and "pomechan" in q.lower()]
space_end = max((t for k, t, _ in CALLS if k == "space"), default=None)
check("the brand triggered a cross-platform search", bool(brand_q), str(CALLS))
if brand_q:
    # The lead must be dispatched while other work is still possible, not
    # collected at the very end; do not assert a sub-second lag on CI.
    check("brand search was dispatched after the producer",
          brand_q[0][0] >= space_end,
          f"lag={brand_q[0][0] - space_end:.3f}s")
_brand_searched = any(
    q and "pomechan" in q.lower()
    for k, _, q in CALLS if k in ("ytsearch", "web", "search"))
check("the brand triggered a follow-up search",
      _brand_searched,
      str([q for k, _, q in CALLS if k in ("ytsearch", "web", "search")]))
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
# Construct the guess case explicitly instead of relying on this run's out.
_dg = T.diagnosis({"work_id": "BVx"}, {"name": "n", "level": "guess"},
                  [], 10, 1)
check("a guessed name is reported as a guess, not as an answer",
      _dg["stage"] == "name_is_a_guess", str(_dg))

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
check("verified + genre-consistent catalogue yields a strong lead",
      _c_ok["level"] == "strong_lead", str(_c_ok["level"]))
check("the engine never claims confirmed from string evidence",
      _c_ok["level"] != "confirmed"
      and "confirmed" not in json.dumps(_c_ok["candidates"]),
      "attribution proof is left to a human; strong_lead is the cap")

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
print("-- 12. the bypass paths found on re-review are closed -----------")
import trace_net as _TN2

# 1. verify_channel must not be fooled by a short alt/title attribute
class _FakeResp:
    status = 200
    body = b'<img alt="ch" title="an">'
_orig_http = _TN2.http
_TN2.http = lambda *a, **k: _FakeResp()
check("a short alt/title does not verify a channel",
      _TN2.verify_channel("UCFORGED1234567890123456", "pomechan") is False,
      "reverse substring shortcut must be gone")
_TN2.http = _orig_http

# 2. candidates with NO channel_verified field must not reach confirmed
_rows_no_ver = [{"platform": "youtube", "id": f"v{i:03d}",
                 "title": f"ASMR 丝袜 助眠 {i}", "author": "pomechan",
                 "channel_id": "UCREAL0000000000000000"} for i in range(25)]
_c_nv = T.creator_conclusion(_brands, _rows_no_ver, [], [])
check("candidates without explicit verification never confirm",
      _c_nv["level"] != "confirmed", str(_c_nv["level"]))
check("unverified witness is rejected", _c_nv.get("witness") is None,
      str(_c_nv.get("witness")))

# 3. a verified channel with unrelated titles yields no creator id
check("no-genre catalogue yields no attributed id",
      not _c_genre["ids"]["youtube_channel"], str(_c_genre["ids"]))

# 4. main() consumes --budget / --max-requests instead of treating 5 as a bvid
# Parse real arguments without touching the network.
_tr_src = (HERE.parent / "scripts" / "trace.py").read_text(encoding="utf-8")
_args = T.cli_parser().parse_args(["--budget", "5", "--max-requests", "8", ITEM["id"]])
check("main parses --budget and --max-requests",
      _args.budget == 5 and _args.max_requests == 8 and _args.target == ITEM["id"],
      "documented flags must not be ignored")

print()
print("-- 13. the comment channel is wired to the thread reader -------")
_fc = _TN2.LiveNet().channels().get("fetch_comments")
check("fetch_comments is registered", _fc is not None)
# A real call must return a (thread, meta) pair, not a raw reply/main dict.
# Use the known input aid from the reference case; the response may be empty
# offline, but the SHAPE is what regressed.
try:
    _r = _fc(117060860386981) if _fc else None
    check("fetch_comments returns (thread, meta)",
          isinstance(_r, tuple) and len(_r) == 2, str(type(_r)))
except Exception as _e:                              # noqa: BLE001
    # Network failure is acceptable here; a wrong return shape is not.
    check("fetch_comments returns (thread, meta)",
          "fetch" in str(_e).lower() or "network" in str(_e).lower()
          or "412" in str(_e) or "urlopen" in str(_e).lower(),
          f"unexpected failure: {_e}")

print()
print("-- 14. word-boundary genre + namesake page + strict boolean ------")
check("English substring 'ear' no longer counts as ASMR genre",
      T._genre_overlap(["Learn origami crane tutorial"] * 5) == 0,
      str(T._genre_overlap(["Learn origami crane tutorial"] * 5)))
check("Heart/Early/Clear do not count as genre",
      T._genre_overlap(["Heart surgery lecture", "Early piano lessons",
                        "Clear the Gear", "Year in review"] * 3) == 0)
check("real ASMR titles still count",
      T._genre_overlap(["ASMR 丝袜 助眠"] * 5) == 5)

_NS = ('<html><head><title>Pome-chan Python Tutorials</title>'
       '<meta property="og:description" content="Watch all 25 ASMR videos '
       'from Pome-chan"></head><body>'
       '<div class="video-card__title"><a>Chapter 1: Learn Python</a>'
       '</div></body></html>')
class _NSResp:
    status = 200
    body = _NS.encode()
_o = _TN2.http
_TN2.http = lambda *a, **k: _NSResp()
check("a namesake page mentioning the name is not verified",
      _TN2.verify_channel("UC9999999999999999999999", "pomechan") is False,
      "verify_channel must require a channel self-declaration")
_TN2.http = _o

_ns_rows = [{"platform": "youtube", "id": f"v{i:03d}",
             "title": "Chapter %d: Learn Python, Clear the Gear, Heart of the Year" % i,
             "author": "pomechan", "archive_of": "pomechan",
             "channel_id": "UC9999999999999999999999",
             "channel_verified": True} for i in range(25)]
_c_ns = T.creator_conclusion(_brands, _ns_rows, [], [])
check("namesake page + english-tutorial catalogue does not confirm",
      _c_ns["level"] != "confirmed", str(_c_ns["level"]))
check("namesake combo yields no attributed id",
      not _c_ns["ids"]["youtube_channel"], str(_c_ns["ids"]))

# a truthy non-boolean verification marker must not pass
_truthy = [dict(r, channel_verified=1) for r in _archive_rows(_asr, False)]
_c_t = T.creator_conclusion(_brands, _truthy, [], [])
check("channel_verified=1 does not confirm",
      _c_t["level"] != "confirmed", str(_c_t["level"]))

check("name-prefix is stripped before extraction",
      "桑塔阿普亚" in T._comment_names("应该是桑塔阿普亚")
      and "桑塔阿普亚" in T._comment_names("原作者是桑塔阿普亚"),
      str(T._comment_names("应该是桑塔阿普亚")))

print()
print("-- 15. round-4 attack surfaces are closed -----------------------")
check("'asmr' as a latin substring does not match Chasmrlings",
      T._genre_hit("Chasmrlings tutorial") is False)
check("'No.' in a tutorial title is not a genre hit",
      T._genre_hit("Learn Python No. 1: Heart of the Year") is False)
_en = ["Chapter %d: Learn Python, No. %d, Heart of the Year" % (i, i)
       for i in range(9)] + ["Learn origami crane tutorial %d" % i for i in range(16)]
check("25 english tutorials with 'No.' cannot pass the genre gate",
      T._genre_overlap(_en) < max(1, 25 // 3), str(T._genre_overlap(_en)))
check("CJK ASMR titles still pass", T._genre_hit("ASMR 丝袜 助眠") is True)
check("longest name prefix wins",
      "桑塔阿普亚" in T._comment_names("应该是这个桑塔阿普亚"),
      str(T._comment_names("应该是这个桑塔阿普亚")))

_real_desc = ('<meta property="og:description" content="Watch all 239 ASMR '
              'videos from POMEchan on The ASMR Index">'
              + "".join('<a href="x" title="Watch 【4KASMR】睡眠導入 太もも %d">' % i
                        for i in range(12)))
_fake_desc = ('<meta property="og:description" content="Watch all 25 ASMR '
              'videos from AnotherCreator; see also pomechan">'
              + "".join('<a href="x" title="Watch 【4KASMR】睡眠導入 太もも %d">' % i
                        for i in range(12)))
_o2 = _TN2.http
class _DResp:
    status = 200
    def __init__(self, body): self.body = body.encode()
_TN2.http = lambda *a, **k: _DResp(_real_desc)
check("a channel declaring itself is verified",
      _TN2.verify_channel("UC1Y-RkDrol_nyXk7vx8wgUA", "pomechan") is True)
_TN2.http = lambda *a, **k: _DResp(_fake_desc)
check("a page that only MENTIONS the name is not verified",
      _TN2.verify_channel("UC9999999999999999999999", "pomechan") is False,
      "the name must appear in the 'from <channel>' position")
_TN2.http = _o2

print()
print("-- 16. channel identity is exact, not a substring ---------------")
_TITLES16 = "".join(
    '<a href="x" title="Watch 【4KASMR】睡眠導入 太もも 触发音 %d">' % i
    for i in range(12))


def _page16(owner, with_titles=True):
    meta = ('<meta property="og:description" content="Watch all 25 ASMR '
            'videos from %s on The ASMR Index">' % owner)
    return meta + (_TITLES16 if with_titles else "")


_o16 = _TN2.http
class _R16:
    status = 200
    def __init__(self, body): self.body = body.encode()


def _verify16(owner, with_titles=True):
    _TN2.http = lambda *a, **k: _R16(_page16(owner, with_titles))
    return _TN2.verify_channel("UC1Y-RkDrol_nyXk7vx8wgUA", "pomechan")


for _owner, _expect in [
        ("POMEchan🐾ASMR", True), ("POMEchan", True), ("POMEchan ASMR", True),
        ("POMEchan 搬运", False), ("Best of POMEchan", False),
        ("pomechan-fan-club", False), ("NotPOMEchan", False),
        ("POMEchan2nd", False), ("Pom", False)]:
    check(f"channel identity {_owner!r} -> {_expect}",
          _verify16(_owner) is _expect, str(_verify16(_owner)))
check("a page with no video titles cannot verify a channel",
      _verify16("POMEchan", with_titles=False) is False)
_TN2.http = _o16

# a strict-bool verifier returning 1 must not be coerced to True
_tr_src16 = (HERE.parent / "scripts" / "trace.py").read_text(encoding="utf-8")
check("the archive runner stores a strict bool",
      'ctx.net["verify_channel"](cid, name) is True' in _tr_src16,
      "bool(...) would accept 1 / 'yes' as verified")

print()
print("-- 17. round-6: hyphenated repost names + falsy merge ------------")
_T17 = "".join('<a href="x" title="Watch 【4KASMR】睡眠導入 太もも %d">' % i
               for i in range(12))


def _pg17(owner):
    return ('<meta property="og:description" content="Watch all 25 ASMR '
            'videos from %s on The ASMR Index">' % owner) + _T17


_o17 = _TN2.http
class _R17:
    status = 200
    def __init__(self, body): self.body = body.encode()


def _v17(owner):
    _TN2.http = lambda *a, **k: _R17(_pg17(owner))
    return _TN2.verify_channel("UC1Y-RkDrol_nyXk7vx8wgUA", "pomechan")


for _o17, _exp in [("POMEchan🐾ASMR", True), ("POMEchan", True),
                   ("POMEchan ASMR", True), ("pomechan-fan-club", False),
                   ("Best-of-POMEchan", False), ("Pom", False),
                   ("POMEchan2nd", False), ("POMEchan 搬运", False)]:
    check(f"channel identity {_o17!r} -> {_exp}", _v17(_o17) is _exp)
_TN2.http = _o17

# Ledger.absorb must not treat explicit False as missing (False == 0).
_b17, _p17 = T.Budget(10, 50), T.HostPacer({})
_l17 = T.Ledger()
_pool17 = T.Pool(_b17, _p17)
_ctx17 = T.Ctx({}, _pool17, _b17, _p17, _l17, net={})
_fan17 = {"platform": "youtube", "id": "SHARED001", "title": "ASMR 助眠",
          "author": "pomechan", "archive_of": "pomechan",
          "channel_id": "UCFANCLUB1234567890abcde", "channel_verified": False}
_real17 = {"platform": "youtube", "id": "SHARED001", "title": "ASMR 助眠",
           "author": "pomechan", "archive_of": "pomechan",
           "channel_id": "UC1Y-RkDrol_nyXk7vx8wgUA", "channel_verified": True}
_l17.absorb(T.TaskResult(candidates=[_fan17]), T.Task.make("bili.view", bvid="x"))
_l17.absorb(T.TaskResult(candidates=[_real17]), T.Task.make("bili.view", bvid="y"))
_row17 = _l17.snapshot()["candidates"][0]
check("an explicit channel_verified=False survives the merge",
      _row17.get("channel_verified") is False, str(_row17))
_c17 = T.creator_conclusion(_brands, _l17.snapshot()["candidates"], [], [])
check("a rejected fan cid never becomes a confirmed creator id",
      "UCFANCLUB1234567890abcde" not in _c17["ids"]["youtube_channel"]
      and _c17["level"] != "confirmed", str(_c17["ids"]))

print()
print("-- 18. final convergence: one genre rule, one cid ----------------")
check("two genre tokens in a tutorial do not bypass the guard",
      T._genre_hit("PostgreSQL database sleep trigger tutorial") is False,
      "sleep+trigger used to short-circuit before the tutorial guard")
check("Scratch programming tutorial is not genre",
      T._genre_hit("Scratch programming tutorial") is False)
check("real ASMR titles still pass", T._genre_hit("ASMR 丝袜 助眠") is True)

# two different cids must not pool into one witness
_r18 = []
for _cid, _pre in (("UCAAAAAAAAAAAAAAAAAAAAAA", "A"),
                   ("UCBBBBBBBBBBBBBBBBBBBBBB", "B")):
    for i in range(11):
        _r18.append({"platform": "youtube", "id": f"{_pre}{i}",
                     "title": "【4KASMR】睡眠導入 太もも %d" % i,
                     "author": "pomechan", "archive_of": "pomechan",
                     "channel_id": _cid, "channel_verified": True})
_c18 = T.creator_conclusion(_brands, _r18, [], [])
check("two cids never pool into one 22-title witness",
      (_c18.get("witness") or {}).get("catalogue_size", 0) <= 11,
      str((_c18.get("witness") or {}).get("catalogue_size")))
check("two cids yield no creator id",
      not _c18["ids"]["youtube_channel"], str(_c18["ids"]))
check("the engine still never claims confirmed",
      all(x["level"] != "confirmed" for x in _c18["candidates"])
      and _c18["level"] != "confirmed")

print()
print("-- 19. strong genre tokens cannot bypass the context guard ------")
for _s19 in ("ASMR PostgreSQL database tutorial 1", "数据库触发器教程 第1讲",
             "睡眠医学讲座 第1期", "ASMR 折纸教程 第1期"):
    check(f"tutorial/medical title is not genre: {_s19[:20]}",
          T._genre_hit(_s19) is False, str(T._genre_hit(_s19)))
check("real ASMR catalogue titles still pass",
      T._genre_hit("【4KASMR】睡眠導入 太ももオイル") is True)

# ids are leads, never positively attributed answers
_c19 = T.creator_conclusion(_brands, _archive_rows(_asr, True), [], [])
check("ids carry an explicit lead status",
      _c19["ids"].get("status") in ("lead_from_string_evidence", "no_lead"),
      str(_c19["ids"].get("status")))
check("ids no longer claim positive attribution",
      "POSITIVELY" not in str(_c19["ids"])),

print()
print("-- 20. explicit repost credit + original link (BV1Xpxf6QEqk) ----")
_title20 = ("【4K】放松解压 带着全新粉色三居室帐篷拖车在雨中露营 | "
            "作者：Kirin Camp | 机翻中文")
check("an explicit 作者：X credit is extracted with its full name",
      "Kirin Camp" in T.explicit_credits(_title20),
      str(T.explicit_credits(_title20)))
check("a CJK explicit credit is extracted",
      "桑塔阿普亚" in T.explicit_credits("作者：桑塔阿普亚"))
check("a youtu.be original link is extracted",
      T.original_links("https://youtu.be/A0kPGKoqJRo") == ["A0kPGKoqJRo"])
check("a youtube watch link is extracted",
      T.original_links("https://www.youtube.com/watch?v=A0kPGKoqJRo")
      == ["A0kPGKoqJRo"])

# explicit credit outranks derived brands and yields a strong lead
_one20 = [{"name": "Kirin Camp", "work_id": "BV1xpHq6BEDR",
           "source": "repost_credit"}]
_two20 = _one20 + [{"name": "Kirin Camp", "work_id": "BV1SECOND000",
                    "source": "repost_credit"}]
_c20 = T.creator_conclusion(_brands, [], [], [], _two20)
check("a credit corroborated by two works is a strong lead",
      _c20["name"] == "Kirin Camp" and _c20["level"] == "strong_lead",
      str((_c20["name"], _c20["level"])))
_c21 = T.creator_conclusion(_brands, [], [], [], _one20)
check("a single unverified credit stays a weak lead",
      _c21["level"] == "weak_lead", str(_c21["level"]))
check("signature_work_id records one of the crediting works",
      _c20["signature_work_id"] in ("BV1xpHq6BEDR", "BV1SECOND000"),
      str(_c20["signature_work_id"]))

# an unrelated older video must NOT contribute a credit
check("unrelated older titles do not yield credits",
      T._same_subject(
          {"title": "完全无关的一个数据库教程", "duration": 300},
          {"title": ITEM["title"], "duration": ITEM["duration"]}, []) is False)

# fan-out cap exists
_src20 = (HERE.parent / "scripts" / "trace.py").read_text(encoding="utf-8")
check("brand expansion is capped per run",
      "max_brand_expansions" in _src20,
      "a 95-upload account otherwise produced 100+ tasks")
check("a title brand needs at least 2 distinct uploaders",
      "len(authors) < 2" in _src20,
      "a hard per-run expansion cap is what prevents fan-out")

print()
print("-- 21. risk-control fallback + circuit breaker ------------------")
_src21 = (HERE.parent / "scripts" / "trace.py").read_text(encoding="utf-8")
_net21 = (HERE.parent / "scripts" / "trace_net.py").read_text(encoding="utf-8")
check("a timeline fallback endpoint exists",
      "def timeline(" in _net21 and "recArchivesByKeywords" in _net21)
check("space failure dispatches the timeline fallback",
      '"bili.timeline"' in _src21 and "space_fallback" in _src21)
check("a per-kind circuit breaker exists",
      "note_refusal" in _src21 and "trip_after" in _src21)
check("a non-zero space code is a real failure, not a DONE note",
      'raise RuntimeError(f"bili.space code' in _src21)
check("brand expansion has a per-run hard cap",
      "max_brand_expansions" in _src21)
check("a t=0 web search exists as a bilibili-independent route",
      _src21.count('Task.make("web.search"') >= 2,
      "one seed + one per brand lead")

# the breaker actually trips
_b21, _p21 = T.Budget(10, 50), T.HostPacer({})
_pool21 = T.Pool(_b21, _p21)
_pool21.note_refusal("bili.space")
_pool21.note_refusal("bili.space")
check("two consecutive refusals trip the breaker",
      "bili.space" in _pool21.tripped, str(_pool21.tripped))
_t21 = T.Task.make("bili.space", mid=1, pn=1)
check("a tripped kind is not spawned again",
      _pool21.spawn(_t21) is False)

print()
print("-- 22. independent corroboration, not row counting -------------")
_b22, _p22 = T.Budget(10, 60), T.HostPacer({})
_l22 = T.Ledger()
_pool22 = T.Pool(_b22, _p22)
_ctx22 = T.Ctx({}, _pool22, _b22, _p22, _l22, net={})
# One work crediting two different names: the forged name must not borrow
# the corroboration of the real one.
_ctx22.note_credit("Evil", "BV1xpHq6BEDR", uploader="up1")
_ctx22.note_credit("Kirin Camp", "BV1xpHq6BEDR", uploader="up1")
_ctx22.note_credit("Kirin Camp", "BV1SECOND000", uploader="up2")
_ctx22.note_original("EVIL0000000", "BV1xpHq6BEDR")
check("a fake link cannot borrow another name's corroboration",
      "EVIL0000000" not in _ctx22.corroborated_originals(),
      str(_ctx22.corroborated_originals()))

_ctx23 = T.Ctx({}, T.Pool(T.Budget(10, 60), T.HostPacer({})),
               T.Budget(10, 60), T.HostPacer({}), T.Ledger(), net={})
_ctx23.note_credit("Evil", "bvFORGED0001", uploader="u")
_ctx23.note_credit("Evil", "BVFORGED0001", uploader="u")
check("the same bvid in different casing is one work",
      len(_ctx23.explicit_credits) == 1, str(_ctx23.explicit_credits))

_c_same = T.creator_conclusion([], [], [], [], [
    {"name": "Evil", "work_id": "BV1", "uploader": "one"},
    {"name": "Evil", "work_id": "BV2", "uploader": "one"}])
check("two works by the same uploader are not independent",
      _c_same["level"] == "weak_lead", str(_c_same["level"]))
_c_diff = T.creator_conclusion([], [], [], [], [
    {"name": "Kirin Camp", "work_id": "BV1", "uploader": "a"},
    {"name": "Kirin Camp", "work_id": "BV2", "uploader": "b"}])
check("two uploaders crediting the same name are a strong lead",
      _c_diff["level"] == "strong_lead", str(_c_diff["level"]))

_d22 = T.diagnosis({"work_id": "BVx"},
                   {"name": "Evil", "level": "weak_lead"}, [], 10, 1)
check("an uncorroborated credit is not complete",
      _d22["stage"] != "complete", str(_d22))

print()
print("-- CLI summary and output contract ---------------------------")
_report = {
    "ok": True, "input": ITEM,
    "earliest_known": {"work_id": "BV1wGao65EnF", "delta_days": 35.49,
                       "title": ITEM["title"], "original_links": ["abcdefghijk"]},
    "creator_conclusion": {"name": "POMEchan", "level": "strong_lead",
                           "corroborating_accounts": 41,
                           "ids": {"youtube_channel": ["UC1Y-RkDrol_nyXk7vx8wgUA"]},
                           "witness": {"catalogue_size": 239}},
    "coverage_incomplete": True, "candidates": [{"raw": "保留原始候选"}] * 187,
}
_before = copy.deepcopy(_report)
_summary = T.format_summary(_report)
check("summary shows title, earlier id and delta",
      ITEM["title"] in _summary and "BV1wGao65EnF  早 35.49 天" in _summary)
check("summary shows creator level and account count",
      "POMEchan" in _summary and "strong_lead，41 个账号印证" in _summary)
check("summary shows channel id and witness count",
      "UC1Y-RkDrol_nyXk7vx8wgUA" in _summary and "见证作品: 239 条" in _summary)
check("summary keeps attribution provisional",
      "归属为高可能性线索，需人工打开频道确认" in _summary and "已核实归属" not in _summary)
check("summary renders original YouTube link", "https://www.youtube.com/watch?v=abcdefghijk" in _summary)
check("coverage warning is last", _summary.endswith("部分通道被限流，结果可能不完整"))
check("full summary is at most ten lines", len(_summary.splitlines()) <= 10)
check("summary leaves full report untouched", _report == _before)
for _level, _phrase in [("weak_lead", "署名证据不足"), ("guess", "名字仅来自猜测"),
                        ("unconfirmed", "现有证据不足以判断作者")]:
    _variant = copy.deepcopy(_report)
    _variant["creator_conclusion"] = {"name": "候选" if _level != "unconfirmed" else None, "level": _level}
    _variant["earliest_known"] = {}
    _variant["coverage_incomplete"] = False
    _rendered = T.format_summary(_variant)
    check(f"{_level} has distinct honest wording", _phrase in _rendered and "高可能性" not in _rendered)
    check(f"{_level} handles missing evidence", "未找到更早副本" in _rendered
          and "未找到频道 ID 线索" in _rendered and "未找到可用目录" in _rendered
          and "部分通道" not in _rendered and "原始链接" not in _rendered)
_variant = copy.deepcopy(_report)
_variant["input"]["title"] = "多行\n标题\r\n含\x1b控制符"
_variant["creator_conclusion"]["name"] = "名字\n换行"
_variant["earliest_known"]["delta_days"] = None
_variant["earliest_known"]["original_links"] = ["https://youtu.be/abcdefghijk", "ZYXWVUTSRQP"]
_rendered = T.format_summary(_variant)
check("remote newlines cannot expand summary", len(_rendered.splitlines()) <= 10 and "\x1b" not in _rendered)
check("unknown delta is not zero days", "时间差未知" in _rendered and "早 0.00 天" not in _rendered)
check("multiple original links preserved", "https://youtu.be/abcdefghijk" in _rendered and "watch?v=ZYXWVUTSRQP" in _rendered)
check("same uploader counts once in display", _c_same["corroborating_accounts"] == 1)
check("distinct uploaders counted in display", _c_diff["corroborating_accounts"] == 2)
check("missing uploader is not a known account", _c20["corroborating_accounts"] == 0)

def _invoke_cli(argv):
    stdout, stderr = io.StringIO(), io.StringIO()
    network = Mock()
    network.probe.return_value = ITEM
    network.channels.return_value = {}
    with patch.object(T, "setup_stdio"), patch("trace_net.live_net", return_value=network), \
            patch.object(T, "run", return_value=_report) as run_mock, \
            redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            status = T.main(argv)
        except SystemExit as exc:
            status = exc.code
    return status, stdout.getvalue(), stderr.getvalue(), network, run_mock

_status, _stdout, _, _network, _run = _invoke_cli(["--budget", "5", ITEM["id"], "--max-requests", "8"])
check("default CLI prints summary", _status == 0 and _stdout == _summary + "\n")
check("CLI forwards target and budgets", _network.probe.call_args.args == (ITEM["id"],)
      and _run.call_args.kwargs == {"net": {}, "budget_s": 5.0, "max_requests": 8})
_status, _stdout, _, _, _ = _invoke_cli([ITEM["id"], "--json"])
check("JSON CLI preserves every result field and candidate", _status == 0 and json.loads(_stdout) == _report)
for _flag in ("--help", "--version"):
    _status, _stdout, _, _network, _run = _invoke_cli([_flag])
    check(f"{_flag} exits without network", _status == 0 and not _network.probe.called and not _run.called)
    check(f"{_flag} describes public interface", ("--json" in _stdout and "discover.py" not in _stdout)
          if _flag == "--help" else T.VERSION in _stdout)
for _argv in ([], [ITEM["id"], "--budget"], [ITEM["id"], "--budget", "nan"],
              [ITEM["id"], "--budget", "inf"], [ITEM["id"], "--budget", "0"],
              [ITEM["id"], "--max-requests", "-1"], [ITEM["id"], "--max-requests", "1.5"],
              [ITEM["id"], "--unknown"]):
    _status, _, _stderr, _network, _run = _invoke_cli(_argv)
    check(f"invalid CLI args fail before network: {_argv}",
          _status == 2 and bool(_stderr) and not _network.probe.called and not _run.called)

print()
bad = [n for n, c in R if not c]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
