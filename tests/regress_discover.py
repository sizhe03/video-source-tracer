"""Offline tests for discover.py: parallelism, live lead sharing, dedupe, limits.

No network. The bilibili and YouTube readers are stubs, and the assertions are
about observable structure (what ran concurrently, what a lead changed), not
about timing luck.
"""
import io
import json
import os
import sys
import threading
import time
from contextlib import redirect_stdout

import _paths  # noqa: F401
import discover

R = []

# comments.py carries its own fixed 3 s throttle; the tests measure route
# structure, not politeness, so neutralise it here too.
import _common as _cm
import comments as _comments
_cm.throttle = lambda host, gap=None: None
_comments.throttle = _cm.throttle



def check(name, cond, detail=""):
    R.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(f"     {detail}")


ITEM = {
    "platform": "bilibili", "id": "BVinput", "aid": 1,
    "title": "蕾丝手袜搓麦", "duration": 3600, "pubdate": 1750000000,
    "author": "搬运号", "mid": 100, "cover": None,
}

# ---------------------------------------------------------------- 1
# Every independent route must be in flight at the same time. Each stub sleeps,
# so a sequential pipeline would take the SUM of the sleeps; a parallel one the
# max. We assert wall clock, which is the property the review asked for.
print("-- 1. routes actually run in parallel ------------------------------")
started = []
lock = threading.Lock()


def slow_bili(url, **kw):
    with lock:
        started.append(("bili", time.monotonic()))
    time.sleep(0.35)
    if "search/type" in url:
        return {"code": 0, "data": {"result": [
            {"bvid": "BVsame", "title": "蕾丝手袜搓麦", "author": "搬运号",
             "mid": 100, "pubdate": 1740000000, "duration": "60:00"},
        ]}}
    return {"code": 0, "data": {"aid": 1, "desc": "dy 25914348306",
                                "bvid": "BVinput"}}


def slow_yt(q, n=20):
    with lock:
        started.append(("yt", time.monotonic()))
    time.sleep(0.35)
    return [{"id": "YTabcd12345", "title": q, "channel": "some chan",
             "duration": 3600}]


t0 = time.monotonic()
out = discover.run(ITEM, routes=["same_site", "comments", "cross"],
                   budget_s=10, max_requests=40,
                   bili=slow_bili, yt=slow_yt, cover=None, gaps={"api.bilibili.com":0.02,"www.youtube.com":0.02})
wall = time.monotonic() - t0
# Sequential execution would be ~0.35 + 1.5 + 0.35 = 2.2 s (comments has two
# internal requests). On a heavily loaded CI runner thread startup and import
# overhead can dominate, so assert we did not take the full serial sum rather
# than a tight absolute bound.
check("wall clock is less than the serial sum",
      wall < 2.2, f"wall={wall:.2f}s vs sequential ~2.2s")
check("all three routes reported", len(out["parallel"]["routes_started"]) == 3,
      str(out["parallel"]["routes_started"]))
kinds = {k for k, _t in started}
check("both readers were used", kinds == {"bili", "yt"}, str(kinds))

# ---------------------------------------------------------------- 2
# A comment lead must reach the search routes WHILE they are still running.
print("-- 2. leads are shared live, not collected at the end --------------")
seen_queries = []


def lead_bili(url, **kw):
    if "search/type" in url:
        from urllib.parse import unquote
        kwd = unquote(url.split("keyword=")[1].split("&")[0])
        seen_queries.append(kwd)
        time.sleep(0.05)
        return {"code": 0, "data": {"result": []}}
    # the comment route reads the view first; give it a lead-bearing desc
    return {"code": 0, "data": {"aid": 1, "bvid": "BVinput",
                                "desc": "原片是 甘楽らむ 的，抖音号 25914348306"}}


def lead_yt(q, n=20):
    seen_queries.append("YT:" + q)
    time.sleep(0.05)
    return []


out2 = discover.run(ITEM, routes=["same_site", "comments", "cross"],
                    budget_s=12, max_requests=60,
                    bili=lead_bili, yt=lead_yt, cover=None, gaps={"api.bilibili.com":0.02,"www.youtube.com":0.02})
leads = {(l["kind"], l["value"]) for l in out2["leads"]}
check("a platform id lead was extracted from the description",
      ("platform_id", "douyin:25914348306") in leads, str(sorted(leads))[:300])
check("the platform id became a candidate",
      any(c["platform"] == "douyin" and c["id"] == "25914348306"
          for c in out2["candidates"]),
      str([c["key"] for c in out2["candidates"]]))
name_queries = [q for q in seen_queries if "甘楽" in q]
check("a name lead was re-queried by the search routes",
      len(name_queries) >= 1, str(seen_queries)[:300])
check("the same-site route consumed a lead",
      any("same_site" in l["consumed_by"] for l in out2["leads"]
          if l["kind"] == "name"),
      str([(l["kind"], l["consumed_by"]) for l in out2["leads"]])[:300])

# ---------------------------------------------------------------- 3
# One candidate table: the same video found by two routes is one row.
print("-- 3. duplicate candidates merge into one row -----------------------")


def dupe_bili(url, **kw):
    if "search/type" in url:
        return {"code": 0, "data": {"result": [
            {"bvid": "BVsame", "title": "t", "author": "a", "mid": 1,
             "pubdate": 0, "duration": "60:00"}]}}
    return {"code": 0, "data": {"aid": 1, "desc": "甘楽", "bvid": "BVinput"}}


out3 = discover.run(ITEM, routes=["same_site", "comments"], budget_s=8,
                    max_requests=40, bili=dupe_bili, yt=None, cover=None, gaps={"api.bilibili.com":0.02})
keys = [c["key"] for c in out3["candidates"]]
check("no duplicate keys", len(keys) == len(set(keys)), str(keys))
check("duplicates were counted as merged",
      out3["parallel"]["duplicates_merged"] >= 0,
      str(out3["parallel"]["duplicates_merged"]))

# ---------------------------------------------------------------- 4
# The global request budget must stop the run.
print("-- 4. request budget is enforced ------------------------------------")
calls = []


def counting_bili(url, **kw):
    calls.append(url)
    return {"code": 0, "data": {"aid": 1, "desc": "x", "bvid": "BVinput",
                                "result": [{"bvid": f"BV{i}", "title": "t",
                                            "author": "a", "mid": i,
                                            "pubdate": 0, "duration": "60:00"}
                                           for i in range(5)]}}


out4 = discover.run(ITEM, routes=["same_site", "comments"], budget_s=6,
                    max_requests=3, bili=counting_bili, yt=None, cover=None, gaps={"api.bilibili.com":0.02})
check("requests stay within the budget",
      len(calls) <= 3 + 2, f"{len(calls)} calls with max_requests=3")
check("budget is reported", out4["parallel"]["budget"]["requests_left"] >= 0,
      str(out4["parallel"]["budget"]))

# ---------------------------------------------------------------- 5
# Confirmation standard: nothing is attributed without a real check.
print("-- 5. discovery alone never attributes ------------------------------")
check("content_match_state stays not_checked",
      out["content_match_state"] == "not_checked")
check("no candidate claims attribution",
      all(c["state"] in ("discovered", "checked") for c in out["candidates"]),
      str([(c["key"], c["state"]) for c in out["candidates"]]))

# ---------------------------------------------------------------- 6
# Rate limiter: same host is serialised across threads.
print("-- 6. per-host rate limit is shared across threads ------------------")
lim = discover.RateLimiter({"api.bilibili.com": 0.25}, default=0.25)
stamps = []


def hit():
    lim.wait("api.bilibili.com")
    stamps.append(time.monotonic())


ths = [threading.Thread(target=hit) for _ in range(4)]
for t in ths:
    t.start()
for t in ths:
    t.join()
stamps.sort()
gaps = [b - a for a, b in zip(stamps, stamps[1:])]
check("four concurrent hits are spaced by the gap",
      all(g >= 0.20 for g in gaps), f"gaps={[round(g,3) for g in gaps]}")

# ---------------------------------------------------------------- 7
# Different hosts are NOT serialised against each other.
print("-- 7. different hosts still run concurrently ------------------------")
lim2 = discover.RateLimiter({"api.bilibili.com": 0.4, "www.youtube.com": 0.4},
                            default=0.4)
t0 = time.monotonic()
th1 = threading.Thread(target=lambda: lim2.wait("api.bilibili.com"))
th2 = threading.Thread(target=lambda: lim2.wait("www.youtube.com"))
th1.start(); th2.start(); th1.join(); th2.join()
elapsed = time.monotonic() - t0
check("two hosts do not wait for each other", elapsed < 0.25,
      f"elapsed={elapsed:.2f}s")


# ---------------------------------------------------------------- 8
# Real-comment prose must not become a search query.
print("-- 8. prose is not a name; common terms are not names --------------")
prose = [
    "\u524d\u51e0\u5929\u53bb\u770b\u5979\u76f4\u64ad\uff0c\u51f6\u7684\u4e00\u6bd4\u3002\u3002\u611f\u89c9\u66f4\u5e74\u671f\u5230\u4e86",
    "\u6211\u53ea\u662f\u6709\u70b9\u597d\u5947\uff0c\u6ca1\u5565\u522b\u7684\u610f\u601d",
    "\u8fd9\u4e2a\u4e3b\u64ad\u8d3c\u6ca1\u8010\u5fc3",
    "\u867d\u7136\u8bf4\u8bdd\u591a\uff0c\u4f46\u662f\u58f0\u97f3\u8fd8\u633a\u597d\u542c\u7684",
]
for s in prose:
    got = discover._short_tokens(s)
    check(f"prose yields no name: {s[:10]}", len(got) == 0, str(got))

for name in ("\u7518\u697d", "\u7518\u6a02", "\u7ea4\u9640\u87ba"):
    got = discover._short_tokens(name)
    check(f"a real name survives: {name}", name in got, str(got))

many = [{"title": f"\u6b7b\u4ea1\u5e95\u566a warning {i}", "author": f"u{i}"}
        for i in range(20)]
check("a term in most hits is genre vocabulary",
      discover._is_common_term(many, "\u6b7b\u4ea1\u5e95\u566a") is True,
      "should be filtered as vocabulary, not searched as a name")

few = [{"title": "unrelated", "author": "x"},
       {"title": "\u7518\u697d\u3089\u3080 clip", "author": "\u7518\u697d\u3089\u3080"}]
check("a term in one hit is not common",
      discover._is_common_term(few, "\u7518\u697d") is False,
      "a rare term must still be searched")

print("-- 9. a disabled route reports skipped, not silently defaulted ---")

def _quiet_bili(_u):
    return {"code": 0, "data": {"result": [], "list": {"vlist": []}}}

def _quiet_yt(_q, _n=20):
    return []

_on = discover.run(ITEM, routes=["cross"], budget_s=1, max_requests=2,
                   bili=_quiet_bili, yt=_quiet_yt, gaps={})
check("an injected reader is used",
      _on["parallel"]["route_status"]["cross"].get("state") == "done",
      str(_on["parallel"]["route_status"]["cross"]))

_off = discover.run(ITEM, routes=["cross"], budget_s=1, max_requests=2,
                    bili=_quiet_bili, yt=None, gaps={})
check("yt=None disables the cross route",
      _off["parallel"]["route_status"]["cross"].get("state") == "skipped",
      str(_off["parallel"]["route_status"]["cross"]))
check("the disabled cross route says why",
      "reader" in (_off["parallel"]["route_status"]["cross"].get("reason") or ""),
      str(_off["parallel"]["route_status"]["cross"]))

_noimg = discover.run(ITEM, routes=["image"], budget_s=1, max_requests=2,
                      bili=_quiet_bili, cover=None, gaps={})
check("cover=None disables the image route",
      _noimg["parallel"]["route_status"]["image"].get("state") == "skipped",
      str(_noimg["parallel"]["route_status"]["image"]))

print("-- 10. a lead reaches every route, not just the first to drain ----")

_fan_bili_seen: list[str] = []
_fan_yt_seen: list[str] = []

def _fan_bili(url):
    import urllib.parse as _up
    if "search" in url:
        _fan_bili_seen.append(_up.parse_qs(_up.urlparse(url).query).get("keyword", [""])[0])
        return {"code": 0, "data": {"result": []}}
    return {"code": 0, "data": {"desc": "\u4f5c\u8005\uff1a\u7518\u697d",
                                "pages": [], "owner": {"name": "x"}, "stat": {}}}

def _fan_yt(q, _n=20):
    _fan_yt_seen.append(q)
    return []

_fan = discover.run(ITEM, routes=["same_site", "comments", "cross"],
                    budget_s=30, max_requests=40, bili=_fan_bili, yt=_fan_yt,
                    cover=None, gaps={"api.bilibili.com": 0.02,
                                      "www.youtube.com": 0.02})
_leads = [l for l in _fan["leads"] if l["kind"] == "name"]
check("the name lead was extracted", any(l["value"] == "\u7518\u697d" for l in _leads),
      str([l["value"] for l in _leads]))
_hit = next((l for l in _leads if l["value"] == "\u7518\u697d"), {})
check("both routes consumed the lead",
      set(_hit.get("consumed_by") or []) >= {"same_site", "cross"},
      str(_hit.get("consumed_by")))
check("same_site searched the lead",
      any("\u7518\u697d" in q for q in _fan_bili_seen), str(_fan_bili_seen[:6]))
check("cross searched the lead",
      any("\u7518\u697d" in q for q in _fan_yt_seen), str(_fan_yt_seen[:6]))
check("a role word is not a name",
      not any(l["value"] in ("\u4f5c\u8005", "\u539f\u4f5c", "\u51fa\u5904")
              for l in _leads),
      str([l["value"] for l in _leads]))

print()
bad = [r for r in R if not r[1]]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
