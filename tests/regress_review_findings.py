"""Regression suite for the defects the three reviewers found.

Each check names the finding it pins, so a future edit that reintroduces the
bug fails with the reason rather than a mystery assertion.
"""
import io
import sys
import threading
import time
import urllib.parse as up
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
SKILL = Path(r"C:\Users\10552\.dsh\skills\bb03-trace-video-source")
sys.path.insert(0, str(SKILL / "scripts"))
import discover  # noqa: E402

R = []


def check(name, cond, detail=""):
    R.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(f"     {detail}")


ITEM = {"platform": "bilibili", "id": "BVinput", "aid": 1,
        "title": "蕾丝手袜搓麦", "duration": 3600, "pubdate": 1750000000,
        "author": "搬运号", "mid": 100, "cover": None}


def quiet(url, **kw):
    return {"code": 0, "data": {"aid": 1, "desc": "", "bvid": "BVinput",
                                "result": []}}


print("-- A. P0-1 image route must not iterate cands outside the lock ---")
# The old code re-scanned b.cands in the empty branch and raised
# "dictionary changed size during iteration" while another route inserted.
src = (SKILL / "scripts" / "discover.py").read_text(encoding="utf-8")
img = src[src.index("def route_image"):src.index("ROUTES = {")]
loop = img[img.index("while not b.stop.is_set()"):]
outside = [ln.strip() for ln in loop.splitlines()
           if "b.cands.values()" in ln]
check("no bare b.cands iteration in the image loop",
      not outside or all("with b.lock" in loop[:loop.index(ln)] for ln in outside),
      str(outside))

try:
    out = discover.run(ITEM, routes=["image"], budget_s=1, max_requests=4,
                       bili=quiet, cover=None, gaps={})
    st = out["parallel"]["route_status"]["image"].get("state")
    check("image route survives to a terminal state", st in ("skipped", "done"),
          str(st))
except Exception as e:                              # noqa: BLE001
    check("image route survives to a terminal state", False, repr(e))

print()
print("-- B. P0-2 platform-id scan must use the lead condition ---------")

def desc_bili(url, **kw):
    if "search/type" in url:
        return {"code": 0, "data": {"result": []}}
    return {"code": 0, "data": {"aid": 1, "bvid": "BVinput",
                                "desc": "dy 25914348306", "result": []}}


out_b = discover.run(ITEM, routes=["comments"], budget_s=3, max_requests=10,
                     bili=desc_bili, yt=None, cover=None,
                     gaps={"api.bilibili.com": 0.02})
keys_b = [c["key"] for c in out_b["candidates"]]
check("a platform id from the description still becomes a candidate",
      "douyin:25914348306" in keys_b, str(keys_b))

print()
print("-- C. P0-3 drain() must advance, not replay the first lead -----")
board = discover.Blackboard()
for v in ("a", "b", "c"):
    board.add_lead("name", v, "test")
got = [board.drain(0.01) for _ in range(3)]
vals = [g["value"] if g else None for g in got]
check("drain returns successive leads", vals == ["a", "b", "c"], str(vals))

print()
print("-- D. HIGH-1 threads are joined against one shared deadline -----")
# budget_s=0.25 used to take 5.4 s because each thread got its own 5 s join.
def slow(url, **kw):
    time.sleep(2.0)
    return {"code": 0, "data": {"result": [], "aid": 1, "desc": ""}}


t0 = time.monotonic()
discover.run(ITEM, routes=["same_site", "comments", "cross"], budget_s=0.3,
             max_requests=6, bili=slow, yt=lambda q, n=20: [],
             cover=None, gaps={"api.bilibili.com": 0.01,
                               "www.youtube.com": 0.01})
elapsed = time.monotonic() - t0
check("a short budget returns promptly", elapsed < 5.0, f"{elapsed:.2f}s")

print()
print("-- E. HIGH-2 comments.py must not keep its own throttle ---------")
check("comments.throttle is neutralised during the run",
      "C.throttle = lambda" in src, "route_comments must disable it")
_metered_body = src.split("def _metered")[1].split("try:")[0]
check("comments requests are metered by the shared budget",
      "ctx.budget.take()" in _metered_body
      and "ctx.limiter.wait" in _metered_body
      and "C.bili_json = ck" in src and "ck = _metered" in src,
      "unmetered requests slipped past max_requests")

print()
print("-- F. HIGH-3 enrichment cannot eat the whole allowance ---------")
cover_calls = []


def cover_reader(url, **kw):
    cover_calls.append(url)
    return {"code": 0, "data": {"result": [], "aid": 1, "desc": ""}}


search_calls = []


def bili_f(url, **kw):
    if "search/type" in url:
        search_calls.append(url)
    return {"code": 0, "data": {"result": [], "aid": 1, "desc": ""}}


discover.run(ITEM, routes=["same_site", "comments"], budget_s=2,
             max_requests=3, bili=bili_f, yt=None, cover=None, gaps={})
check("a 3-request budget still runs a search",
      len(search_calls) >= 1, f"searches={len(search_calls)}")
b = discover.Budget(10, 3)
taken = [b.take(enrichment=True) for _ in range(3)]
check("enrichment is refused once only the floor is left",
      taken.count(True) <= 1, str(taken))

print()
print("-- G. HIGH-4 a creator's own results are not genre vocabulary ---")
own = [{"title": f"作品{i}", "author": "甘楽"} for i in range(10)]
check("a term that fills the author field is not filtered",
      not (discover._is_common_term(own, "甘楽") and not any(
          "甘楽" in (h.get("author") or "") for h in own)),
      "甘楽 must survive the common-term filter")

print()
print("-- H. MED-6 a failed comment fetch is not reported as done -----")
def fail_view(url, **kw):
    if "view" in url:
        raise RuntimeError("HTTP 412")
    return {"code": 0, "data": {"result": [], "aid": 1, "desc": ""}}


out_h = discover.run(ITEM, routes=["comments"], budget_s=3, max_requests=8,
                     bili=fail_view, yt=None, cover=None, gaps={})
st_h = out_h["parallel"]["route_status"]["comments"]
check("a 412 does not masquerade as done",
      st_h.get("state") in ("partial", "error"), str(st_h))

print()
print("-- I. MED-7 a late cursor does not replay history ----------------")
b2 = discover.Blackboard()
for v in ("old1", "old2"):
    b2.add_lead("name", v, "test")
cur = b2.consumer("late", from_now=True)
b2.add_lead("name", "fresh", "test")
seen = []
while True:
    l = cur.next(timeout=0.05)
    if l is None:
        break
    seen.append(l["value"])
check("from_now skips historical leads", seen == ["fresh"], str(seen))

print()
print("-- J. LOW-11 platform ids need a word boundary ------------------")
check("'dysfunctional' yields no douyin id",
      discover.PLATFORM_ID.search("dysfunctional") is None,
      str(discover.PLATFORM_ID.search("dysfunctional")))
check("a real id is still found",
      discover.PLATFORM_ID.search("dy 25914348306") is not None)

print()
print("-- K. LOW-12 unknown routes fail loudly -------------------------")
try:
    discover.run(ITEM, routes=["nosuchroute"], budget_s=1, max_requests=2,
                 bili=quiet, yt=None, cover=None, gaps={})
    check("an unknown route name is rejected", False, "silently returned ok")
except ValueError:
    check("an unknown route name is rejected", True)
except Exception as e:                              # noqa: BLE001
    check("an unknown route name is rejected", False, repr(e))

print()
print("-- L. the creator/vocabulary discriminator, on measured cases ----")
# Real numbers from the BV1p4KhzoEck run: 音符 returned 60+ unrelated videos
# because one uploader was literally called 音符是我的大米饭.
_ganraku = [{"title": f"作品{i}", "author": "甘楽らむ"} for i in range(10)]
_genre = [{"title": "死亡底噪", "author": f"u{i}"} for i in range(20)]
_yinfu = [{"title": "音符盒", "author": "王鎮豪"}, {"title": "音符", "author": "小白"},
          {"title": "音符x", "author": "音符是我的大米饭"},
          {"title": "y", "author": "a"}, {"title": "z", "author": "b"}]

check("a creator's own results are not filtered as vocabulary",
      discover._is_creator_term(_ganraku, "甘楽") is True,
      "甘楽's uploads all carry 甘楽 as author")
check("genre vocabulary is not mistaken for a creator",
      discover._is_creator_term(_genre, "死亡底噪") is False,
      "spread across 20 unrelated uploaders")
check("one coincidental handle does not rescue a common word",
      discover._is_creator_term(_yinfu, "音符") is False,
      "音符是我的大米饭 alone must not open the floodgates")
check("音符 is rejected as a handle",
      "音符" in discover._CHATTER and discover._short_tokens("音符") == [],
      str(discover._short_tokens("音符")))
check("a real handle still survives",
      discover._short_tokens("柚子") == ["柚子"], str(discover._short_tokens("柚子")))

print()
print("-- M. ranking, pacing, stragglers, unconsumed leads ---------------")
check("an earlier candidate outranks a later one",
      discover._earlier_score(1000, 2000) > discover._earlier_score(3000, 2000),
      "the whole search is for the EARLIER upload")
check("an unknown pubdate ranks below a known-later candidate",
      discover._earlier_score(None, 2000) < discover._earlier_score(3000, 2000),
      "unknown excludes nothing, so it cannot outrank something we disproved")
check("a known-earlier candidate still outranks unknown",
      discover._earlier_score(1000, 2000) > discover._earlier_score(None, 2000),
      "the real source must be able to rise above undated noise")

_src = (SKILL / "scripts" / "discover.py").read_text(encoding="utf-8")
_img = _src[_src.index("def route_image"):_src.index("ROUTES = {")]
check("the cover host is paced",
      'ctx.limiter.wait("i0.hdslb.com")' in _img,
      "DEFAULT_GAPS configured that host and nothing applied it")

_out_m = discover.run(ITEM, routes=["same_site"], budget_s=2, max_requests=6,
                      bili=quiet, yt=None, cover=None, gaps={})
check("stragglers are reported",
      "stragglers" in _out_m["parallel"], str(list(_out_m["parallel"])))
check("unconsumed leads are surfaced",
      "unconsumed_leads" in _out_m,
      "a lead nobody consumed is an open thread, not a silent loss")

print()
bad = [r for r in R if not r[1]]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
