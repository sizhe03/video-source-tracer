"""The v5 review's six counterexamples, as permanent regression cases.

Every one of these failed on v5. They are here so the same defect cannot return
silently: a future change that reintroduces it turns this file red.
"""
import os
import sys

import _paths
import authorcheck
import auprint
import find_earlier as F

R = []


def check(name, cond, detail=""):
    R.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(f"     {detail}")


# ---------------------------------------------------------------- 1
# Repeated content must NOT be judged "no_match", at ANY repetition count.
print("-- 1. repetition count must not flip the verdict --------------------")
period = os.path.join(_paths.OUTDIR, "rep_period.wav")
import wave  # noqa: E402
import numpy as np  # noqa: E402


def _room(seconds, seed):
    r = np.random.default_rng(seed)
    n = int(auprint.SR * seconds)
    x = r.normal(0, 0.10, n)
    for k in range(0, n, auprint.SR // 4):
        x[k:k + auprint.SR // 4] *= r.uniform(0.4, 1.0)
    for t in r.uniform(0.2, max(0.3, seconds - 0.3), max(1, int(seconds * 1.7))):
        i = int(t * auprint.SR)
        L = int(auprint.SR * r.uniform(0.004, 0.02))
        b = r.normal(0, 0.55, L) * np.exp(-np.linspace(0, 6, L))
        x[i:i + L] += b[:max(0, min(n - i, L))]
    return x


def _wav(name, data):
    p = os.path.join(_paths.OUTDIR, name)
    data = np.clip(np.asarray(data, dtype=float), -1, 1)
    with wave.open(p, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(auprint.SR)
        f.writeframes((data * 32000).astype(np.int16).tobytes())
    return p


one = _wav("rep_one.wav", _room(auprint.HOP * 468 / auprint.SR, 5))
period_data = _room(auprint.HOP * 468 / auprint.SR, 5)   # identical material
for n in (3, 9):
    p = _wav(f"rep_{n}.wav", np.tile(period_data, n))
    r = auprint.match(one, p)
    check(f"{n}x repetition -> ambiguous_alignment",
          r["verdict"] == "ambiguous_alignment",
          f"got {r['verdict']} (mass_share={r.get('peak_mass_share')}, "
          f"alignments={len(r.get('alignments') or [])})")

# ---------------------------------------------------------------- 2
# The best window centre may carry zero votes.
print("-- 2. best window may be centred on an empty bin --------------------")
hist = {0: 100, 6: 100}
chosen = auprint.best_lag(hist, auprint.CLUSTER_RADIUS)
check("best_lag finds the empty centre",
      auprint._window_sum(hist, chosen, auprint.CLUSTER_RADIUS) == 200,
      f"chose {chosen} with window "
      f"{auprint._window_sum(hist, chosen, auprint.CLUSTER_RADIUS)}")

# ---------------------------------------------------------------- 3
# Shared title vocabulary must never become attribution.
print("-- 3. shared words are relevance, not attribution -------------------")
authorcheck._yt_search = lambda q, n=20: [
    {"channel": "ASMR小屋", "channel_id": "UCother",
     "title": "ASMR 助眠 摩擦 窗玻璃"}]
res = authorcheck.verify_youtube("ASMR", ["ASMR 助眠 摩擦 麦克风"])
for c in res["candidates"]:
    ev = c["evidence"]
    check("attribution stays unverifiable on shared words",
          ev["attribution"]["verdict"] != "supports",
          f"{ev['attribution']}")
    check("status is not 'attributed'", c["status"] != "attributed",
          f"status={c['status']}")
    check("the shared words are recorded as relevance",
          ev["relevance"]["verdict"] in ("supports", "not_checked", "no_overlap"),
          f"{ev['relevance']}")
    check("no reason claims a link to the clip",
          not any("link to this clip" in r for r in c["pursue_reasons"]),
          str(c["pursue_reasons"]))

# ---------------------------------------------------------------- 4
# A generic query word cannot buy title credit.
print("-- 4. generic query term gives no title credit ----------------------")
ref = {"platform": "bilibili", "id": "ref", "title": "ASMR", "title_is_query": True,
       "has_rank": False, "query_terms": F._terms("ASMR"), "pubdate": 0,
       "duration": 662, "author": None, "resolution": None}
sc = F.score({"id": "BVx", "title": "ASMR 助眠 摩擦 窗玻璃", "pubdate": 0,
              "duration": 660, "author": "x", "resolution": None}, ref)
check("generic-only overlap is not title_near_identical",
      "title_near_identical" not in sc["signals"], str(sc["signals"]))
check("generic-only overlap is not actionable",
      sc["bucket"] in ("unlikely", "rank_only", "struct_only"), sc["bucket"])

# ---------------------------------------------------------------- 5
# Structural signals alone must not bypass the content check.
print("-- 5. n_ind alone cannot reach worth_checking -----------------------")
ref2 = {"platform": "youtube", "id": "ref", "title": "太もも マイク",
        "title_is_query": True, "has_rank": True,
        "query_terms": F._terms("太もも マイク"), "pubdate": 0, "duration": 662,
        "author": None, "resolution": None}
sc2 = F.score({"id": "vid0", "title": "completely unrelated woodworking",
               "search_rank": 0, "pubdate": 0, "duration": 800,
               "author": "x", "resolution": None}, ref2)
check("unrelated title + rank 1 + longer is NOT worth_checking",
      sc2["bucket"] != "worth_checking", f"bucket={sc2['bucket']}")
check("it is recorded as struct_only instead",
      sc2["bucket"] == "struct_only", f"bucket={sc2['bucket']}")

# ---------------------------------------------------------------- 6
# A same-name same-genre account must not stop the alias ladder.
print("-- 6. same-name same-genre account does not end the ladder ----------")
calls = []


def fake(q, n=20):
    calls.append(q)
    if q.startswith("甘樂"):
        return [{"channel": "甘樂ASMR", "channel_id": "UCwrong",
                 "title": "ASMR 助眠 摩擦"}]
    return [{"channel": "甘楽らむ", "channel_id": "UCright",
             "title": "ASMR 太もも マイク"}]


authorcheck._yt_search = fake
res2 = authorcheck.verify_youtube("甘樂")
check("the ladder walks the spelling variants",
      len(calls) >= 5, f"only {len(calls)} queries: {calls}")
check("both the decoy and the real account are reported",
      len(res2["candidates"]) >= 2,
      str([(c["channel"], c["status"]) for c in res2["candidates"]]))

print()
bad = [r for r in R if not r[1]]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
