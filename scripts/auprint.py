#!/usr/bin/env python
"""Experimental audio landmark alignment; not creator or video identity proof.

Usage: auprint.py A.wav B.wav
       auprint.py --video A.mp4 --video B.mp4
Synthetic regression coverage does not establish real-world accuracy.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections import Counter

from _common import die, emit, ffmpeg, fmt_dur, note, setup_stdio

try:
    import numpy as np
except Exception:  # noqa: BLE001
    die("numpy is required")

SR = 8000           # landmarks survive nicely at 8 kHz and it is 5x faster
WIN = 2048
HOP = 512
PEAKS_PER_FRAME = 5     # fewer peaks, because (f1,f2,dt) multiplies keys
FANOUT = 5              # how many frames ahead a partner peak may sit
# Experimental thresholds; historical tuning notes below are not validation.
#
# Raw `modal_share` is useless here: dense ambient audio (fans, fabric, breath)
# produces millions of hash collisions, so a true match still only holds ~2% of
# the total. What separates a real match is *concentration*: the votes pile into
# a handful of adjacent lag bins instead of scattering.
#
#   positive control (re-encoded crop of the same master):
#       cluster 24,947 votes, cluster_share 0.019, modal/total 1.9%
#   same-camera-take but different audio mix (the B站 re-cut):
#       cluster    416 votes, cluster_share 0.0003
#
# => ~60x separation. Thresholds sit in the middle.
CLUSTER_RADIUS = 3      # +-3 bins == +-0.19 s
MIN_CLUSTER_VOTES = 6000        # only applied to clips long enough to accrue them
MIN_CLUSTER_SHARE = 0.010
MIN_VOTES_PER_FRAME = 0.02      # length-normalised: votes / analysed frames
TONAL_CUTOFF = 0.55            # max(top-six amplitude share, narrow-peak share)
LONG_ENOUGH_SECONDS = 60        # below this, absolute vote counts are meaningless

# Evidence-adequacy floors. These apply at EVERY duration, which is the fix for
# "a 5 s pair scored 1 vote and passed": the old floors were gated behind
# `analysed >= 10`, so short clips skipped them entirely.
MIN_CLUSTER_ABSOLUTE = 12      # below this, a "match" is coincidental
MIN_HASH_YIELD = 0.02          # matched distinct hashes / total hashes
MIN_TIME_COVER = 0.05          # matched time points / analysed frames
MIN_LAG_DOMINANCE = 3.0        # deprecated: replaced by MIN_PEAK_RATIO
# Below this best/mean ratio the vote distribution has no usable peak. Chosen
# just under the observed true-match value (2.1) so that real matches are still
# evaluated, while flagging that the margin is thin. This is a competence
# boundary, not a calibrated detector.
MIN_PEAK_RATIO = 1.8            # deprecated
# Calibrated between the measured classes: true match 2.42, unrelated 1.02.
MIN_PROMINENCE = 1.6
# A lag histogram needs enough time to be stable at all. Below this the analysis
# window is shorter than the spectral window's useful accumulation.
MIN_ANALYSED_SECONDS = 8.0

# --- ambiguity ---------------------------------------------------------------
# Two alignments count as "comparable" when the runner-up holds at least this
# fraction of the winner's votes. 0.40 is a judgement call on one fixture set,
# not a calibrated constant.
AMBIGUITY_RATIO = 0.40
# A window must hold at least this many votes before it may pose as a second
# alignment. Without a floor, a wide smear of accidental collisions counts as
# "another alignment" and every dense recording looks ambiguous.
MIN_ALIGNMENT_VOTES = 200


def _window_sum(hist: dict[int, int], c: int, radius: int) -> int:
    return sum(v for d, v in hist.items() if abs(d - c) <= radius)


def best_lag(hist: dict[int, int], radius: int) -> int:
    """Centre of the highest-sum window over the WHOLE offset axis.

    The centre does not have to carry votes. Two lobes six bins apart with an
    empty middle put the best centre on a zero-vote bin; taking
    `max(hist, key=...)` could never reach it.
    """
    if not hist:
        return 0
    lo, hi = min(hist) - radius, max(hist) + radius
    return max(range(lo, hi + 1), key=lambda c: _window_sum(hist, c, radius))


def _peak_windows(hist: dict[int, int], radius: int, min_votes: int):
    """Return true local maxima, retaining equal-height plateau intervals."""
    if not hist:
        return {}, []
    lo, hi = min(hist) - radius, max(hist) + radius
    # Sliding sum avoids rescanning every histogram bin for every centre.
    value = _window_sum(hist, lo, radius)
    vals = {lo: value}
    for c in range(lo + 1, hi + 1):
        value += hist.get(c + radius, 0) - hist.get(c - radius - 1, 0)
        vals[c] = value
    peaks = []
    start = lo
    while start <= hi:
        end = start
        while end < hi and vals[end + 1] == vals[start]:
            end += 1
        v = vals[start]
        if (v >= min_votes and v > vals.get(start - 1, -1)
                and v > vals.get(end + 1, -1)):
            peaks.append({"centre": (start + end + 1) // 2, "votes": v,
                          "start": start, "end": end})
        start = end + 1
    peaks.sort(key=lambda p: (-p["votes"], p["centre"]))
    return vals, peaks


def enumerate_alignments(hist: dict[int, int], radius: int,
                         min_votes: int) -> list[tuple[int, int]]:
    """Display distinct peaks; never use this merged list to erase rivals."""
    vals, raw = _peak_windows(hist, radius, min_votes)
    kept = []
    for p in raw:
        c, v = p["centre"], p["votes"]
        conflict = False
        for kc, kv in kept:
            between = [vals[x] for x in range(min(c, kc) + 1, max(c, kc))]
            if abs(c - kc) <= radius * 2 or (between and min(between) > 0.85 * min(v, kv)):
                conflict = True
                break
        if not conflict:
            kept.append((c, v))
    return kept


def alignment_evidence(hist: dict[int, int], radius: int) -> dict:
    """Separate display grouping from localization and raw competing evidence.

    95%-height width and the seven-bin localization limit are conservative
    heuristics, not calibrated confidence. A broad/shallow shape stays undecided.
    """
    vals, raw = _peak_windows(hist, radius, MIN_ALIGNMENT_VOTES)
    lag = raw[0]["centre"] if raw else best_lag(hist, radius)
    height = vals.get(lag, 0)
    left = right = lag
    while vals.get(left - 1, -1) >= height * .95:
        left -= 1
    while vals.get(right + 1, -1) >= height * .95:
        right += 1
    rival = max((p["votes"] for p in raw if abs(p["centre"] - lag) > radius * 2), default=0)
    return {"lag": lag, "raw_peaks": raw, "rival_votes": rival,
            "width_bins": right - left + 1, "interval_bins": [left, right],
            "broad": right - left + 1 > radius * 2 + 1}


def _spectral_profile(x) -> dict:
    """Describe occupied bands and amplitude concentration independently.

    Empty space BETWEEN broad bands is not tonal evidence. Components bridge
    holes of at most two bins; only components at most six bins wide count as
    narrow peaks. These heuristics remain experimental and are exported.
    """
    import numpy as np
    empty = {"occupied_hz": None, "fill": None, "peakiness": None,
             "within_band_fill": None, "narrow_peak_share": None,
             "band_count": None, "nyquist_hz": SR / 2}
    if x is None or len(x) < WIN:
        return empty
    win = np.hanning(WIN).astype(np.float32)
    step = max(WIN, len(x) // 40)
    rows = []
    for i in range(0, len(x) - WIN + 1, step):
        spec = np.abs(np.fft.rfft(x[i:i + WIN] * win))
        total = float(spec.sum())
        if total <= 0:
            continue
        occupied = np.flatnonzero(spec >= float(spec.max()) / 20.0)
        if not len(occupied):
            continue
        groups = np.split(occupied, np.flatnonzero(np.diff(occupied) > 3) + 1)
        spans = [(int(g[0]), int(g[-1])) for g in groups]
        span = int(occupied[-1] - occupied[0]) + 1
        width = sum(b - a + 1 for a, b in spans)
        narrow_amp = sum(float(spec[a:b + 1].sum()) for a, b in spans if b - a + 1 <= 6)
        rows.append((span * SR / WIN, len(occupied) / span,
                     float(np.sort(spec)[-6:].sum()) / total,
                     len(occupied) / width, narrow_amp / total, len(groups)))
    if not rows:
        return empty
    values = np.mean(rows, axis=0)
    names = ("occupied_hz", "fill", "peakiness", "within_band_fill",
             "narrow_peak_share", "band_count")
    return {**{name: round(float(v), 4) for name, v in zip(names, values)},
            "nyquist_hz": SR / 2}


def _tonal_from_profile(profile: dict) -> float:
    return max(profile.get("peakiness") or 0.0, profile.get("narrow_peak_share") or 0.0)


def _tonal_ratio(x) -> float:
    return _tonal_from_profile(_spectral_profile(x))


def to_wav(path: str) -> tuple[str, bool]:
    """Return (wav_path, is_temp). Non-wav input is decoded to mono 8 kHz."""
    if path.lower().endswith((".wav", ".flac")):
        return path, False
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    cmd = [
        ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "wav", tmp,
    ]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        die(f"audio extract failed: {p.stderr.decode('utf-8', 'ignore')[:300]}")
    return tmp, True


def read_wav(path: str) -> np.ndarray:
    import wave

    w = wave.open(path, "rb")
    ch, n, sr = w.getnchannels(), w.getnframes(), w.getframerate()
    raw = w.readframes(n)
    w.close()
    d = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if ch > 1:
        d = d.reshape(-1, ch).mean(axis=1)
    if sr != SR:
        # crude resample is fine for landmark peaks
        idx = np.arange(0, len(d), sr / SR)
        d = np.interp(idx, np.arange(len(d)), d).astype(np.float32)
    return d


def fingerprints(x: np.ndarray) -> list[tuple[int, list[tuple[int, float]]]]:
    """Per-frame strongest spectral peaks as (bin, log magnitude)."""
    frames: list[tuple[int, list[tuple[int, float]]]] = []
    win = np.hanning(WIN).astype(np.float32)
    for i in range(0, len(x) - WIN, HOP):
        spec = np.abs(np.fft.rfft(x[i:i + WIN] * win))
        spec = np.log1p(spec)
        cand: list[tuple[int, float]] = []
        med = float(np.median(spec))
        for k in range(2, len(spec) - 2):
            v = spec[k]
            if (v > spec[k - 1] and v > spec[k + 1]
                    and v > spec[k - 2] and v > spec[k + 2] and v > med):
                cand.append((k, float(v)))
        if cand:
            cand.sort(key=lambda kv: -kv[1])
            frames.append((i // HOP, cand[:PEAKS_PER_FRAME]))
    return frames


def hash_index(frames):
    """(f1, f2, dt) -> list of anchor times.

    dt is the frame distance between the anchor peak and its target peak. Two
    recordings only agree on a landmark when the frequency pair AND their
    spacing agree, which is what stops dense/periodic material from matching
    itself everywhere.
    """
    by_time = {t: peaks for t, peaks in frames}
    times = sorted(by_time)
    out: dict[tuple[int, int, int], list[int]] = {}
    for idx, t in enumerate(times):
        anchor = by_time[t]
        # look ahead a bounded number of frames for partner peaks
        for j in range(idx, min(idx + 1 + FANOUT, len(times))):
            partner = by_time[times[j]]
            dt = times[j] - t
            for f1, _m1 in anchor[:PEAKS_PER_FRAME]:
                for f2, _m2 in partner[:PEAKS_PER_FRAME]:
                    if f1 == f2:
                        continue
                    out.setdefault((f1, f2, dt), []).append(t)
    return out


def _proves(verdict: str) -> str:
    """What the verdict actually supports. Empty when nothing was established."""
    if verdict == "same_audio_segment":
        return ("a shared AUDIO RECORDING at this lag: one alignment dominates "
                "the landmark votes")
    if verdict == "ambiguous_alignment":
        return ("multiple candidate audio alignments; no unique offset established")
    if verdict == "no_match":
        return ""
    if verdict == "insufficient_evidence":
        return ""      # deliberately empty - the run did not decide anything
    if verdict == "incomparable_tonal_input":
        return ""      # deliberately empty - the method cannot judge this input
    return ""


def match(a_path: str, b_path: str) -> dict:
    a, a_tmp = to_wav(a_path)
    b, b_tmp = to_wav(b_path)
    try:
        xa, xb = read_wav(a), read_wav(b)
        note(f"A {fmt_dur(len(xa) / SR)}   B {fmt_dur(len(xb) / SR)}")
        ha = hash_index(fingerprints(xa))
        hb = hash_index(fingerprints(xb))
        note(f"hash keys: A={len(ha)}  B={len(hb)}")

        votes: Counter[int] = Counter()
        # keep (lag -> {(hash, frame_a, frame_b)}) so the evidence numbers can
        # be restricted to the winning lag AND measured on either side
        lag_pairs: dict[int, set[tuple[int, int, int]]] = {}
        for hi, (key, ts_a) in enumerate(ha.items()):
            ts_b = hb.get(key)
            if not ts_b:
                continue
            for ta in ts_a:
                for tb in ts_b:
                    d = tb - ta
                    votes[d] += 1
                    lag_pairs.setdefault(d, set()).add((hi, ta, tb))
    finally:
        if a_tmp:
            os.unlink(a)
        if b_tmp:
            os.unlink(b)

    total = sum(votes.values())
    if not votes:
        return {
            "mode": "auprint",
            "a": a_path, "b": b_path,
            "total_votes": 0,
            "verdict": "incomparable",
            "note": ("no shared spectral landmarks at all - different audio, "
                     "or one side is silent/mono-cancelled"),
        }

    (lag, top) = votes.most_common(1)[0]
    # Selecting the winning offset by its single tallest bin was wrong: the bin
    # with the most votes is not necessarily the centre of the window with the
    # most votes. Measured on a synthetic distribution: tallest bin 30 votes /
    # window 30, while a neighbouring window held 126 over 7 bins. Pick the
    # window, then centre on it.
    hist: dict[int, int] = {}
    for d, v in votes.items():
        hist[d] = hist.get(d, 0) + v

    localization = alignment_evidence(hist, CLUSTER_RADIUS)
    lag = localization["lag"]
    cluster = _window_sum(hist, lag, CLUSTER_RADIUS)
    top = hist.get(lag, 0)

    # --- alignments (ambiguity) ---------------------------------------------
    # Repeated content produces several equally supported alignments. Every
    # qualifying window is enumerated -- no cap -- so nine repetitions cannot
    # hide behind a four-peak budget and be called "no match".
    peaks = enumerate_alignments(hist, CLUSTER_RADIUS, MIN_ALIGNMENT_VOTES)
    peak_mass = sum(v for _d, v in peaks)
    mass_share = peak_mass / total if total else 0.0
    ambiguous = (len(peaks) > 1
                 and peaks[1][1] >= AMBIGUITY_RATIO * peaks[0][1])
    rival = localization["rival_votes"]

    # Evidence is counted ONLY inside the winning lag window. Counting the whole
    # hash space made unrelated noise look fully covered.
    in_cluster: set[tuple[int, int, int]] = set()
    for d, pairs in lag_pairs.items():
        if abs(d - lag) <= CLUSTER_RADIUS:
            in_cluster |= pairs
    matched_hashes = len({h for h, _a, _b in in_cluster})
    frames_a_matched = {a for _h, a, _b in in_cluster}
    frames_b_matched = {b for _h, _a, b in in_cluster}
    hash_yield = matched_hashes / max(1, len(ha) + len(hb))

    mean_bin = total / max(1, len(votes))
    peak_ratio = (cluster / mean_bin) if mean_bin else 0.0
    profile_a, profile_b = _spectral_profile(xa), _spectral_profile(xb)
    ta_ratio, tb_ratio = _tonal_from_profile(profile_a), _tonal_from_profile(profile_b)
    cluster_share = cluster / total if total else 0.0
    lag_s = lag * HOP / SR

    # Significance: how much of the *vote mass* sits in the winning window.
    #
    # Two naive comparisons both failed on real data:
    #   - vs a single outside bin: flat noise has a tall random spike, which
    #     inflated dominance and let unrelated audio pass.
    #   - vs the best cluster far away: a true match also votes at other lags
    #     (repeated phrases, self-similar ambience), so the "far" competitor was
    #     2339 vs the winner's 2266 and dominance collapsed to 0.97.
    #
    # What actually separates the cases is the SHARE of mass in the winning
    # window compared with what an unstructured distribution would give.
    #   true match here: cluster_share 0.0077 with 2,266 votes over ~300k
    #   unrelated noise: cluster_share 0.0028
    # so dominance is expressed as share relative to a uniform expectation.
    dur_a, dur_b = len(xa) / SR, len(xb) / SR
    analysed = min(dur_a, dur_b)
    frames_a_total = max(1.0, dur_a * SR / HOP)
    frames_b_total = max(1.0, dur_b * SR / HOP)
    votes_per_frame = cluster / max(1.0, min(frames_a_total, frames_b_total))
    # Coverage has to be measured on ONE side and divided by THAT side's frame
    # count. The old version counted A-side frames over min(duration) frames,
    # which is not a fraction at all: it printed 1.018 when A was longer. Measure
    # both sides separately and take the one that actually overlaps.
    cover_a = len(frames_a_matched) / frames_a_total
    cover_b = len(frames_b_matched) / frames_b_total
    time_cover = min(1.0, max(cover_a, cover_b))
    matched_time_points = max(len(frames_a_matched), len(frames_b_matched))
    # ---- peak prominence ---------------------------------------------------
    # With the dt-aware hash, true and unrelated pairs differ in the SHAPE of
    # the lag histogram, not in its magnitude:
    #
    #   true match (60 s master vs its 10-40 s crop)
    #       top 38,794 | second 16,032 | share 1.35%  -> one sharp peak
    #   unrelated (independent noise, same generator settings)
    #       top  6,847 | second  6,730 | share 0.12%  -> flat plateau
    #
    # prominence = top cluster / next-largest cluster (>=2 bins away)
    hist: dict[int, int] = {}
    for d, v in votes.items():
        hist[d] = hist.get(d, 0) + v
    near = {d: v for d, v in hist.items() if abs(d - lag) <= CLUSTER_RADIUS}
    rest_lags = [d for d in hist if abs(d - lag) > CLUSTER_RADIUS * 3]
    if rest_lags:
        r_lag = max(rest_lags, key=lambda d: hist[d])
        rival_legacy = sum(v for d, v in hist.items()
                           if abs(d - r_lag) <= CLUSTER_RADIUS)
    else:
        rival_legacy = 0
    prominence = (cluster / rival) if rival else float("inf")
    lag_dominance = prominence          # kept for callers reading the old name

    # ---- decision ----------------------------------------------------------
    # Order matters: each guard rules out a class of input the method cannot
    # speak about, before any positive claim is made.
    #
    #   < 8 s analysed          -> histogram too short to be meaningful
    #   < 12 clustered votes    -> coincidence, not evidence
    #   < 2% hash yield         -> too few independent landmarks
    #   tonal input             -> same-tone is indistinguishable from same-source
    #   flat lag histogram      -> no alignment explains the shared landmarks
    #
    # After the (f1,f2,dt) hash fix the last one separates cleanly on measured
    # data: true match prominence 2.42, unrelated 1.02.
    peak_ok = prominence >= MIN_PROMINENCE and cluster_share >= MIN_CLUSTER_SHARE
    if analysed < MIN_ANALYSED_SECONDS:
        verdict = "insufficient_evidence"
        reason = (f"only {analysed:.1f}s analysed; below the {MIN_ANALYSED_SECONDS}s "
                  f"minimum for a stable lag histogram")
    elif cluster < MIN_CLUSTER_ABSOLUTE:
        verdict = "insufficient_evidence"
        reason = f"only {cluster} clustered vote(s)"
    elif hash_yield < MIN_HASH_YIELD:
        verdict = "insufficient_evidence"
        reason = (f"{matched_hashes} distinct hashes agreed "
                  f"(yield {hash_yield:.3f}); too few independent landmarks")
    elif time_cover < MIN_TIME_COVER:
        # Declared since the rewrite but never applied: a distribution can clear
        # every other bar while the shared landmarks sit in <1% of the timeline.
        verdict = "insufficient_evidence"
        reason = (f"matched landmarks cover only {time_cover:.1%} of the "
                  f"overlap; below the {MIN_TIME_COVER:.0%} floor")
    elif max(ta_ratio, tb_ratio) > TONAL_CUTOFF:
        verdict = "incomparable_tonal_input"
        reason = ("at least one side is dominated by sustained frequencies; "
                  "this fingerprint cannot distinguish same-source from same-tone")
    elif localization["broad"]:
        verdict = "insufficient_evidence"
        reason = "broad alignment interval: no localized offset was established"
    elif not ambiguous and rival and prominence < MIN_PROMINENCE:
        verdict = "insufficient_evidence"
        reason = "raw competing peaks remain; display merging cannot establish a dominant offset"
    elif ambiguous:
        # Several alignments of comparable strength. This is evidence of shared
        # content that repeats, NOT of unrelated audio -- reporting it as
        # no_match inverted the answer for loop music and repeated intros.
        verdict = "ambiguous_alignment"
        reason = (f"{len(peaks)} alignments of comparable strength "
                  f"({peaks[0][1]} vs {peaks[1][1]} votes at "
                  f"{peaks[0][0] * HOP / SR:.2f}s and {peaks[1][0] * HOP / SR:.2f}s); "
                  f"no single offset has been established")
    elif not peak_ok:
        verdict = "no_match"
        reason = (f"lag histogram is flat "
                  f"(prominence {prominence:.2f}, share {cluster_share:.4f}); "
                  f"no single alignment explains the shared landmarks")
    else:
        verdict = "same_audio_segment"
        reason = (f"{cluster} votes over {matched_time_points} distinct time "
                  f"points, prominence {prominence:.2f} over the rival lag")

    return {
        "mode": "auprint",
        "a": a_path,
        "b": b_path,
        "total_votes": total,
        "modal_lag_s": round(lag_s, 2),
        "modal_votes": top,
        "modal_share": round(top / total if total else 0, 6),
        "cluster_radius_bins": CLUSTER_RADIUS,
        "cluster_votes": cluster,
        "cluster_share": round(cluster_share, 6),
        "top5": [
            {"lag_s": round(d * HOP / SR, 2), "votes": v}
            for d, v in votes.most_common(5)
        ],
        "analysed_seconds": round(analysed, 1),
        "votes_per_frame": round(votes_per_frame, 4),
        "matched_hashes": matched_hashes,
        "hash_yield": round(hash_yield, 4),
        "matched_time_points": matched_time_points,
        "coverage_a": round(cover_a, 4),
        "coverage_b": round(cover_b, 4),
        "time_coverage": round(time_cover, 4),
        "peak_ratio": round(peak_ratio, 2),
        "rival_lag_votes": rival,
        "rival_lag_legacy": rival_legacy,
        "alignments": [
            {"lag_s": round(d * HOP / SR, 2), "votes": v} for d, v in peaks
        ],
        "ambiguous": ambiguous,
        "peak_mass_share": round(mass_share, 4),
        "prominence": (round(prominence, 2)
                       if prominence != float("inf") else None),
        "alignment_interval_s": [round(v * HOP / SR, 3) for v in localization["interval_bins"]],
        "alignment_width_bins": localization["width_bins"],
        "raw_alignments": [
            {"lag_s": round(p["centre"] * HOP / SR, 3), "votes": p["votes"],
             "plateau_bins": [p["start"], p["end"]]} for p in localization["raw_peaks"]],
        "spectral_profile_a": profile_a,
        "spectral_profile_b": profile_b,
        "tonal_ratio_a": round(ta_ratio, 3),
        "tonal_ratio_b": round(tb_ratio, 3),
        "verdict_reason": reason,
        # `proves` is derived from the verdict. Writing a fixed sentence meant an
        # `insufficient_evidence` result still carried "proves a shared AUDIO
        # RECORDING", which is a conclusion the run explicitly did not reach and
        # that a downstream reader would happily quote.
        "proves": _proves(verdict),
        "does_not_prove": (
            "the same video. Shared background music, a dubbed track, or one "
            "stock track used twice all match. Upgrade to 'same video' only when "
            "the picture also matches continuously at the same offset."
        ),
        "interpretation_note": (
            "same_audio_segment: one alignment dominates the landmark votes; "
            "ambiguous_alignment: several candidate offsets remain; "
            "insufficient_evidence: this run could not decide, which is NOT a "
            "finding of difference; incomparable_tonal_input: the method cannot "
            "judge sustained-frequency material."
        ),
        "thresholds": {
            "min_cluster_votes": MIN_CLUSTER_VOTES,
            "min_cluster_share": MIN_CLUSTER_SHARE,
            "min_votes_per_frame": MIN_VOTES_PER_FRAME,
            "min_cluster_absolute": MIN_CLUSTER_ABSOLUTE,
            "min_hash_yield": MIN_HASH_YIELD,
            "min_time_coverage": MIN_TIME_COVER,
            "min_prominence": MIN_PROMINENCE,
            "tonal_cutoff": TONAL_CUTOFF,
            "localization_height_fraction": 0.95,
            "max_localization_width_bins": CLUSTER_RADIUS * 2 + 1,
            "display_valley_ratio": 0.85,
            "narrow_component_max_bins": 6,
            "component_max_gap_bins": 2,
        },
        "calibration_note": (
            "two generations of numbers exist in this file and they are NOT "
            "interchangeable. OLD (f1,f2) hash: 25,441 / 2,353 / 1,262 votes. "
            "CURRENT (f1,f2,dt) hash: 38,794 vs 16,032 (true) and 6,847 vs "
            "6,730 (unrelated) on the 60s fixture. Only the second generation "
            "describes this code; both are single-fixture tuning values, not "
            "universal constants."
        ),
        "verdict": verdict,
    }


def main(argv: list[str]) -> int:
    setup_stdio()
    if "--video" in argv:
        idx = [i for i, t in enumerate(argv) if t == "--video"]
        vids = [argv[i + 1] for i in idx if i + 1 < len(argv)]
        if len(vids) < 2:
            # tolerate `--video A.mp4 B.mp4` (one flag, two paths)
            start = idx[0] + 1
            vids = [a for a in argv[start:] if not a.startswith("--")][:2]
        if len(vids) < 2:
            print(__doc__)
            return 2
        emit(match(vids[0], vids[1]))
        return 0
    args = [a for a in argv if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        return 2
    emit(match(args[0], args[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
