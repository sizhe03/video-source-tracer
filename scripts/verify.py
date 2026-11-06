#!/usr/bin/env python
"""Stage 2 - identity verification with MANDATORY controls.

The single most important rule in this script: a similarity number on its own
means nothing. Feature matchers happily report 10-20 "inliers" between two
unrelated anime-figure thumbnails. So every run must include

  * a positive control (the two images must be the same picture, so the metric
    is shown to be able to fire), and
  * negative controls (images that are definitely unrelated, so we learn the
    noise floor for THIS pair of images).

A suspect is only accepted when

    suspect_inliers >= 50 AND suspect_inliers > 5 * max(negative_inliers)

Modes:
    python verify.py pixels   A.jpg B.jpg               # YAVG / YMAX after resize
    python verify.py frames   A.mp4 B.mp4 [--offset S]  # 2fps fingerprint voting
    python verify.py sift     A.jpg B.jpg --neg C.jpg [--neg D.jpg] [--pos P.jpg]
    python verify.py psnr     A.mp4 B.mp4 --at 520 --ref 546
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from _common import die, emit, ffmpeg, note, setup_stdio

try:
    import numpy as np
except Exception:  # noqa: BLE001
    die("numpy is required (python -m pip install numpy)")
try:
    import cv2
except Exception:  # noqa: BLE001
    die("opencv is required (python -m pip install opencv-python-headless)")


# ---------------------------------------------------------------- image utils
def load_img(path: str, width: int | None = None):
    im = cv2.imread(path)
    if im is None:
        die(f"cannot read image: {path}")
    if width and im.shape[1] != width:
        h = int(im.shape[0] * width / im.shape[1])
        im = cv2.resize(im, (width, h), interpolation=cv2.INTER_AREA)
    return im


def phash(im, hs: int = 16, ws: int = 16):
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(g)[:hs, :ws]
    med = np.median(d[1:, :].flatten())
    return (d > med).flatten()


def pixel_delta(a_path: str, b_path: str) -> dict:
    """Resize B to A, then diff.

    A blur is applied first: JPEG ringing and two different resize kernels
    (cv2 INTER_AREA vs ffmpeg lanczos) leave isolated single-pixel spikes that
    would otherwise blow up YMAX on genuinely identical images. After the blur
    the mean plus the 99th percentile separate "same file re-encoded" from
    "different picture" cleanly.
    """
    a = load_img(a_path)
    b = load_img(b_path, width=a.shape[1])
    if b.shape[:2] != a.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    ga = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    gb = cv2.GaussianBlur(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    d = cv2.absdiff(ga, gb)
    yavg = float(d.mean())
    ymax = int(d.max())
    p99 = float(np.percentile(d, 99))
    if yavg < 1.0 and p99 < 12:
        verdict = "L2_identical_image"
    elif yavg < 6.0:
        verdict = "L2_very_close"
    else:
        verdict = "no_match"
    return {
        "mode": "pixels",
        "a": a_path,
        "b": b_path,
        "YAVG": round(yavg, 3),
        "YMAX": ymax,
        "Y_p99": round(p99, 2),
        "phash_distance": int((phash(a) != phash(b)).sum()),
        "verdict": verdict,
    }


# --------------------------------------------------------------- frame voting
def extract_fingerprints(video: str, fps: float = 2.0, size=(64, 36)) -> np.ndarray:
    """W x H grayscale frames at `fps`, flattened into row-per-frame vectors."""
    w, h = size
    fd, tmp = tempfile.mkstemp(suffix=".bin")
    os.close(fd)
    try:
        cmd = [
            ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", video, "-vf", f"fps={fps},scale={w}:{h},format=gray",
            "-f", "rawvideo", tmp,
        ]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode != 0:
            die(f"ffmpeg failed: {p.stderr.decode('utf-8', 'ignore')[:300]}")
        buf = np.fromfile(tmp, dtype=np.uint8).astype(np.float32)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    fs = w * h
    n = buf.size // fs
    frames = buf[: n * fs].reshape(n, fs)
    frames -= frames.mean(axis=1, keepdims=True)
    frames /= (np.linalg.norm(frames, axis=1, keepdims=True) + 1e-6)
    return frames


def frame_vote(a_video: str, b_video: str, fps: float = 2.0, stride: int = 4) -> dict:
    A = extract_fingerprints(a_video, fps)
    B = extract_fingerprints(b_video, fps)
    note(f"fingerprints: A={A.shape[0]} frames, B={B.shape[0]} frames @{fps}fps")

    peaks = []
    for i in range(0, len(A), stride):
        c = B @ A[i]
        j = int(np.argmax(c))
        peaks.append((float(c[j]), i, j))
    peaks.sort(reverse=True)

    from collections import Counter

    votes: Counter[float] = Counter()
    for score, i, j in peaks:
        if score >= 0.85:
            votes[round((j - i) / fps, 1)] += 1
    top_offset, top_votes = (votes.most_common(1)[0] if votes else (None, 0))

    best = peaks[0] if peaks else (0.0, 0, 0)
    return {
        "mode": "frames",
        "a": a_video,
        "b": b_video,
        "fps": fps,
        "top_corr": round(best[0], 4),
        "top_offset_s": round((best[2] - best[1]) / fps, 1),
        "modal_offset_s": top_offset,
        "modal_offset_votes": top_votes,
        "offset_histogram": votes.most_common(5),
        "verdict": (
            "L2_same_footage"
            if best[0] >= 0.85 and top_votes >= 3
            else "no_match"
        ),
    }


# ---------------------------------------------------------------------- SIFT
def sift_inliers(a_path: str, b_path: str, max_w: int = 1280) -> tuple[int, int]:
    a = load_img(a_path, width=max_w)
    b = load_img(b_path, width=max_w)
    sift = cv2.SIFT_create(4000)
    g1 = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    k1, d1 = sift.detectAndCompute(g1, None)
    k2, d2 = sift.detectAndCompute(g2, None)
    if d1 is None or d2 is None or len(k1) < 10 or len(k2) < 10:
        return 0, 0
    knn = cv2.BFMatcher().knnMatch(d1, d2, k=2)
    good = [m for m, n in knn if m.distance < 0.75 * n.distance]
    inliers = 0
    if len(good) >= 4:
        src = np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
        if mask is not None:
            inliers = int(mask.sum())
    return len(good), inliers


def sift_with_controls(a: str, b: str, negs: list[str], poss: list[str]) -> dict:
    if not negs:
        die("refusing to judge without at least one --neg control image")
    good, inl = sift_inliers(a, b)
    neg_results = []
    for n in negs:
        g, i = sift_inliers(a, n)
        neg_results.append({"path": n, "good": g, "inliers": i})
    pos_results = []
    for p in poss:
        g, i = sift_inliers(a, p)
        pos_results.append({"path": p, "good": g, "inliers": i})

    neg_max = max([r["inliers"] for r in neg_results], default=0)
    pos_min = min([r["inliers"] for r in pos_results], default=0)

    # Order matters. The positive control is a PRE-CONDITION: if the matcher
    # cannot separate two copies of the same picture, every score it produces on
    # the suspect is meaningless. The previous version tested `accepted` first,
    # so a run with no positive control -- or a failing one -- could still emit
    # L2_same_image. Review reproduced exactly that (100 / neg 5 / pos 0 ->
    # L2_same_image).
    if not pos_results:
        verdict = "unverified_no_positive_control"
    elif pos_min < 50:
        verdict = "controls_invalid_positive_control_weak"
    elif not neg_results:
        verdict = "unverified_no_negative_control"
    elif inl >= 50 and inl > 5 * neg_max:
        verdict = "L2_same_image"
    else:
        verdict = "noise_rejected"

    return {
        "mode": "sift",
        "a": a,
        "b": b,
        "suspect": {"good": good, "inliers": inl},
        "negatives": neg_results,
        "positives": pos_results,
        "neg_max": neg_max,
        "pos_min": pos_min if pos_results else None,
        "rule": (
            "precondition: positive control >= 50 inliers, else the run proves "
            "nothing; then suspect >= 50 AND suspect > 5*max(negative)"
        ),
        "positives_supplied": len(pos_results),
        "negatives_supplied": len(neg_results),
        "verdict": verdict,
    }


# ---------------------------------------------------------------------- PSNR
def psnr_at(a_video: str, b_video: str, at: float, ref: float, dur: float = 3.0) -> dict:
    ff = ffmpeg()
    tmpdir = tempfile.mkdtemp(prefix="trace_psnr_")
    seg_a = os.path.join(tmpdir, "a.mp4")
    seg_b = os.path.join(tmpdir, "b.mp4")
    for src, start, dst in ((a_video, at, seg_a), (b_video, ref, seg_b)):
        cmd = [
            ff, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(start), "-i", src, "-t", str(dur),
            "-c:v", "libx264", "-crf", "12", dst,
        ]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode != 0:
            die(f"ffmpeg cut failed: {p.stderr.decode('utf-8', 'ignore')[:200]}")
    cmd = [ff, "-hide_banner", "-nostats", "-i", seg_a, "-i", seg_b, "-lavfi", "psnr", "-f", "null", "-"]
    p = subprocess.run(cmd, capture_output=True)
    text = (p.stderr + p.stdout).decode("utf-8", "ignore")
    avg = None
    for line in text.splitlines():
        if "average:" in line:
            try:
                avg = float(line.split("average:")[1].split()[0])
            except Exception:  # noqa: BLE001
                pass
    for f in (seg_a, seg_b):
        try:
            os.unlink(f)
        except OSError:
            pass
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass
    return {
        "mode": "psnr",
        "a": a_video,
        "b": b_video,
        "a_start": at,
        "b_start": ref,
        "psnr_avg_db": avg,
        "verdict": "L2_same_footage" if avg and avg >= 30 else ("weak" if avg and avg >= 22 else "no_match"),
        "note": "unrelated footage of similar content typically reads 10-13 dB; same source typically 20+ dB",
    }


# ---------------------------------------------------------------------- main
def main(argv: list[str]) -> int:
    setup_stdio()
    if len(argv) < 3:
        print(__doc__)
        return 2
    mode, a, b = argv[0], argv[1], argv[2]
    rest = argv[3:]

    def opt(flag: str, default=None):
        if flag in rest:
            return rest[rest.index(flag) + 1]
        return default

    def multi(flag: str) -> list[str]:
        out = []
        for i, tok in enumerate(rest):
            if tok == flag and i + 1 < len(rest):
                out.append(rest[i + 1])
        return out

    if mode == "pixels":
        emit(pixel_delta(a, b))
        return 0
    if mode == "frames":
        emit(frame_vote(a, b, fps=float(opt("--fps", 2.0)), stride=int(opt("--stride", 4))))
        return 0
    if mode == "sift":
        emit(sift_with_controls(a, b, multi("--neg"), multi("--pos")))
        return 0
    if mode == "psnr":
        emit(psnr_at(a, b, float(opt("--at", 0)), float(opt("--ref", 0)),
                     float(opt("--dur", 3))))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
