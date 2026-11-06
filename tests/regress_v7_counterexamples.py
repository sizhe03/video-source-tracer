"""The v7 review's counterexamples, as permanent regression cases.

Each one failed on v7. They exist so the same defect cannot come back quietly.
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
import authorcheck
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


def run(argv):
    buf, old = io.StringIO(), sys.stderr
    sys.stderr = io.StringIO()
    try:
        with redirect_stdout(buf):
            F.main(argv)
    finally:
        sys.stderr = old
    return json.loads(buf.getvalue())


def wav(name, data):
    p = os.path.join(D, name)
    data = np.clip(np.asarray(data, dtype=float), -1, 1)
    with wave.open(p, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(auprint.SR)
        f.writeframes((data * 32000).astype(np.int16).tobytes())
    return p


BILI_META = lambda bv: {                                  # noqa: E731
    "platform": "bilibili", "id": bv, "title": "identical title",
    "pubdate": 0, "duration": 600, "author": "orig", "mid": 1,
    "views": 1, "resolution": "1920x1080", "cover": ""}


# ---------------------------------------------------------------- 1
print("-- 1. title similarity is NOT a content verdict ---------------------")
F.bili_meta = BILI_META
F.bili_json = lambda u, headers=None, retries=2: {"code": 0, "data": {"result": [
    {"bvid": "BVtitle", "title": "identical title", "author": "a", "mid": 1,
     "pubdate": 0, "duration": "10:00", "play": 1},
    {"bvid": "BVstruct", "title": "completely unrelated woodworking",
     "author": "b", "mid": 2, "pubdate": 1700000000, "duration": "20:00",
     "play": 1}]}}
o = run(["BVref"])
check("no field named content_established",
      "content_established" not in o, str(sorted(o)))
check("title_supported_candidates names what it is",
      "title_supported_candidates" in o, str(sorted(o)))
check("content_match_state is not_checked",
      o.get("content_match_state") == "not_checked", o.get("content_match_state"))
check("a title-supported candidate also has a comment task",
      "BVtitle" in {c["id"] for c in o.get("comment_candidates") or []},
      str([c["id"] for c in o.get("comment_candidates") or []]))
check("the legend does not claim a content match",
      "content match was established" not in o["bucket_legend"]["worth_checking"],
      o["bucket_legend"]["worth_checking"])

# ---------------------------------------------------------------- 2
print("-- 2. a YouTube candidate gets a YouTube reader ---------------------")
F.yt_search = lambda q, limit=40: [
    {"platform": "youtube", "search_rank": 0, "id": "abcdefghijk",
     "title": "completely unrelated", "pubdate": 1700000000, "duration": 1200,
     "author": "chan", "mid": "UCx", "views": 1, "resolution": None}]
o2 = run(["--yt", "some query", "--duration", "600", "seed"])
queue = o2.get("comment_candidates") or []
check("the queue carries a platform", bool(queue) and queue[0].get("platform") == "youtube",
      str(queue))
check("the queue carries a full URL",
      bool(queue) and (queue[0].get("url") or "").startswith("https://www.youtube.com/"),
      str(queue[0].get("url") if queue else None))
check("the reader is not comments.py for youtube",
      bool(queue) and "comments.py" not in (queue[0].get("read_with") or ""),
      str(queue[0].get("read_with") if queue else None))
check("the next text does not tell you to run comments.py on a youtube id",
      "comments.py" not in (o2.get("next") or ""), o2.get("next"))

# bilibili still routes to comments.py. Needs a candidate that lands in
# struct_only, i.e. earlier AND longer than the reference, with no title overlap.
F.bili_json = lambda u, headers=None, retries=2: {"code": 0, "data": {"result": [
    {"bvid": "BVstruct2", "title": "completely unrelated woodworking",
     "author": "b", "mid": 2, "pubdate": 1700000000, "duration": "20:00",
     "play": 1}]}}
F.bili_meta = lambda bv: {
    "platform": "bilibili", "id": bv, "title": "identical title",
    "pubdate": 1790000000, "duration": 600, "author": "orig", "mid": 1,
    "views": 1, "resolution": "1920x1080", "cover": ""}
o2b = run(["BVref"])
qb = o2b.get("comment_candidates") or []
check("bilibili still routes to comments.py",
      bool(qb) and qb[0].get("read_with") == "scripts/comments.py",
      str(qb[0] if qb else None))

# ---------------------------------------------------------------- 3
print("-- 3. empty result must not say 'verify' ----------------------------")
F.bili_json = lambda u, headers=None, retries=2: {"code": 0, "data": {"result": []}}
o3 = run(["BVref"])
check("empty result widens the query set",
      o3.get("next_action") == "widen_query_set", o3.get("next_action"))
check("the sentence agrees with the action",
      "verify" not in (o3.get("next") or "").lower(), o3.get("next"))
check("no candidates are listed", not (o3.get("candidates") or []),
      str(o3.get("candidates")))

# ---------------------------------------------------------------- 4
print("-- 4. peak merge must not swallow separated peaks -------------------")
hist = {i: 40 for i in range(51)}
hist[5] += 1000
hist[45] += 1000


def wsum(c):
    return sum(v for d, v in hist.items() if abs(d - c) <= auprint.CLUSTER_RADIUS)


peaks = auprint.enumerate_alignments(hist, auprint.CLUSTER_RADIUS,
                                     auprint.MIN_ALIGNMENT_VOTES)
check("two separated lobes stay two peaks", len(peaks) == 2, str(peaks))
check("every reported vote count equals its own window",
      all(v == wsum(c) for c, v in peaks),
      str([(c, v, wsum(c)) for c, v in peaks]))
check("neither peak sits in the valley",
      all(abs(c - 25) > auprint.CLUSTER_RADIUS for c, _v in peaks), str(peaks))

# ---------------------------------------------------------------- 5
print("-- 5. band-limited noise is not tonal -------------------------------")
rng = np.random.default_rng(5)
base = rng.normal(0, 0.2, auprint.SR * 12)
freqs = np.fft.rfftfreq(len(base), 1 / auprint.SR)
for cut in (200, 400, 500, 700, 1500):
    spec = np.fft.rfft(base)
    spec[freqs > cut] = 0
    sig = np.fft.irfft(spec, len(base))
    t = auprint._tonal_ratio(sig)
    check(f"lowpass {cut} Hz is not called tonal",
          t < auprint.TONAL_CUTOFF, f"tonal={t:.3f}")
    prof = auprint._spectral_profile(sig)
    check(f"lowpass {cut} Hz reports a filled band",
          (prof.get("fill") or 0) > 0.9, str(prof))

t_ax = np.arange(auprint.SR * 12) / auprint.SR
chord = sum(np.sin(2 * np.pi * f * t_ax)
            for f in (220, 440, 660, 880, 1320, 1760)) / 6
check("a six-tone chord is still tonal",
      auprint._tonal_ratio(chord) >= auprint.TONAL_CUTOFF,
      str(auprint._tonal_ratio(chord)))

# a 400 Hz lowpassed noise must actually MATCH ITSELF
spec = np.fft.rfft(base)
spec[freqs > 400] = 0
sig = np.fft.irfft(spec, len(base))
p400 = wav("v7_lp400.wav", sig / (np.abs(sig).max() + 1e-9) * 0.6)
res = auprint.match(p400, p400)
check("400 Hz lowpassed noise matches itself",
      res["verdict"] == "same_audio_segment", res["verdict"])

# ---------------------------------------------------------------- 6
print("-- 6. relevance vocabulary and structured attribution ---------------")
check("no distinctive overlap is 'no_overlap', not 'not_checked'",
      authorcheck._relevance(["ASMR 助眠 摩擦 麦克风"],
                             ["ASMR 助眠 摩擦 窗玻璃"])["verdict"] == "no_overlap")
check("no_overlap is never 'contradicts'",
      authorcheck._relevance(["ASMR 助眠"], ["完全无关"])["verdict"]
      != "contradicts")
bare = authorcheck._evidence("ch", "UC1", ["ASMR"], None, None, [],
                             linked_by="https://example.com/x")
check("a bare string cannot grant attribution",
      bare["attribution"]["verdict"] == "unverifiable",
      str(bare["attribution"]))
full = authorcheck._evidence("ch", "UC1", ["ASMR"], None, None, [],
                             input_work_id="input-clip",
                             linked_by={"url": "https://example.com/work/BV1", "work_id": "BV1",
                                        "account_id": "UC1", "input_work_id": "input-clip",
                                        "basis": "inspected creator credit linking this input to BV1",
                                        "evidence_type": "creator_credit", "verified": True,
                                        "evidence_quote": "Created by ch; original work BV1",
                                        "checked_at": "2026-11-05"})
check("a complete structured link does grant attribution",
      full["attribution"]["verdict"] == "supports", str(full["attribution"]))

print()
bad = [r for r in R if not r[1]]
print(f"{len(R) - len(bad)}/{len(R)} checks passed")
sys.exit(1 if bad else 0)
