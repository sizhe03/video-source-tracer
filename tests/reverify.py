"""Independent assertions about the FIXED behaviour.

Each check names the v4 defect it guards. A reviewer can read this file as the
contract and re-run it as the proof.
"""
import os
import sys

import _paths
import authorcheck
import comments
import find_earlier
import auprint

R = []


def check(name, cond, detail=""):
    R.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(f"     {detail}")


def code_only(path):
    """Source with comments stripped, so historical notes do not trip a check."""
    out = []
    for line in open(path, encoding="utf-8").read().splitlines():
        s = line.split("#", 1)[0]
        if s.strip():
            out.append(s)
    return "\n".join(out)


# --- 1. the status-string contract is gone ---------------------------------
c_src, a_src, ac_src = code_only("comments.py"), code_only("alias_resolve.py"), \
    code_only("authorcheck.py")
check("callers no longer compare a status string",
      '"genre_matches"' not in c_src and '"genre_matches"' not in a_src,
      "a status value _stage() can never return was gating findings")
check("authorcheck publishes worth_pursuing()", "def worth_pursuing(" in ac_src)

# --- 2. leads survive the name cap -----------------------------------------
th = [{"rpid": i, "user": f"u{i}", "text": f"\u8fd9\u662f\u540d\u5b57{i}\u53f7\u7684\u4f5c\u54c1",
       "like": 1, "rcount": 0, "nested": []} for i in range(20)]
th.append({"rpid": 99, "user": "a",
           "text": "\u539f\u7247\u662f https://www.youtube.com/@KanraRamu \u8fd9\u4e2a\u9891\u9053",
           "like": 90, "rcount": 0, "nested": []})
sel = comments.resolve_candidates(th, 15)
check("a URL lead outlives candidates()[:top]",
      len([c for c in sel if c.get("is_lead")]) == 2,
      f"selected {len(sel)} rows")

# --- 3. genre gate uses word boundaries ------------------------------------
check("'ear' does not match inside 'learn'",
      authorcheck._genre_ok(["Learn woodworking with hand tools"]) is False)
check("genuine ASMR vocabulary still passes",
      authorcheck._genre_ok(["ASMR \u592a\u3082\u3082"]) is True)
check("CJK genre words still pass",
      authorcheck._genre_ok(["\u52a9\u7720 \u6469\u64e6"]) is True)

# --- 4. sub-replies paginate ------------------------------------------------
pages = []


def fake(url, headers=None, retries=2):
    if "reply/reply" in url:
        pn = int(url.split("pn=")[1].split("&")[0])
        pages.append(pn)
        rows = (["r"] * 20 if pn == 1 else ["r"] * 5 if pn == 2 else [])
        return {"code": 0, "data": {
            "replies": [{"rpid": pn * 100 + i, "member": {"uname": "u"},
                         "content": {"message": "\u7518\u6a02"}, "like": 0}
                        for i in range(len(rows))],
            "page": {"count": 25}}}
    return {"code": 0, "data": {
        "cursor": {"all_count": 40, "is_end": True, "next": 0},
        "replies": [{"rpid": 1, "member": {"uname": "op"},
                     "content": {"message": "\u8fd9\u662f\u54ea\u4f4d\u554a"},
                     "like": 0, "rcount": 25}]}}


comments.bili_json = fake
got, _m = comments.fetch_comments(1)
check("nested replies page past pn=1",
      len(got[0]["nested"]) >= 25 and pages == [1, 2],
      f"collected={len(got[0]['nested'])} pages={pages}")

# --- 5. rank alone cannot make a candidate actionable -----------------------
F = find_earlier
F.throttle = lambda h, g=None: None
ref = {"platform": "bilibili", "id": "ref", "title": "\u67d0\u6807\u9898",
       "title_is_query": True, "has_rank": False,
       "query_terms": F._terms("\u67d0\u6807\u9898"), "pubdate": 0,
       "duration": 662, "author": None, "resolution": None}
sc = F.score({"id": "BVx", "title": "\u5b8c\u5168\u65e0\u5173\u7684\u6728\u5de5\u6559\u5b66",
              "pubdate": 0, "duration": 660, "author": "x",
              "resolution": None}, ref)
check("an unrelated candidate is not actionable", sc["bucket"] == "unlikely",
      str(sc))

# --- 6. auprint's decision structure ---------------------------------------
ap_src = open("auprint.py", encoding="utf-8").read()
check("ambiguous results have their own verdict",
      'verdict = "ambiguous_alignment"' in ap_src)
check("the winning offset is the best WINDOW over the whole axis",
      "def best_lag(" in ap_src and "max(range(lo, hi + 1)" in ap_src,
      "a zero-vote window centre must be reachable")
check("alignments are not capped at four",
      "if len(peaks) >= 4" not in ap_src,
      "a cap made nine repetitions look like 'no match'")
check("MIN_TIME_COVER participates in the decision",
      "time_cover < MIN_TIME_COVER" in ap_src)
check("coverage cannot exceed 1.0", "min(1.0, max(cover_a, cover_b))" in ap_src)

# --- 7. docs and code agree ----------------------------------------------
skill = open(os.path.join("..", "SKILL.md"), encoding="utf-8").read()
check("the report template no longer claims an L1-L4 ladder",
      "\u8bc1\u636e\u7b49\u7ea7\uff1aL1" not in skill)

print()
bad = [r for r in R if not r[1]]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
