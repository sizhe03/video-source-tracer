"""Regression: find_earlier's action buckets, on both backends.

The v4 review's two open items live here:
  * a candidate must not become actionable on search rank alone
  * an identical-title, same-duration earlier upload must not fall to unlikely
Offline: the search and metadata endpoints are stubbed.
"""
import io
import json
import sys
from contextlib import redirect_stdout

import _paths
import find_earlier as F
import _common

_common.throttle = lambda host, gap=None: None
F.throttle = _common.throttle

TITLE = "【ASMR】用麦克风在姐姐的大腿上摩擦_丝袜触发音"


def run(argv):
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = io.StringIO()
    try:
        with redirect_stdout(buf):
            F.main(argv)
    finally:
        sys.stderr = old
    return json.loads(buf.getvalue())


def buckets(out):
    d = {}
    for c in out["candidates"]:
        d.setdefault(c["bucket"], []).append(c["id"])
    return d


CASES = []

# A. bilibili --query: identical title, no dates, same duration
F.bili_json = lambda url, headers=None, retries=2: {"code": 0, "data": {"result": [
    {"bvid": "BVsame", "title": TITLE, "author": "shao\u5fa1", "mid": 1,
     "pubdate": 0, "duration": "11:02", "play": 61000},
    {"bvid": "BVdiff", "title": "\u5b8c\u5168\u65e0\u5173\u7684\u6728\u5de5\u6559\u5b66",
     "author": "x", "mid": 2, "pubdate": 0, "duration": "11:00", "play": 10},
]}}
out_a = run(["--query", TITLE, "--duration", "662", "ref"])

# B. youtube --yt: 40 unrelated results, dateless, rank only
F.yt_search = lambda q, limit=40: [
    {"platform": "youtube", "search_rank": i, "id": f"vid{i:02d}",
     "title": f"completely unrelated clip {i}", "pubdate": 0, "duration": 700,
     "author": f"ch{i}", "mid": f"UC{i}", "views": 1, "resolution": None}
    for i in range(40)]
out_b = run(["--yt", "\u592a\u3082\u3082 \u30de\u30a4\u30af", "--duration", "723", "seed"])

# C. youtube --yt: the real upstream shape (title matches, longer, top of search)
F.yt_search = lambda q, limit=40: [
    {"platform": "youtube", "search_rank": 3, "id": "PnAKqVB9PPo",
     "title": "\u592a\u3082\u3082 \u30de\u30a4\u30af \u3053\u3059\u308a\u3064\u3051\u308b ASMR",
     "pubdate": 0, "duration": 723, "author": "\u7518\u697d\u3089\u3080",
     "mid": "UCu8", "views": 3189, "resolution": None}]
out_c = run(["--yt", "\u592a\u3082\u3082 \u30de\u30a4\u30af \u3053\u3059\u308a\u3064\u3051\u308b",
             "--duration", "662", "seed"])

b_a, b_b, b_c = buckets(out_a), buckets(out_b), buckets(out_c)

CHECKS = [
    ("bilibili --query: identical title is actionable",
     "BVsame" in (b_a.get("examine_first", []) + b_a.get("worth_checking", [])),
     f"buckets={b_a}"),
    ("bilibili --query: unrelated title stays unlikely",
     "BVdiff" in b_a.get("unlikely", []),
     f"buckets={b_a}"),
    ("youtube --yt: rank alone never actionable",
     not b_b.get("examine_first") and not b_b.get("worth_checking")
     and len(b_b.get("rank_only", [])) == 20,
     f"rank_only={len(b_b.get('rank_only', []))} buckets={list(b_b)}"),
    ("youtube --yt: matching title + longer is actionable",
     bool(b_c.get("examine_first") or b_c.get("worth_checking")),
     f"buckets={b_c}"),
]

print(f"{'check':56s} ok")
print("-" * 70)
fails = 0
for name, ok, detail in CHECKS:
    print(f"{name:56s} {'PASS' if ok else 'FAIL'}")
    if not ok:
        fails += 1
        print(f"    {detail}")

print()
print(f"verdicts        : {out_a['verdict']} / {out_b['verdict']} / {out_c['verdict']}")
print(f"must_not_drop   : {len(out_a['must_not_drop'])} / "
      f"{len(out_b['must_not_drop'])} / {len(out_c['must_not_drop'])}")
print(f"{len(CHECKS) - fails}/{len(CHECKS)} passed")
sys.exit(1 if fails else 0)
