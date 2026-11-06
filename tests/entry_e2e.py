"""End-to-end entry test: run the REAL main() of every CLI against stubs.

The v4 review's central finding was "the bottom layer has a result, the top
layer reports none". Checking `sys.exit` would never catch that, so this test
reads the emitted JSON: findings, confirmed, evidence dimensions, next-step text.

Offline. Polite sleeps are stubbed out; nothing touches the network.
"""
import io
import json
import sys
from contextlib import redirect_stdout

import _paths
import authorcheck
import comments
import alias_resolve
import find_earlier
import deep_sweep
import _common

_common.throttle = lambda host, gap=None: None
for mod in (comments, alias_resolve, authorcheck, find_earlier, deep_sweep):
    if hasattr(mod, "throttle"):
        mod.throttle = _common.throttle

AID = 117556811667338
TITLE = "\u3010ASMR\u3011\u7528\u9ea6\u514b\u98ce\u5728\u59d0\u59d0\u7684\u5927\u817f\u4e0a\u6469\u64e6_\u4e1d\u889c\u89e6\u53d1\u97f3"


class FakeBili:
    """A 38-reply thread. The answer (`\u7518\u6a02`) lives in a sub-reply."""

    def __init__(self):
        self.sub_calls = []

    def json(self, url, headers=None, retries=2):
        if "web-interface/view" in url:
            return {"code": 0, "data": {
                "bvid": "BV1cU5C68EEi", "aid": AID, "cid": 1, "title": TITLE,
                "pubdate": 1778600000, "duration": 662,
                "owner": {"mid": 3537121159678567, "name": "shao\u5fa1"},
                "dimension": {"width": 1920, "height": 1080},
                "stat": {"view": 61000, "reply": 38}}}
        if "reply/reply" in url:
            self.sub_calls.append(url)
            return {"code": 0, "data": {"replies": [
                {"member": {"uname": "\u7ea4\u9640\u87ba"},
                 "content": {"message": "\u7518\u6a02"}, "like": 2}]}}
        if "reply/main" in url:
            return {"code": 0, "data": {
                "cursor": {"all_count": 38, "is_end": True, "next": 0},
                "replies": [
                    {"rpid": 1, "member": {"uname": "op"},
                     "content": {"message": "\u8fd9\u662f\u54ea\u4f4d\u554a"},
                     "like": 9, "rcount": 2},
                    {"rpid": 2, "member": {"uname": "b"},
                     "content": {"message": "\u8fd9\u8c01\u554a"},
                     "like": 3, "rcount": 1},
                    {"rpid": 3, "member": {"uname": "c"},
                     "content": {"message": "\u539f\u7247\u662f "
                                             "https://www.youtube.com/@KanraRamu "
                                             "\u8fd9\u4e2a\u9891\u9053"},
                     "like": 30, "rcount": 0}]}}
        if "search/type" in url:
            import urllib.parse as up
            kw = up.unquote(url.split("keyword=")[1].split("&")[0])
            return {"code": 0, "data": {"result": self._search(kw)}}
        return {"code": 0, "data": {}}

    def _search(self, kw):
        rows = []
        if kw.startswith("\u7518\u6a02") and "ASMR" in kw:
            rows.append({"bvid": "BVx1", "title": "\u53f0\u7063\u6587\u5316\u4ecb\u7d39",
                         "author": "\u53f0\u7063\u7518\u6a02", "mid": 111,
                         "pubdate": 1700000000, "duration": "5:00", "play": 1})
        if kw.startswith("\u7518\u697d") and "ASMR" in kw:
            rows.append({"bvid": "BVx2",
                         "title": "\u3010ASMR\u3011\u30b7\u30b9\u30bf\u30fc\u3055\u3093\u306e\u592a\u3082\u3082",
                         "author": "\u7518\u697d\u3089\u3080", "mid": 222,
                         "pubdate": 1778600000, "duration": "12:03", "play": 9})
        return rows


fake = FakeBili()
comments.bili_json = fake.json
alias_resolve.bili_json = fake.json
authorcheck.bili_json = fake.json


def fake_yt(q, n=20):
    if q.startswith("\u7518\u697d") and "ASMR" in q:
        return [{"channel": "\u7518\u697d\u3089\u3080", "channel_id": "UCu8",
                 "title": "\u3010ASMR\u3011\u30b7\u30b9\u30bf\u30fc\u3055\u3093\u306e\u592a\u3082\u3082\u306b\u30de\u30a4\u30af"},
                {"channel": "\u7518\u697d\u3089\u3080", "channel_id": "UCu8",
                 "title": "ASMR \u8131\u304e\u305f\u3066\u9774\u4e0b"}]
    if q.startswith("\u7518\u6a02") and "ASMR" in q:
        return [{"channel": "\u53f0\u7063\u7518\u6a02", "channel_id": "UCt",
                 "title": "\u53f0\u6e7e \u6587\u5316 \u4ecb\u7d39 \u65c5\u884c"}]
    return []


authorcheck._yt_search = fake_yt


def run(mod, argv):
    buf, err, old = io.StringIO(), io.StringIO(), sys.stderr
    sys.stderr = err
    try:
        with redirect_stdout(buf):
            mod.main(argv)
    finally:
        sys.stderr = old
    try:
        return json.loads(buf.getvalue())
    except Exception:
        return {"_raw": buf.getvalue()[:600]}


CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name)
    if not ok and detail:
        print(f"     {detail}")


print("=" * 78)
print("ENTRY 1  comments.py BV1cU5C68EEi --verify")
print("=" * 78)
o1 = run(comments, ["BV1cU5C68EEi", "--verify"])
findings = o1.get("findings") or []
leads = o1.get("leads") or []
print(f"  comments={o1.get('comments')} candidates={len(o1.get('candidates') or [])} "
      f"findings={len(findings)} leads={len(leads)}")
print(f"  next={o1.get('next')}")
check("a finding is reported (was 0 before the fix)", len(findings) >= 1,
      json.dumps(o1, ensure_ascii=False)[:400])
check("the finding is \u7518\u6a02",
      any(f.get("token") == "\u7518\u6a02" for f in findings),
      str([f.get("token") for f in findings]))
check("the direct URL/handle lead is preserved", len(leads) == 2,
      str([l.get("token") for l in leads]))
check("next-step text points at the finding",
      "confirm" in (o1.get("next") or ""), o1.get("next"))

print()
print("=" * 78)
print("ENTRY 2  alias_resolve.py --bvid BV1cU5C68EEi --verify")
print("=" * 78)
o2 = run(alias_resolve, ["--bvid", "BV1cU5C68EEi", "--verify"])
resolved = o2.get("worth_pursuing") or o2.get("resolved") or 0
print(f"  tokens={o2.get('tokens_searched')} worth_pursuing={resolved} "
      f"leads={o2.get('leads')}")
print(f"  unresolved={o2.get('unresolved')}")
check("at least one token is worth pursuing", resolved >= 1,
      json.dumps(o2, ensure_ascii=False)[:400])
check("confirmed list is NOT empty (was [] before the fix)",
      bool(o2.get("confirmed")),
      f"confirmed={o2.get('confirmed')}")
check("leads are reported separately, not searched as names",
      len(o2.get("leads") or []) == 2, str(o2.get("leads")))

print()
print("=" * 78)
print("ENTRY 3  find_earlier.py: 40 unrelated dateless results")
print("=" * 78)


def fake_earlier(url, headers=None, retries=2):
    if "view" in url:
        return {"code": 0, "data": {
            "bvid": "BV1cU5C68EEi", "title": TITLE, "pubdate": 1778600000,
            "duration": 662, "owner": {"name": "shao\u5fa1", "mid": 1},
            "stat": {"view": 1},
            "dimension": {"width": 1920, "height": 1080}, "pic": ""}}
    return {"code": 0, "data": {"result": [
        {"bvid": f"BVunrel{i:02d}", "title": f"\u65e0\u5173\u89c6\u9891 {i}",
         "author": f"c{i}", "mid": 900 + i, "pubdate": 0,
         "duration": f"1:{i:02d}", "play": 10} for i in range(40)]}}


find_earlier.bili_json = fake_earlier
o3 = run(find_earlier, ["BV1cU5C68EEi"])
b3 = {}
for c in o3.get("candidates") or []:
    b3.setdefault(c["bucket"], []).append(c["id"])
print(f"  verdict={o3.get('verdict')} buckets={ {k: len(v) for k, v in b3.items()} }")
check("no unrelated result is actionable",
      not b3.get("examine_first") and not b3.get("worth_checking"),
      str({k: len(v) for k, v in b3.items()}))

print()
print("=" * 78)
print("ENTRY 4  deep_sweep.py: nothing silently truncated")
print("=" * 78)


def fake_sweep(url, headers=None, retries=2):
    if "view" in url:
        return {"code": 0, "data": {
            "bvid": "BV1cU5C68EEi", "title": TITLE, "pubdate": 1778600000,
            "duration": 662, "owner": {"name": "shao\u5fa1", "mid": 1}}}
    rows = [{"bvid": f"BVin{i:02d}", "title": f"c{i}", "author": "a",
             "duration": f"0:{10 + i}", "play": 1} for i in range(50)]
    rows.append({"bvid": "BVlong", "title": "long", "author": "a",
                 "duration": "2:00:00", "play": 1})
    return {"code": 0, "data": {"result": rows}}


deep_sweep.bili_json = fake_sweep
o4 = run(deep_sweep, ["BV1cU5C68EEi", "--alias", "\u7518\u6a02", "--pages", "1"])
allrows = (o4.get("ranked_in_band") or []) + (o4.get("ranked_out_of_band") or [])
print(f"  unique={o4.get('unique_seen')} in_band={len(o4.get('ranked_in_band') or [])} "
      f"out_of_band={len(o4.get('ranked_out_of_band') or [])}")
check("the out-of-band long candidate is retained",
      any(c["bvid"] == "BVlong" for c in allrows))

print()
print("=" * 78)
bad = [c for c in CHECKS if not c[1]]
print(f"{len(CHECKS) - len(bad)}/{len(CHECKS)} checks passed")
sys.exit(1 if bad else 0)
