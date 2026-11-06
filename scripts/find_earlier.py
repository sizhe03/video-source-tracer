#!/usr/bin/env python
"""Stage 1b -- find earlier copies of the same clip on the same platform.

The whole point: re-upload chains are serial. The previous hand is always
EARLIER and usually LONGER, because each re-upload often crops the head/tail to
dodge fingerprinting -- but neither is guaranteed. Duration is a soft hint;
content containment is what decides.

Usage:
    python find_earlier.py BV1TrxQ64EXo
    python find_earlier.py --query "<title text>" --duration 631
    python find_earlier.py --yt "\u592a\u3082\u3082 \u30de\u30a4\u30af ASMR" --duration 723

Output: ranked candidates with a `pre` score, plus an explicit verdict.
"""
from __future__ import annotations

import json
import subprocess
import sys

from _common import (bili_json, cookie_arg, emit,
    fmt_dur,
    http_json,
    jaccard,
    note,
    setup_stdio,
    throttle,
)

BILI_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
BILI_SEARCH = (
    "https://api.bilibili.com/x/web-interface/search/type"
    "?search_type=video&keyword={kw}&page=1&page_size=50&order=pubdate"
)
BILI_HDR = {"Referer": "https://www.bilibili.com/"}


def bili_meta(bvid: str) -> dict | None:
    throttle("api.bilibili.com", gap=2.0)
    j = bili_json(BILI_VIEW.format(bvid=bvid), headers=BILI_HDR)
    if j.get("code") != 0:
        return None
    d = j["data"]
    return {
        "platform": "bilibili",
        "id": d["bvid"],
        "title": d["title"],
        "pubdate": d["pubdate"],
        "duration": d["duration"],
        "author": d["owner"]["name"],
        "mid": d["owner"]["mid"],
        "views": d["stat"].get("view"),
        "resolution": f'{d["dimension"]["width"]}x{d["dimension"]["height"]}',
        "cover": d.get("pic"),
    }


def bili_search(keyword: str) -> list[dict]:
    throttle("api.bilibili.com", gap=9.0)
    try:
        j = bili_json(BILI_SEARCH.format(kw=_q(keyword)), headers=BILI_HDR)
    except Exception as e:  # noqa: BLE001
        note(f"search failed: {e}")
        return []
    if j.get("code") != 0:
        note(f"search code={j.get('code')} msg={j.get('message')}")
        return []
    out = []
    for r in (j.get("data") or {}).get("result") or []:
        out.append(
            {
                "platform": "bilibili",
                "id": r.get("bvid"),
                "title": _clean(r.get("title")),
                "pubdate": int(r.get("pubdate") or 0),
                "duration": _secs(r.get("duration")),
                "author": r.get("author"),
                "mid": r.get("mid"),
                "views": r.get("play"),
                "resolution": None,
            }
        )
    return out


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s)


def _clean(s: str | None) -> str:
    import re

    return re.sub(r"<[^>]+>", "", s or "")


def _secs(d) -> int:
    """bilibili returns durations as 'M:SS' or 'H:MM:SS' strings in search."""
    if isinstance(d, int):
        return d
    if not d:
        return 0
    parts = [int(p) for p in str(d).split(":") if p.strip().isdigit()]
    if not parts:
        return 0
    total = 0
    for p in parts:
        total = total * 60 + p
    return total


_GENERIC_TERMS = {
    "asmr", "助眠", "睡眠", "安眠", "触发音", "摩擦", "麦克风", "麦", "耳",
    "舔耳", "耳かき", "囁き", "タイツ", "太もも", "ニーハイ", "靴下", "丝袜",
    "白丝", "大腿", "哄睡", "擦麦", "声", "音", "sleep", "whisper", "ear",
    "tingles", "mic", "microphone", "thigh", "thighs", "socks", "legs",
    "video", "the", "and", "with",
}


def _terms(s: str) -> list[str]:
    """Content words of a title/query, CJK-aware (bigrams + latin words)."""
    from _common import tokens as _t

    return _t(s)


def _distinctive_terms(s: str) -> set[str]:
    """Features that could actually identify this clip.

    Filtering the flattened token list was not enough: `ASMR 助眠` tokenises to
    `asmr, 助眠, 助, 眠`, and dropping the two generic words still left `助` and
    `眠` behind -- two "distinctive" terms produced by one generic phrase. The
    unit of filtering has to match the unit of meaning: decide per segment, then
    keep the features of the segments that survive.

    A run that is *built out of* generic vocabulary (助眠摩擦) is stripped of
    those words and only the residue is kept.
    """
    from _common import segments

    keep: set[str] = set()
    long_generic = sorted((g for g in _GENERIC_TERMS if len(g) >= 2),
                          key=len, reverse=True)
    for kind, text in segments(s):
        if kind == "latin":
            if text in _GENERIC_TERMS or len(text) < 3:
                continue
            keep.add(text)
            continue
        # Strip generic vocabulary out of the run FIRST, then keep what is left.
        # Judging the run as a whole dropped a long real title down to the
        # connective residue ("用在姐姐的上"), which is noise rather than content.
        residue = text
        for g in long_generic:
            residue = residue.replace(g, " ")
        for chunk in residue.split():
            if len(chunk) < 2:
                continue                  # a single leftover character is noise
            keep.add(chunk)
            keep.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return keep


def yt_search(query: str, limit: int = 40) -> list[dict]:
    """YouTube search via yt-dlp's flat-playlist mode (no login needed)."""
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "-J",
        "--no-warnings",
        f"ytsearch{limit}:{query}",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=240)
        d = json.loads(p.stdout)
    except Exception as e:  # noqa: BLE001
        note(f"yt-dlp search failed: {e}")
        return []
    out = []
    for rank, e in enumerate(d.get("entries") or []):
        out.append(
            {
                "platform": "youtube",
                "search_rank": rank,
                "id": e.get("id"),
                "title": e.get("title"),
                "pubdate": 0,
                "duration": int(e.get("duration") or 0),
                "author": e.get("channel") or e.get("uploader"),
                "mid": e.get("channel_id"),
                "views": e.get("view_count"),
                "resolution": None,
            }
        )
    return out


def score(cand: dict, ref: dict) -> dict:
    """Rank one candidate as a possible earlier hand of `ref`.

    Review-driven rewrite. The previous version had three faults that could
    discard the true source before it was ever examined:

      1. duration was treated as a monotonic signal ("longer = upstream").
         Re-uploads may ADD a bumper, repeat a section, concatenate episodes or
         slow the clip down, and an author may post a short teaser before the
         full version. Duration is now only a *soft plausibility* hint.
      2. a heavily re-titled true upstream scored 55 and landed in
         `possible_previous_hand`; a lower threshold plus a separate
         "untitled candidate worth checking" bucket keeps it visible.
      3. title similarity dominated the total. Titles are evidence about
         *labelling*, not about *content*, so their weight is capped.

    The score is now an ordering key, not a verdict. `bucket` says what to do:
      examine_first  - score high enough to check immediately
      worth_checking - low score but has an independent reason (much earlier /
                       much longer / whole-channel match) - DO NOT drop these
      unlikely       - no supporting signal at all
    """
    reasons: list[str] = []
    s = 0.0
    signals: list[str] = []

    # --- duration: soft hint only -------------------------------------------
    dur, rdur = cand.get("duration") or 0, ref.get("duration") or 0
    ratio = (dur / rdur) if rdur and dur else None
    if ratio is not None:
        if 0.90 <= ratio <= 1.10:
            s += 20
            signals.append("duration_close")
            reasons.append(f"duration close ({fmt_dur(dur)} vs {fmt_dur(rdur)})")
        elif 1.10 < ratio <= 2.50:
            s += 15
            signals.append("duration_longer")
            reasons.append(
                f"duration longer ({fmt_dur(dur)}) - may be original, or may "
                f"carry an added intro / concat / slow-down")
        elif 0.50 <= ratio < 0.90:
            s += 10
            signals.append("duration_shorter")
            reasons.append(f"duration shorter ({fmt_dur(dur)}) - may be a crop "
                           f"of a longer master")
        else:
            reasons.append(f"duration ratio {ratio:.2f} - no duration signal")

    # --- title / rank ------------------------------------------------------
    # The two query branches used to be exclusive: `--yt` scored rank ONLY and
    # never looked at the title, so a candidate whose title matched the query
    # terms still read jaccard 0 and fell to `rank_only`. Both branches now do
    # the same content comparison; rank is an ADDITIONAL signal, never the only
    # one. Rank says "the search engine liked it", not "it is the same clip".
    ref_title = ref.get("title") or ""
    j = 0.0
    if ref.get("title_is_query"):
        # Distinctiveness is decided per text segment, then the surviving
        # segments supply the comparison features. Filtering a flattened token
        # list left the fragments of generic phrases behind (助/眠 from 助眠).
        q_dist = _distinctive_terms(ref_title)
        c_dist = _distinctive_terms(cand.get("title") or "")
        all_q = set(_terms(ref_title))
        all_c = set(_terms(cand.get("title") or ""))
        overlap = len(all_q & all_c) / len(all_q) if all_q else 0.0
        shared_dist = q_dist & c_dist
        disc_overlap = (len(shared_dist) / len(q_dist)) if q_dist else 0.0
        specific_query = len(q_dist) >= 2
        j = disc_overlap if q_dist else overlap
        if specific_query and disc_overlap >= 0.85:
            s += 30
            signals.append("title_near_identical")
            reasons.append(f"distinctive-term overlap {disc_overlap:.2f} "
                           f"(near-identical): {sorted(shared_dist)}")
        elif specific_query and disc_overlap >= 0.55:
            s += 18
            signals.append("title_similar")
            reasons.append(f"distinctive-term overlap {disc_overlap:.2f}: "
                           f"{sorted(shared_dist)}")
        elif overlap >= 0.85:
            # the query itself is generic: matching it proves nothing
            reasons.append(
                f"query-term overlap {overlap:.2f} but the query carries no "
                f"distinctive terms ({sorted(q_dist) or 'none'}) - this is "
                f"'satisfies the query', NOT 'same title'")
        elif overlap >= 0.35:
            reasons.append(f"query-term overlap {overlap:.2f} (loose)")
        else:
            reasons.append(f"query-term overlap {overlap:.2f} (rewritten or "
                           f"translated - this alone is NOT evidence of a "
                           f"different clip)")
        # rank is supplementary and capped, so it can never carry a candidate
        # into a verification bucket on its own
        if ref.get("has_rank"):
            rank = cand.get("search_rank")
            if rank is not None and rank < 3:
                s += 12
                signals.append("good_rank")
                reasons.append(f"top-{rank + 1} search relevance")
            elif rank is not None and rank < 8:
                s += 8
                signals.append("good_rank")
                reasons.append(f"rank {rank + 1} in search")
            elif rank is not None and rank < 20:
                s += 3
                signals.append("weak_rank")
                reasons.append(f"rank {rank + 1} in search (weak)")
        signals.append("no_date_available")
    else:
        j = jaccard(cand.get("title") or "", ref_title)
        if j >= 0.80:
            s += 30
            signals.append("title_near_identical")
            reasons.append(f"title jaccard {j:.2f} (near-identical)")
        elif j >= 0.55:
            s += 18
            signals.append("title_similar")
            reasons.append(f"title jaccard {j:.2f}")
        elif j >= 0.35:
            s += 8
            reasons.append(f"title jaccard {j:.2f} (loose)")
        else:
            reasons.append(
                f"title jaccard {j:.2f} (rewritten or translated - this alone "
                f"is NOT evidence of a different clip)")

    # --- date: strong, because a re-upload chain is ordered in time ---------
    if cand.get("pubdate") and ref.get("pubdate"):
        if cand["pubdate"] < ref["pubdate"]:
            days = (ref["pubdate"] - cand["pubdate"]) / 86400
            s += 20 if days >= 7 else 10
            signals.append("earlier")
            reasons.append(f"published {days:.1f} days earlier")
        elif cand["pubdate"] > ref["pubdate"]:
            s -= 20
            reasons.append("published later - downstream unless it is a "
                           "re-upload of a still-earlier original")
    elif not cand.get("pubdate"):
        # No date: neutral, never a penalty. Recorded so the report can say the
        # date was unavailable rather than implying it was checked.
        reasons.append("candidate has no publication date; date not scored")

    # --- resolution: weak ------------------------------------------------
    ra, ca = cand.get("resolution"), ref.get("resolution")
    if ra and ca:
        try:
            rw = int(ra.split("x")[0])
            cw = int(ca.split("x")[0])
            if rw > cw:
                s += 8
                signals.append("higher_res")
                reasons.append(f"higher resolution {ra} > {ca}")
        except Exception:  # noqa: BLE001
            pass

    if cand.get("author") and cand.get("author") == ref.get("author"):
        signals.append("same_uploader")
        reasons.append("same uploader")

    # --- bucket: the actionable part ---------------------------------------
    # "worth_checking" exists so a real upstream with a rewritten title can
    # never be silently filtered out. It needs at least one independent signal
    # beyond a title mismatch.
    # "worth_checking" must admit the rewritten-title upstream WITHOUT admitting
    # unrelated clips from the same genre. Measured calibration:
    #   rewritten-title true upstream (longer + 65 d earlier)  -> 2 signals
    #   unrelated same-genre clip (longer + earlier, no title) -> 1 signal
    # so one independent signal is not enough; require two, or one plus a
    # duration difference large enough that cropping cannot explain it.
    independent = {"earlier", "duration_longer", "higher_res", "good_rank"}
    n_ind = len(independent & set(signals))
    ref_date = ref.get("pubdate") or 0
    cand_date = cand.get("pubdate") or 0
    earlier = bool(cand_date and ref_date and cand_date < ref_date)
    date_unknown = not cand_date or not ref_date

    # Title evidence and structural evidence are two different questions, and a
    # bucket needs both to be meaningful:
    #   content_ok -> is this plausibly the SAME clip?
    #   struct     -> is there any reason to think it sits UPSTREAM?
    # Gating on structural signals alone demoted a same-title, same-duration,
    # top-ranked candidate to `rank_only` (measured: score 52, content match,
    # zero verification), which is the very failure the buckets exist to stop.
    content_ok = ("title_near_identical" in signals
                  or "title_similar" in signals)
    struct = any(x in signals for x in (
        "earlier", "duration_close", "duration_longer", "duration_shorter",
        "higher_res", "good_rank", "same_uploader"))

    if s >= 55:
        bucket = "examine_first"
    elif content_ok and struct:
        bucket = "worth_checking"
    elif n_ind >= 2 and content_ok:
        # Two structural signals PLUS a content match. Without `content_ok` this
        # branch admitted a completely unrelated title on rank + length alone
        # (measured: score 27, worth_checking, zero title overlap).
        bucket = "worth_checking"
    elif earlier and ("duration_longer" in signals and ratio and ratio >= 1.20):
        # A rewritten-title upstream looks exactly like this: no title evidence,
        # earlier AND much longer. It must stay visible -- but it is a
        # structural guess, not a content match, so it goes to `struct_only`
        # where the queue turns it into "go read the comments", not into
        # "verify the video" (there is nothing to compare yet).
        bucket = "struct_only"
        reasons.append("content NOT established: structural only (earlier + "
                       "much longer). Kept because a re-titled upstream looks "
                       "like this; read its comments and credits.")
    elif n_ind >= 2:
        # structurally promising but the title says nothing: record it, do not
        # spend a verification pass on a guess
        bucket = "struct_only"
    elif date_unknown and ("good_rank" in signals or "weak_rank" in signals):
        # Search position alone is a hint worth recording, never a reason to
        # spend a verification pass. Reported separately so it stays visible.
        bucket = "rank_only"
    else:
        bucket = "unlikely"

    return {
        "score": round(s, 1),
        "bucket": bucket,
        "reasons": reasons,
        "signals": signals,
        "duration_ratio": round(ratio, 3) if ratio else None,
        "title_jaccard": round(j, 3),
    }


def _candidate_url(cand: dict, ref: dict) -> str | None:
    """A link a human or a reader can actually open."""
    pid = cand.get("id") or ""
    plat = cand.get("platform") or ref.get("platform")
    if plat == "bilibili":
        return f"https://www.bilibili.com/video/{pid}/"
    if plat == "youtube":
        return f"https://www.youtube.com/watch?v={pid}"
    return None


def _comment_reader(platform: str | None) -> str:
    """Which reader applies. Naming the wrong one sent YouTube ids to bilibili."""
    if platform == "bilibili":
        return "scripts/comments.py"
    if platform == "youtube":
        return ("open the watch page; expand description, pinned comments and "
                "source-related reply threads with an available page reader")
    return "open the URL and read the description, credits and comments"


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2

    backend = "bilibili"
    keyword = None
    duration = None
    ref: dict | None = None
    target = None

    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--query":
            keyword = argv[i + 1]
            i += 2
        elif a == "--yt":
            backend = "youtube"
            keyword = argv[i + 1]
            i += 2
        elif a == "--duration":
            duration = int(argv[i + 1])
            i += 2
        else:
            target = a
            i += 1

    if backend == "bilibili" and target and target.upper().startswith("BV"):
        ref = bili_meta(target)
        if ref is None:
            note(f"could not load {target}")
            emit({"ok": False, "error": "view failed", "target": target})
            return 1
        keyword = keyword or ref["title"]
        duration = duration or ref["duration"]
        ref["has_rank"] = False
        ref["query_terms"] = _terms(keyword)
    else:
        ref = {
            "platform": backend,
            "id": target,
            "title": keyword or "",
            "title_is_query": True,
            "has_rank": backend == "youtube",
            "query_terms": _terms(keyword or ""),
            "pubdate": 0,
            "duration": duration or 0,
            "author": None,
            "resolution": None,
        }

    # a plain title search, plus a de-punctuated variant to hit re-uploads that
    # rewrote "/" as "_", or dropped the brackets / hearts
    queries = [keyword]
    stripped = " ".join(
        t for t in _clean(keyword).replace("_", " ").replace("/", " ").split()
        if t not in {"ASMR", "asmr"}
    ).strip()
    if stripped and stripped != keyword:
        queries.append(stripped)

    pool: dict[str, dict] = {}
    for q in queries:
        hits = bili_search(q) if backend == "bilibili" else yt_search(q)
        note(f"query {q!r} -> {len(hits)} hits")
        for h in hits:
            if h.get("id"):
                pool.setdefault(h["id"], h)

    cands = []
    for c in pool.values():
        if ref.get("id") and c["id"] == ref["id"]:
            continue
        # Duration is NOT a filter here. Review showed a same-title 2x-length
        # earlier upload being discarded before it could be scored. Discovery
        # paths keep everything; score()/buckets decide what deserves attention.
        pass
        s = score(c, ref)
        cands.append({**c, **s})

    order = {"examine_first": 0, "worth_checking": 1, "struct_only": 2,
             "rank_only": 3, "unlikely": 4}
    cands.sort(key=lambda x: (order.get(x["bucket"], 9), -x["score"],
                              x.get("pubdate") or 0))

    top = cands[0] if cands else None
    to_check = [c for c in cands if c["bucket"] in ("examine_first",
                                                    "worth_checking")]
    if top and top["bucket"] == "examine_first":
        verdict = "examine_first"
    elif to_check:
        # a rewritten-title upstream lives here; it must not read as "none"
        verdict = "worth_checking"
    elif cands:
        verdict = "no_credible_match"
    else:
        verdict = "no_candidates"

    # Never silently truncate a list that claims to be actionable. Everything in
    # `must_not_drop` is emitted in full; only `unlikely` rows are paginated, and
    # the count of what was withheld is reported.
    keep = [c for c in cands if c["bucket"] in ("examine_first", "worth_checking")]
    struct_only = [c for c in cands if c["bucket"] == "struct_only"]
    rank_only = [c for c in cands if c["bucket"] == "rank_only"]
    rest = [c for c in cands if c["bucket"] == "unlikely"]
    visible_rest = rest[:12]

    # `struct_only` is not a dead end: a re-upload that rewrote its title carries
    # the creator's name in its COMMENTS and credits, which is exactly how the
    # reference trace was solved. Emit it as a work queue with an explicit
    # action, not as prose in a legend -- v6 left the candidate in the list but
    # the next step still said "escalate to deep_sweep", so nothing ever read it.
    comment_queue = [
        {
            "id": c["id"],
            "platform": c.get("platform") or ref.get("platform"),
            "url": _candidate_url(c, ref),
            "read_with": _comment_reader(c.get("platform") or ref.get("platform")),
            "title": c.get("title"),
            "author": c.get("author"),
            "duration": c.get("duration"),
            "why": ("title-supported repost: inspect credits and replies for creator leads"
                    if any(s in (c.get("signals") or []) for s in
                           ("title_near_identical", "title_similar"))
                    else "structural candidate: inspect credits and replies for creator leads"),
            "signals": c.get("signals"),
            "status": "pending",
            "evidence": [],
            "blocked_reason": None,
            "on_blocked": "retain this task; inspect other reposts and report unread coverage",
        }
        for c in keep + struct_only
    ]
    # Content verification and credit discovery are separate tasks. Title
    # similarity never removes the opportunity to find a name in comments.
    title_supported = [c for c in keep
                       if ("title_near_identical" in (c.get("signals") or [])
                           or "title_similar" in (c.get("signals") or []))]
    # One decision produces both the machine action and the sentence. v7 kept
    # them in separate if-chains and they disagreed: an empty result still said
    # "verify with controls" while the text said "widen the search".
    if comment_queue:
        next_action = "inspect_comments_and_credits"
    elif verdict in ("examine_first", "worth_checking"):
        next_action = "verify_with_controls"
    elif verdict in ("no_candidates", "no_credible_match") or rank_only:
        next_action = "widen_query_set"
    else:
        next_action = "widen_query_set"

    action_detail = {
        "verify_with_controls": (
            "run verify.py (pixels / frames / sift) WITH a positive and at "
            "least one negative control before believing any candidate"),
        "inspect_comments_and_credits": (
            "read each id in comment_candidates using its `read_with` field; "
            "the creator's name or the original link is often written in a "
            "re-upload's comments, description or 【】brackets, and the title "
            "alone will never show it"),
        "widen_query_set": (
            "nothing here is worth verifying yet: widen the query set "
            "(deep_sweep.py page_size=50, channel.py timeline sweep) first"),
    }[next_action]

    emit(
        {
            "ok": True,
            "reference": ref,
            "queries": queries,
            "verdict": verdict,
            "candidates": keep + struct_only + rank_only + visible_rest,
            "struct_only": [c["id"] for c in struct_only],
            "rank_only": [c["id"] for c in rank_only],
            # the actionable queue: run comments.py on each of these ids
            "comment_candidates": comment_queue,
            "verification_candidates": [
                {"id": c["id"], "platform": c.get("platform") or ref.get("platform"),
                 "url": _candidate_url(c, ref), "status": "pending"} for c in keep],
            "next_action": next_action,
            "next_action_detail": action_detail,
            "bucket_legend": {
                "examine_first": "high retrieval priority; content remains unverified",
                "worth_checking": "verify now. Read `signals` to see WHY: a title "
                                  "signal means the TITLE supports this candidate, "
                                  "which is not a content match; "
                                  "`earlier`+`duration_longer` alone means even the "
                                  "title says nothing",
                "struct_only": "earlier/longer/higher-res but NO title evidence; "
                               "read its comments and credits, do not treat it as "
                               "an upstream claim",
                "rank_only": "search position only; widen the query set instead",
                "unlikely": "no supporting signal",
            },
            # Title similarity ranks candidates; it does not establish that the
            # content is the same. Named for what it is so no caller can read it
            # as a content verdict -- v7 called this `content_established`.
            "title_supported_candidates": [c["id"] for c in title_supported],
            "content_match_state": "not_checked",
            "withheld": {
                "unlikely_not_listed": max(0, len(rest) - len(visible_rest)),
                "note": "examine_first and worth_checking are always listed in full",
            },
            "next": _next_text(next_action, comment_queue, rank_only, verdict),
            "must_not_drop": [c["id"] for c in to_check]
                             + [c["id"] for c in struct_only],
        }
    )
    return 0


def _next_text(action: str, comment_queue: list[dict], rank_only: list[dict],
               verdict: str) -> str:
    """One sentence for one action. Same decision, different rendering.

    v7 maintained two if-chains and they contradicted each other: an empty
    result produced `next_action: verify_with_controls` next to
    `next: widen the search`. Both strings now come from `next_action`.
    """
    if action == "inspect_comments_and_credits":
        return (f"read {len(comment_queue)} candidate(s) in comment_candidates "
                f"using their `read_with` field: a re-upload that rewrote its "
                f"title usually still names the creator in its comments or "
                f"【】credits, and a frame comparison cannot recover a name")
    if action == "verify_with_controls":
        return ("verify the examine_first / worth_checking candidates with "
                "verify.py (with controls) before believing any of them")
    if verdict == "no_candidates":
        return ("the query set returned nothing at all: widen it "
                "(deep_sweep.py page_size=50, channel.py timeline sweep)")
    if rank_only:
        return ("only search-rank hits: widen the query set (deep_sweep.py, "
                "channel.py) instead of verifying these")
    return ("no same-platform ancestor visible to this query set; escalate to "
            "deep_sweep.py and channel.py")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
