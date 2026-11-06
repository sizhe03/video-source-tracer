"""The v6 review's counterexamples, as permanent regression cases.

Each one failed on v6. They exist so the same defect cannot come back quietly.
"""
import io
import json
import os
import sys
import wave
from contextlib import redirect_stdout

import numpy as np

import _paths  # noqa: F401

import _paths  # noqa: E402
import auprint
import find_earlier as F
import _common

_common.throttle = lambda host, gap=None: None
F.throttle = _common.throttle

D = _paths.OUTDIR
os.makedirs(D, exist_ok=True)

R = []


def check(name, cond, detail=""):
    R.append((name, cond, detail))
    print(("PASS " if cond else "FAIL ") + name)
    if not cond and detail:
        print(f"     {detail}")


def wav(name, data):
    p = os.path.join(D, name)
    data = np.clip(np.asarray(data, dtype=float), -1, 1)
    with wave.open(p, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(auprint.SR)
        f.writeframes((data * 32000).astype(np.int16).tobytes())
    return p


def run_main(argv):
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = io.StringIO()
    try:
        with redirect_stdout(buf):
            F.main(argv)
    finally:
        sys.stderr = old
    return json.loads(buf.getvalue())


# ---------------------------------------------------------------- 1
print("-- 1. struct_only must become a comment-reading task ----------------")
F.bili_json = lambda u, headers=None, retries=2: {"code": 0, "data": {"result": [
    {"bvid": "BVstruct", "title": "completely unrelated woodworking",
     "author": "wood", "mid": 7, "pubdate": 1700000000,
     "duration": "20:00", "play": 3}]}}
F.bili_meta = lambda bv: {
    "platform": "bilibili", "id": bv, "title": "some title",
    "pubdate": 1790000000, "duration": 600, "author": "orig", "mid": 1,
    "views": 1, "resolution": "1920x1080", "cover": ""}
out = run_main(["BVref"])
check("comment_candidates is emitted", "comment_candidates" in out,
      str(list(out)))
check("next_action is inspect_comments_and_credits",
      out.get("next_action") == "inspect_comments_and_credits",
      out.get("next_action"))
check("the candidate is queued for comments",
      any(c["id"] == "BVstruct" for c in out.get("comment_candidates") or []),
      str(out.get("comment_candidates")))
check("the next text points at the per-platform reader",
      "read_with" in (out.get("next") or ""), out.get("next"))
check("must_not_drop includes the struct_only candidate",
      "BVstruct" in (out.get("must_not_drop") or []),
      str(out.get("must_not_drop")))

# ---------------------------------------------------------------- 2
print("-- 2. Chinese generic words must not survive as fragments -----------")
from _common import segments, tokens  # noqa: E402

check("segments never span a space",
      segments("ASMR 助眠") == [("latin", "asmr"), ("cjk", "助眠")],
      str(segments("ASMR 助眠")))
check("tokens contain no cross-space bigram",
      "asmr助" not in tokens("ASMR 助眠"), str(tokens("ASMR 助眠")))
check("'ASMR 助眠' yields no distinctive terms",
      F._distinctive_terms("ASMR 助眠") == set(),
      str(F._distinctive_terms("ASMR 助眠")))
check("'麦克风' yields no distinctive terms",
      F._distinctive_terms("麦克风") == set(),
      str(F._distinctive_terms("麦克风")))
check("a real title still yields distinctive terms",
      len(F._distinctive_terms("白丝吊带抹茶")) > 0,
      str(F._distinctive_terms("白丝吊带抹茶")))

ref = {"platform": "bilibili", "id": "ref", "title": "ASMR 助眠",
       "title_is_query": True, "has_rank": False,
       "query_terms": F._terms("ASMR 助眠"), "pubdate": 0, "duration": 600,
       "author": None, "resolution": None}
sc = F.score({"id": "BVx", "title": "ASMR 助眠 摩擦 窗玻璃", "pubdate": 0,
              "duration": 610, "author": "x", "resolution": None}, ref)
check("a generic query earns no title credit",
      "title_near_identical" not in sc["signals"], str(sc["signals"]))
check("and does not reach examine_first",
      sc["bucket"] != "examine_first", sc["bucket"])

# ---------------------------------------------------------------- 3
print("-- 3. band-limited noise must not be called tonal -------------------")
rng = np.random.default_rng(77)
n = rng.normal(0, 0.2, auprint.SR * 12)
spec = np.fft.rfft(n)
freqs = np.fft.rfftfreq(len(n), 1 / auprint.SR)
spec[freqs > 1500] = 0
band = np.fft.irfft(spec, len(n))
band = band / (np.abs(band).max() + 1e-9) * 0.6
pb = wav("v6_band.wav", band)
res = auprint.match(pb, pb)
check("lowpass-1500Hz noise matches itself",
      res["verdict"] == "same_audio_segment", res["verdict"])
check("its tonal score stays below the cutoff",
      max(res.get("tonal_ratio_a") or 0, res.get("tonal_ratio_b") or 0)
      < auprint.TONAL_CUTOFF,
      f"{res.get('tonal_ratio_a')} vs {auprint.TONAL_CUTOFF}")

t = np.arange(auprint.SR * 12) / auprint.SR
chord = sum(np.sin(2 * np.pi * f * t)
            for f in (220, 440, 660, 880, 1320, 1760)) / 6
pc = wav("v6_chord.wav", chord)
check("a six-tone chord still reads as tonal",
      auprint._tonal_ratio(np.asarray(chord)) >= auprint.TONAL_CUTOFF,
      str(auprint._tonal_ratio(np.asarray(chord))))

# ---------------------------------------------------------------- 4
print("-- 4. `proves` must follow the verdict ------------------------------")
a = wav("v6_a.wav", np.random.default_rng(1).normal(0, .2, auprint.SR * 10))
b = wav("v6_b.wav", np.random.default_rng(2).normal(0, .2, auprint.SR * 10))
r2 = auprint.match(a, b)
check("insufficient_evidence carries an empty `proves`",
      r2["verdict"] != "insufficient_evidence" or r2.get("proves") == "",
      f"{r2['verdict']} / {r2.get('proves')!r}")
check("the caveat lives in its own field",
      "does_not_prove" in r2 and "same video" in r2["does_not_prove"])
check("a positive verdict still states what it proves",
      auprint._proves("same_audio_segment") != ""
      and auprint._proves("ambiguous_alignment") != "")

# ---------------------------------------------------------------- 5
print("-- 5. a flat plateau is ONE alignment -------------------------------")
flat = {i: 100 for i in range(50)}
peaks = auprint.enumerate_alignments(flat, auprint.CLUSTER_RADIUS,
                                     auprint.MIN_ALIGNMENT_VOTES)
check("50 flat bins collapse to a single peak", len(peaks) == 1, str(peaks))
two = auprint.enumerate_alignments({0: 500, 30: 500}, auprint.CLUSTER_RADIUS,
                                   auprint.MIN_ALIGNMENT_VOTES)
check("two distant lobes stay two peaks", len(two) == 2, str(two))
stair = auprint.enumerate_alignments({i: 1000 - i * 40 for i in range(20)},
                                     auprint.CLUSTER_RADIUS,
                                     auprint.MIN_ALIGNMENT_VOTES)
check("a staircase is not many peaks", len(stair) == 1, str(stair))

# ---------------------------------------------------------------- 6
print("-- 6. the legend must not claim a content match ---------------------")
ref2 = {"platform": "bilibili", "id": "ref", "title": "some title",
        "title_is_query": True, "has_rank": False,
        "query_terms": F._terms("some title"), "pubdate": 1790000000,
        "duration": 600, "author": None, "resolution": None}
sc2 = F.score({"id": "BVlong", "title": "completely unrelated woodworking",
               "pubdate": 1700000000, "duration": 1200, "author": "wood",
               "resolution": None}, ref2)
check("earlier+longer with no title evidence says so",
      any("content NOT established" in r for r in sc2["reasons"]),
      str(sc2["reasons"]))
check("it is still kept for checking",
      sc2["bucket"] in ("worth_checking", "struct_only"), sc2["bucket"])

print()
bad = [r for r in R if not r[1]]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
