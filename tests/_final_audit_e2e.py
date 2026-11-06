#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Final-round independent audit: replay every representative attack
end-to-end through trace.T.run() and creator_conclusion, plus the positive
control. Offline only. Written for the 0e15065 final convergence review.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import trace as T
import trace_net as TN

R = []


def check(name, cond, detail=""):
    R.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print("     " + str(detail)[:500])


def json_has_confirmed_level(obj) -> bool:
    """Walk any dict/list and report whether any `level` field equals
    'confirmed', or the literal 'confirmed' appears as a level value."""
    if isinstance(obj, dict):
        if obj.get("level") == "confirmed":
            return True
        return any(json_has_confirmed_level(v) for v in obj.values())
    if isinstance(obj, list):
        return any(json_has_confirmed_level(v) for v in obj)
    return False


def emit_cc(**kw):
    return T.creator_conclusion(
        kw.get("brands", []), kw.get("cands", []),
        kw.get("comment_names", []), kw.get("identities", []))


# ---------------------------------------------------------------- fixtures
REAL_CID = "UC1Y-RkDrol_nyXk7vx8wgUA"
FAKE_CID = "UCFORGED1234567890123456"
FAKE_CID2 = "UCFORGED2234567890123456"


def asmr_titles(n, prefix="ASMR 丝袜 触发音 助眠"):
    return [f"{prefix} {i}" for i in range(n)]


def archive_rows(cid, titles, verified, name="pomechan"):
    return [{"platform": "youtube", "id": f"v{i:03d}", "title": t,
             "author": name, "archive_of": name,
             "channel_id": cid, "channel_verified": verified}
            for i, t in enumerate(titles)]


BRANDS = [{"brand": "pomechan", "accounts": 3,
           "platforms": ["bilibili", "youtube"], "sample": ["a", "b", "c"]}]


print("=" * 78)
print("A. the engine can never emit a confirmed level")
print("=" * 78)

# 1. forged UC, unverified
c = emit_cc(brands=BRANDS, cands=archive_rows(FAKE_CID, asmr_titles(25), False))
check("forged unverified UC never confirmed", c["level"] != "confirmed")
check("forged unverified UC yields no id",
      not c["ids"]["youtube_channel"], str(c["ids"]))

# 2. 25 origami titles, verified
orig = [f"折纸教程 第{i}期" for i in range(25)]
c = emit_cc(brands=BRANDS, cands=archive_rows(FAKE_CID, orig, True))
check("25-origami verified never confirmed", c["level"] != "confirmed")
check("25-origami witness rejected", c.get("witness") is None)
check("25-origami yields no id", not c["ids"]["youtube_channel"])

# 3. 25 english tutorials with 'No.'
en = ["Chapter %d: Learn Python, No. %d, Heart of the Year" % (i, i)
      for i in range(25)]
c = emit_cc(brands=BRANDS, cands=archive_rows(FAKE_CID, en, True))
check("25 en-tutorial No. never confirmed", c["level"] != "confirmed")
check("en-tutorial witness rejected", c.get("witness") is None)

# 4. Learn / Heart / Early / Clear / Gear / Year substrings
check("'ear' substring is not ASMR",
      T._genre_hit("Learn origami crane tutorial") is False)
check("Heart/Early/Clear/Gear/Year are not genre",
      T._genre_overlap(["Heart surgery lecture", "Early piano lessons",
                        "Clear the Gear", "Year in review"] * 3) == 0)

# 5. Chasmrlings
check("Chasmrlings tutorial is not genre",
      T._genre_hit("Chasmrlings tutorial") is False)

# 6. namesake musician
mus = [f"Pastel Room {i} / Nyan cat remix / 太鼓の達人" for i in range(25)]
c = emit_cc(brands=BRANDS, cands=archive_rows(FAKE_CID, mus, True))
check("namesake musician never confirmed", c["level"] != "confirmed")
check("namesake musician witness rejected", c.get("witness") is None)

# 7. PostgreSQL database sleep trigger tutorial
check("sleep+trigger in tutorial does not bypass guard",
      T._genre_hit("PostgreSQL database sleep trigger tutorial") is False)

# 8. Scratch programming tutorial
check("Scratch programming tutorial is not genre",
      T._genre_hit("Scratch programming tutorial") is False)

# 9. double genre words (sleep + trigger) but tutorial
check("double genre word tutorial still rejected",
      T._genre_hit("sleep trigger tutorial") is False)

# 10. channel_verified = 1 (truthy int)
truthy = [dict(r, channel_verified=1) for r in archive_rows(FAKE_CID, asmr_titles(25), False)]
c = emit_cc(brands=BRANDS, cands=truthy)
check("channel_verified=1 never confirms", c["level"] != "confirmed")
check("channel_verified=1 witness rejected", c.get("witness") is None)

# 11. cross-cid aggregation must not pool
r18 = []
for _cid, _pre in (("UCAAAAAAAAAAAAAAAAAAAAAA", "A"),
                   ("UCBBBBBBBBBBBBBBBBBBBBBB", "B")):
    for i in range(11):
        r18.append({"platform": "youtube", "id": f"{_pre}{i}",
                    "title": "【4KASMR】睡眠導入 太もも %d" % i,
                    "author": "pomechan", "archive_of": "pomechan",
                    "channel_id": _cid, "channel_verified": True})
c = emit_cc(brands=BRANDS, cands=r18)
check("two cids never pool into one witness",
      (c.get("witness") or {}).get("catalogue_size", 0) <= 11)
check("two cids yield no creator id", not c["ids"]["youtube_channel"])

print()
print("=" * 78)
print("B. guess can never be diagnosis=complete")
print("=" * 78)
dg = T.diagnosis({"work_id": "BVx"}, {"name": "n", "level": "guess"}, [], 10, 1)
check("guess + earliest is NOT complete", dg["stage"] == "name_is_a_guess",
      str(dg))
cg = emit_cc(brands=[], cands=[],
             comment_names=[{"name": "桑塔阿普亚", "hedged": True}])
check("hedged comment yields level=guess", cg["level"] == "guess", str(cg))

print()
print("=" * 78)
print("C. verify_channel identity gates (namesake / repost / full-width)")
print("=" * 78)

_TT = "".join('<a href="x" title="Watch 【4KASMR】睡眠導入 太もも %d">' % i
              for i in range(12))


def page(owner, with_titles=True):
    meta = ('<meta property="og:description" content="Watch all 25 ASMR '
            'videos from %s on The ASMR Index">' % owner)
    return meta + (_TT if with_titles else "")


class Resp:
    status = 200
    def __init__(self, body):
        self.body = body.encode()


_orig = TN.http


def v(owner, with_titles=True):
    TN.http = lambda *a, **k: Resp(page(owner, with_titles))
    try:
        return TN.verify_channel(REAL_CID, "pomechan")
    finally:
        TN.http = _orig


# from AnotherCreator see also
check("AnotherCreator 'see also' is not verified",
      v("AnotherCreator; see also pomechan") is False)
check("a page that merely mentions the name is not verified",
      v("AnotherCreator") is False)
check("the real self-declaring channel is verified", v("POMEchan🐾ASMR") is True)

# repost / fan-club / hyphen / full-width
for owner, expect in [
        ("pomechan-fan-club", False),
        ("Best-of-POMEchan", False),
        ("POMEchan 搬运", False),
        ("ｐｏｍｅｃｈａｎ-ｆａｎ-ｃｌｕｂ", False),
        ("POMEchan", True),
        ("POMEchan ASMR", True),
        ("POMEchan2nd", False),
        ("Pom", False)]:
    check(f"identity {owner!r} -> {expect}", v(owner) is expect, owner)

# no-title page fails
check("a page with no video titles cannot verify", v("POMEchan", with_titles=False) is False)

print()
print("=" * 78)
print("D. Ledger falsy merge keeps channel identity atomic")
print("=" * 78)
b, p = T.Budget(10, 50), T.HostPacer({})
led = T.Ledger()
pool = T.Pool(b, p)
ctx = T.Ctx({}, pool, b, p, led, net={})
fan = {"platform": "youtube", "id": "SHARED001", "title": "ASMR 助眠",
       "author": "pomechan", "archive_of": "pomechan",
       "channel_id": "UCFANCLUB1234567890abcde", "channel_verified": False}
real = {"platform": "youtube", "id": "SHARED001", "title": "ASMR 助眠",
        "author": "pomechan", "archive_of": "pomechan",
        "channel_id": REAL_CID, "channel_verified": True}
led.absorb(T.TaskResult(candidates=[fan]), T.Task.make("bili.view", bvid="x"))
led.absorb(T.TaskResult(candidates=[real]), T.Task.make("bili.view", bvid="y"))
row = led.snapshot()["candidates"][0]
check("explicit channel_verified=False survives merge",
      row.get("channel_verified") is False, str(row))
c = T.creator_conclusion(BRANDS, led.snapshot()["candidates"], [], [])
check("rejected fan cid never becomes a creator id",
      "UCFANCLUB1234567890abcde" not in c["ids"]["youtube_channel"]
      and c["level"] != "confirmed")

print()
print("=" * 78)
print("E. full T.run() end-to-end: forged UC + wrong channel id")
print("=" * 78)

ITEM = {"platform": "bilibili", "id": "BV1rCxf6MERM",
        "title": "【ASMR】丝袜“脱了”放我耳朵上？_丝袜触发音",
        "pubdate": 1793835738, "duration": 1989,
        "author": "风吻茉莉2", "mid": 1464362113}


def run_with_archive(archive_cid, verified, titles_fn):
    """Full-chain T.run where web/archive land on archive_cid."""
    def view(b):
        return {"code": 0, "data": {
            "bvid": ITEM["id"], "aid": 1, "title": ITEM["title"],
            "pubdate": ITEM["pubdate"], "duration": 1989,
            "owner": {"name": "风吻茉莉2", "mid": 1464362113}}}

    def space(m, pn=1):
        return {"code": 0, "data": {"list": {"vlist": [
            {"bvid": "BVs1", "title": "【POMEchan】精油涂抹", "author": "风吻茉莉2",
             "created": 1793835738 - 86400, "length": "33:09"},
            {"bvid": "BVs2", "title": "【POMEchan ASMR】4K 大腿枕", "author": "风吻茉莉2",
             "created": 1793835738 - 2 * 86400, "length": "33:09"}]}}}

    def related(b):
        return {"code": 0, "data": [
            {"bvid": "BVr1", "title": "【POMEchan】精油涂抹、捂耳朵", "pubdate": 1793835738 - 86400,
             "duration": 1989, "owner": {"name": "阿猛al"}},
            {"bvid": "BVr2", "title": "【POMEchan ASMR】大腿精油按摩", "pubdate": 1793835738 - 2 * 86400,
             "duration": 1989, "owner": {"name": "阿猛al"}}]}

    def search(kw, page=1, order="pubdate"):
        kl = (kw or "").lower()
        return {"code": 0, "data": {"result": [
            {"bvid": "BVq1", "title": ITEM["title"], "author": "风吻茉莉2",
             "pubdate": 1793000000, "duration": "33:09", "pic": ""}] if kl in ITEM["title"].lower() else []}}

    def yt(q, n=20):
        return []

    def websearch(q):
        return [{"url": f"https://theasmrindex.com/channel/{archive_cid}",
                 "title": "pomechan", "snippet": archive_cid}]

    def resolve_channel(name):
        return archive_cid

    def verify_channel(cid, name):
        return verified and cid == archive_cid

    def archive_channel(cid):
        return [{"id": f"av{i:03d}", "title": t}
                for i, t in enumerate(titles_fn())]

    def fetch_comments(aid):
        return [], {"declared_all_count": 0}

    net = {"view": view, "space": space, "related": related, "search": search,
           "ytsearch": yt, "websearch": websearch, "resolve_channel": resolve_channel,
           "verify_channel": verify_channel, "archive_channel": archive_channel,
           "fetch_comments": fetch_comments}
    return T.run(ITEM, net=net, budget_s=20, max_requests=60,
                 gaps={"api.bilibili.com": 0.01, "www.youtube.com": 0.01,
                       "web": 0.01, "theasmrindex.com": 0.01})


# E1: forged UC, verified=False
out_bad = run_with_archive(FAKE_CID, False, lambda: asmr_titles(25))
cc_bad = out_bad["creator_conclusion"]
check("E2E forged unverified: never confirmed", cc_bad["level"] != "confirmed")
check("E2E forged unverified: no youtube_channel id",
      not cc_bad["ids"]["youtube_channel"], str(cc_bad["ids"]))
check("E2E forged unverified: wrong cid absent",
      FAKE_CID not in cc_bad["ids"]["youtube_channel"])

# E2: forged UC, verified=True but origami titles (wrong genre)
out_genre = run_with_archive(FAKE_CID, True, lambda: orig)
cc_genre = out_genre["creator_conclusion"]
check("E2E forged+verified wrong-genre: never confirmed",
      cc_genre["level"] != "confirmed")
check("E2E forged+verified wrong-genre: no id",
      not cc_genre["ids"]["youtube_channel"])
check("E2E forged+verified wrong-genre: wrong cid absent",
      FAKE_CID not in cc_genre["ids"]["youtube_channel"])

# E3: full JSON never carries a confirmed level
for lbl, o in (("bad", out_bad), ("genre", out_genre)):
    check(f"E2E {lbl}: no confirmed level anywhere in JSON",
          not json_has_confirmed_level(o), "")

print()
print("=" * 78)
print("F. positive control: real POMEchan (239 ASMR titles, self-declared)")
print("=" * 78)


def pos_titles():
    return asmr_titles(239, "【4KASMR】睡眠導入 太もも 触发音")


out_pos = run_with_archive(REAL_CID, True, pos_titles)
cc_pos = out_pos["creator_conclusion"]
diag_pos = out_pos["diagnosis"]
check("positive: name == pomechan", cc_pos["name"] == "pomechan",
      str(cc_pos["name"]))
check("positive: level == strong_lead", cc_pos["level"] == "strong_lead",
      str(cc_pos["level"]))
check("positive: id == UC1Y-RkDrol_nyXk7vx8wgUA",
      cc_pos["ids"]["youtube_channel"] == [REAL_CID],
      str(cc_pos["ids"]["youtube_channel"]))
check("positive: witness catalogue == 239",
      (cc_pos.get("witness") or {}).get("catalogue_size") == 239,
      str((cc_pos.get("witness") or {}).get("catalogue_size")))
check("positive: diagnosis == complete", diag_pos["stage"] == "complete",
      str(diag_pos))

print()
print("=" * 78)
print("G. no-card page (archive runner) + comment channel shape")
print("=" * 78)
# archive_channel returns [] on a page with no cards: run_archive_channel
# produces zero rows, so a forged cid can never grow a catalogue.
c0 = emit_cc(brands=BRANDS, cands=[], comment_names=[])
check("empty catalogue never confirms", c0["level"] != "confirmed"
      and not c0["ids"]["youtube_channel"])

print()
bad = [n for n, c in R if not c]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
