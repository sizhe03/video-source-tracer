"""Regression fixtures for auprint: one case per behaviour the review named.

Every case carries its expected verdict; the script exits non-zero on any
mismatch, so "all pass" is a claim the reviewer can falsify in one command.
Fully offline: the audio is synthesised, no network, no external media.
"""
import sys
import wave

import numpy as np

import _paths
from _paths import out as _out

import auprint

SR, HOP = auprint.SR, auprint.HOP


def w(name, x):
    p = _out(name)
    x = np.clip(np.asarray(x, dtype=float), -1, 1)
    with wave.open(p, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((x * 32000).astype(np.int16).tobytes())
    return p


def room(seconds, seed):
    """Transient-rich broadband material: the shape a real recording has."""
    r = np.random.default_rng(seed)
    n = int(SR * seconds)
    x = r.normal(0, 0.10, n)
    for k in range(0, n, SR // 4):
        x[k:k + SR // 4] *= r.uniform(0.4, 1.0)
    for t in r.uniform(0.2, max(0.3, seconds - 0.3), max(1, int(seconds * 1.7))):
        i = int(t * SR)
        L = int(SR * r.uniform(0.004, 0.02))
        burst = r.normal(0, 0.55, L) * np.exp(-np.linspace(0, 6, L))
        x[i:i + L] += burst[:max(0, min(L, n - i))]
    return x


def tones(seconds, freqs, phase=0.0):
    t = np.arange(int(SR * seconds)) / SR
    return sum(np.sin(2 * np.pi * f * t + phase) for f in freqs) / len(freqs)


master = room(30, 11)
# an exact hop multiple, so repeated copies align on the frame grid
one_period = room(HOP * 468 / SR, 5)

CASES = []


def case(name, pa, pb, expect, why):
    r = auprint.match(pa, pb)
    CASES.append((name, expect, r["verdict"], r, why))


# 1 the canonical positive: a crop of the same recording
case("crop 10-20s of a 30s master",
     w("m30.wav", master), w("crop.wav", master[10 * SR:20 * SR]),
     "same_audio_segment",
     "one lag window dominates; this must stay a positive")

# 2 repeated content: several equally valid offsets
case("master vs three exact copies",
     w("p1.wav", one_period), w("p3.wav", np.tile(one_period, 3)),
     "ambiguous_alignment",
     "three alignments tie; reporting no_match here was the review's case")

# 3 unrelated material
case("independent recording",
     w("m30b.wav", master), w("other.wav", room(30, 99)),
     "insufficient_evidence",
     "no shared landmarks: not enough to speak, not proof of sameness")

# 4 sustained tones cannot be judged by this method
case("six-tone vs six-tone (phase shifted)",
     w("t6a.wav", tones(30, (220, 440, 660, 880, 1320, 1760))),
     w("t6b.wav", tones(30, (220, 440, 660, 880, 1320, 1760), 1.3)),
     "incomparable_tonal_input",
     "must NOT claim a match, and must NOT claim 'different' either: "
     "this method cannot separate same-tone from same-source")

# 5 too short for a stable histogram
case("2s clip vs itself",
     w("s2a.wav", room(2, 21)), w("s2b.wav", room(2, 21)),
     "insufficient_evidence",
     "below MIN_ANALYSED_SECONDS")

# 6 five seconds of independent noise (the review's 1-vote false positive)
case("5s noise vs unrelated 5s noise",
     w("n5a.wav", np.random.default_rng(31).normal(0, .2, SR * 5)),
     w("n5b.wav", np.random.default_rng(32).normal(0, .2, SR * 5)),
     "insufficient_evidence",
     "one coincidence vote must never pass")

# 7 mild time stretch: honest answer is insufficient, not a guess
stretched = np.interp(np.arange(0, len(master), 1.01),
                      np.arange(len(master)), master)
case("1% time-stretched copy",
     w("str_a.wav", master), w("str_b.wav", stretched),
     "insufficient_evidence",
     "one global lag cannot absorb accumulating drift")

print(f"{'case':44s} {'expected':22s} {'got':22s} ok")
print("-" * 100)
fails = 0
for name, exp, got, r, why in CASES:
    ok = exp == got
    print(f"{name:44s} {exp:22s} {got:22s} {'PASS' if ok else 'FAIL'}")
    if not ok:
        fails += 1
        print(f"    why it matters: {why}")
        print(f"    cluster={r.get('cluster_votes')} rival={r.get('rival_lag_votes')} "
              f"prom={r.get('prominence')} cover={r.get('time_coverage')} "
              f"mass={r.get('peak_mass_share')}")
        print(f"    reason: {r.get('verdict_reason')}")
        print(f"    alignments: {r.get('alignments')}")

print()
print(f"{len(CASES) - fails}/{len(CASES)} passed")
sys.exit(1 if fails else 0)
