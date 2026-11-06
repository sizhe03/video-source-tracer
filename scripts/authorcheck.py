"""Account-scoped candidate checks and reviewed creator-attribution records.

Name/genre/title matches identify leads. A content match identifies overlap.
Only an explicitly reviewed creator credit, statement or official cross-link
bound to the candidate account and input work can support attribution. The
validator checks record consistency; it does not inspect a web page itself.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from urllib.parse import quote, urlsplit
from datetime import datetime

from _common import bili_json, note, throttle

GENRE_HINTS = (
    "ASMR", "asmr", "助眠", "安眠", "睡眠", "触发音", "舔耳", "耳かき",
    "マイク", "太もも", "ニーハイ", "タイツ", "スクラッチ", "囁き",
    "ear", "whisper", "sleep", "tingles", "擦麦", "哄睡",
)

BILI_SEARCH = (
    "https://api.bilibili.com/x/web-interface/search/type"
    "?search_type=video&keyword={kw}&page=1&page_size=50"
)


def _yt_search(query: str, n: int = 20) -> list[dict]:
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-J", "--no-warnings",
           f"ytsearch{n}:{query}"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=200)
        return json.loads(p.stdout).get("entries") or []
    except Exception as e:  # noqa: BLE001
        note(f"yt search failed {query!r}: {e}")
        return []


_ASCII_WORD = re.compile(r"^[a-z]+$")


def _genre_ok(titles: list[str]) -> bool:
    """Does this content vocabulary appear?

    ASCII hints need word boundaries. Plain substring matching let `ear` match
    inside `Learn woodworking`, so a trades channel satisfied the ASMR genre gate
    and the alias ladder stopped there. CJK hints have no word boundaries and
    stay substring-matched.
    """
    blob = " ".join(titles).lower()
    for k in GENRE_HINTS:
        kl = k.lower()
        if _ASCII_WORD.match(kl):
            if re.search(rf"(?<![a-z]){re.escape(kl)}(?![a-z])", blob):
                return True
        elif kl in blob:
            return True
    return False


def confirmed_any(cands: dict) -> bool:
    """True when some candidate's OWN works pass the genre gate.

    This is a *progress* signal, not a stopping condition. Two different ASMR
    creators can share a short name, so "the first hit is also an ASMR account"
    does not mean the ladder is finished -- v5 still broke out of the ladder on
    this, which is exactly the same-name-same-genre trap the ladder exists to
    escape.
    """
    for rec in cands.values():
        titles = rec.get("titles") or rec.get("own_titles") or []
        if _genre_ok(titles):
            return True
    return False


def ladder_may_stop(cands: dict) -> bool:
    """May the query ladder stop before exhausting the variants?

    Only on evidence that ties an account to THIS clip. A genre match cannot
    distinguish two same-named creators, so it must never end the search.
    """
    for rec in cands.values():
        account_id = rec.get("account_id") or rec.get("channel_id") or rec.get("mid")
        norm = _normalise_link(rec.get("linked_by"), account_id,
                               rec.get("input_work_id"))
        if norm and not norm.get("incomplete"):
            return True
    return False



# --------------------------------------------------------------- evidence model
# A single `status` string cannot express what is actually known. Four separate
# dimensions, each with its own verdict vocabulary:
#   not_checked | supports | contradicts | unverifiable
EVIDENCE_DIMENSIONS = (
    "account",        # which exact platform account
    "provenance",     # what comment / signature / link points at it
    "content_match",  # which work, at which timestamps, matches the input
    "attribution",    # why it is the creator rather than another re-uploader
    "relevance",      # does the account talk about the same subject (weakest)
)


def _blank_evidence() -> dict:
    return {d: {"verdict": "not_checked", "detail": None} for d in EVIDENCE_DIMENSIONS}


def _evidence(account, account_id, own_titles, item_titles, link, works,
              linked_by=None, input_work_id=None) -> dict:
    ev = _blank_evidence()
    ev["relevance"] = {"verdict": "not_checked", "detail": None}
    ev["account"] = {
        "verdict": "supports" if account else "unverifiable",
        "detail": {"name": account, "id": account_id},
    }
    # provenance: only a real artefact (a comment URL, a signature, a cross-link)
    # may set this. A bare URL is NOT accepted until it has been resolved.
    resolved_link = False
    if isinstance(link, dict):
        try:
            source = urlsplit(link.get("url", ""))
            resolved_link = (link.get("resolved") is True
                             and source.scheme in ("http", "https") and bool(source.hostname)
                             and account_id is not None
                             and str(link.get("account_id")) == str(account_id)
                             and isinstance(link.get("excerpt"), str)
                             and bool(link["excerpt"].strip()))
        except (TypeError, ValueError, AttributeError):
            pass
    if resolved_link:
        ev["provenance"] = {"verdict": "supports", "detail": link}
    else:
        ev["provenance"] = {
            "verdict": "unverifiable",
            "detail": {"note": "no comment link / signature resolved for this "
                               "account yet", "raw": link},
        }
    if works:
        ev["content_match"] = {"verdict": "supports", "detail": works}
    elif own_titles:
        ev["content_match"] = {
            "verdict": "not_checked",
            "detail": {"sampled_titles": len(own_titles),
                       "note": "titles sampled but no frame/audio comparison run"},
        }
    # attribution stays unverifiable unless a caller supplies a STRUCTURED
    # artefact tying this account to this clip. A bare string used to be enough,
    # which meant "non-empty" was doing the work of "verified"; it is now kept as
    # a lead and reported as incomplete.
    norm = _normalise_link(linked_by, account_id, input_work_id)
    if norm and not norm.get("incomplete"):
        ev["attribution"] = {
            "verdict": "supports",
            "detail": {"linked_by": norm},
        }
    elif norm:
        ev["attribution"] = {
            "verdict": "unverifiable",
            "detail": {
                "note": "attribution evidence is incomplete: " + norm["incomplete"],
                "raw": norm,
            },
        }
    else:
        ev["attribution"] = {
            "verdict": "unverifiable",
            "detail": {
                "note": "needs a link or signature tying this account to THIS "
                        "clip; shared title words are relevance, not attribution",
            },
        }
    return ev


def _relevance(item_titles: list[str], own_titles: list[str]) -> dict:
    """How relevant are these works to the input? NOT attribution.

    v5 wrote shared title vocabulary into `attribution.verdict = supports`, so
    two clips that merely shared the words "助眠" and "摩擦" produced
    `status: attributed` with the reason "link to this clip" -- while no such
    link existed. Vocabulary overlap is a *search relevance* signal: it says the
    account talks about the same subject, not that it made this clip.

    Three outcomes, because "the comparison ran and found nothing in common" is
    not the same as "the comparison never ran":

      no_overlap  compared, no distinctive term shared
      supports    compared, distinctive terms shared
      not_checked one side had no distinctive terms to compare

    `no_overlap` is deliberately NOT `contradicts`: a rewritten or translated
    title is a normal case this whole skill exists to handle, not a refutation.
    """
    if not item_titles:
        return {"verdict": "not_checked",
                "detail": {"note": "no input titles supplied to compare"}}
    shared = _shared_terms(item_titles, own_titles) if own_titles else []
    distinctive = [w for w in shared if w not in _GENERIC_TERMS]
    if distinctive:
        return {
            "verdict": "supports",
            "detail": {
                "shared_terms": distinctive,
                "note": "title vocabulary overlap only - relevance, NOT attribution",
            },
        }
    if own_titles:
        return {
            "verdict": "no_overlap",
            "detail": {
                "shared_terms": shared,
                "note": "compared; no distinctive term in common. This is NOT a "
                        "contradiction - rewritten and translated titles are "
                        "expected, and content may still match",
            },
        }
    return {"verdict": "not_checked",
            "detail": {"note": "candidate has no sampled titles"}}


def _normalise_link(link, account_id=None, input_work_id=None) -> dict | None:
    """Validate a recorded human/agent check, not the truth of a remote page.

    Callers must actually inspect the cited artefact before setting verified.
    A work match or an uploader URL alone does not establish creator identity.
    The same contract governs attribution and early stopping of alias search.
    """
    if not link:
        return None
    if isinstance(link, str):
        return {"url": link, "work_id": None, "basis": None,
                "checked_at": None,
                "incomplete": "bare string: no work id, no basis, not verified"}
    if isinstance(link, dict):
        fields = ("url", "work_id", "account_id", "input_work_id", "basis",
                  "checked_at", "evidence_type", "evidence_quote")
        errors = [f"missing or invalid {k}" for k in fields
                  if not isinstance(link.get(k), str) or not link[k].strip()]
        if link.get("verified") is not True:
            errors.append("not explicitly verified")
        if account_id is None or str(link.get("account_id")) != str(account_id):
            errors.append("candidate account does not match")
        if input_work_id is None or link.get("input_work_id") != input_work_id:
            errors.append("input work does not match")
        if link.get("evidence_type") not in {
                "creator_credit", "creator_statement", "official_crosslink"}:
            errors.append("evidence does not establish creator attribution")
        try:
            u = urlsplit(link.get("url", ""))
            if u.scheme not in ("http", "https") or not u.hostname:
                errors.append("invalid evidence URL")
            datetime.fromisoformat(link.get("checked_at", "").replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError):
            errors.append("invalid URL or checked_at")
        if link.get("incomplete"):
            errors.append(str(link["incomplete"]))
        return {**link, "incomplete": "; ".join(errors)}
    return {"incomplete": f"unrecognised link type {type(link).__name__}"}


# ------------------------------------------------------------ public verdict
# Review finding: `resolved` reads like "the author is settled", but it only
# means "worth another look". Callers were also comparing `status` strings
# against a value (`genre_matches`) that `_stage()` can never return, so a run
# that DID find the creator still reported `findings: 0`.
#
# The contract is now: callers ask `worth_pursuing(res)`, never a status string.
def worth_pursuing(res: dict) -> bool:
    """Is this result worth further work? NOT "is this the author"."""
    if not res:
        return False
    if "worth_pursuing" in res:
        return bool(res["worth_pursuing"])
    return bool(res.get("resolved"))          # older payloads


def _pursue_reasons(ev: dict, own_titles: list[str]) -> list[str]:
    out = []
    if ev["attribution"]["verdict"] == "supports":
        out.append("attribution: an artefact ties this account to this clip")
    if ev["content_match"]["verdict"] == "supports":
        out.append("content_match: a work was compared")
    if ev["provenance"]["verdict"] == "supports":
        out.append("provenance: comment link / signature")
    if _genre_ok(own_titles):
        out.append(f"genre: {len(own_titles)} own title(s) in genre vocabulary")
    return out


def _worth_pursuing(ev: dict, own_titles: list[str]) -> bool:
    """Is this candidate worth further work? (NOT "is it the author".)

    A genre match on the account's own works qualifies: it is the strongest
    automatic signal available, and requiring frame-level content_match for
    every candidate would mark every result unresolved.
    """
    return bool(_pursue_reasons(ev, own_titles))


def _stage(ev: dict) -> str:
    """A convenient label, derived from the dimensions -- never a substitute."""
    if ev["attribution"]["verdict"] == "supports":
        return "attributed"
    if ev["content_match"]["verdict"] == "supports":
        return "content_matched"
    if ev["provenance"]["verdict"] == "supports":
        return "linked"
    if ev["account"]["verdict"] == "supports":
        return "account_only"
    return "none"


_CJK_RUN = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]+")
_LATIN_WORD = re.compile(r"[a-z0-9]{4,}")


def _shared_terms(item_titles: list[str], own_titles: list[str]) -> list[str]:
    """Distinctive vocabulary shared by the input clip and the candidate works.

    The previous version only looked at ASCII words >=4 chars, so it was always
    empty for Japanese/Chinese titles and `linked_to_input` never fired. CJK runs
    are compared as character bigrams for the same reason `_common.jaccard`
    does it: unigrams are too common to mean anything.
    """
    def terms(texts: list[str]) -> set[str]:
        out: set[str] = set()
        blob = " ".join(texts).lower()
        for w in _LATIN_WORD.findall(blob):
            if w not in _STOP_TERMS:
                out.add(w)
        for run in _CJK_RUN.findall(blob):
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if bg not in _STOP_TERMS:
                    out.add(bg)
        return out

    a, b = terms(item_titles), terms(own_titles)
    return sorted(a & b)


_STOP_TERMS = {"asmr", "the", "and", "with", "http", "https", "www", "com",
               "video", "watch", "channel"}

# Vocabulary that appears in almost every clip of a genre. Sharing these says
# nothing about which clip is which, so they must never count as a distinctive
# match. Kept separate from _STOP_TERMS (which is about noise) because these are
# meaningful words that are simply not identifying.
_GENERIC_TERMS = {
    "asmr", "助眠", "睡眠", "安眠", "触发音", "摩擦", "麦克风", "麦", "耳",
    "舔耳", "耳かき", "囁き", "タイツ", "太もも", "ニーハイ", "靴下", "丝袜",
    "白丝", "大腿", "哄睡", "擦麦", "声", "音", "sleep", "whisper", "ear",
    "tingles", "mic", "microphone", "thigh", "thighs", "socks", "legs",
}


# ------------------------------------------------------------------ youtube
def verify_youtube(token: str, item_titles: list[str] | None = None) -> dict:
    """Verify a token against YouTube, scoping every check to one channel.

    `item_titles` contribute retrieval relevance only. Automated search does
    not populate reviewed creator-attribution records.
    """
    candidates: dict[str, dict] = {}
    tried: list[dict] = []

    from alias import query_ladder

    for q, reason in query_ladder(token)[:10]:
        needle = q.split(" ")[0]
        entries = _yt_search(q, 20)
        matched = 0
        for e in entries:
            ch = (e.get("channel") or e.get("uploader") or "").strip()
            cid = e.get("channel_id") or ""
            if ch and needle in ch:
                key = cid or ch          # prefer the platform id, not the label
                rec = candidates.setdefault(
                    key, {"channel": ch, "channel_id": cid, "hits": 0, "titles": []})
                rec["hits"] += 1
                # ONLY this channel's own titles may satisfy the genre gate
                if (e.get("channel") or "").strip() == ch:
                    rec["titles"].append(e.get("title") or "")
                matched += 1
        tried.append({"query": q, "reason": reason, "results": len(entries),
                      "matched": matched})
        if ladder_may_stop(candidates):
            break

    out = []
    for rec in candidates.values():
        evidence = _evidence(
            account=rec["channel"], account_id=rec["channel_id"],
            own_titles=rec["titles"], item_titles=item_titles,
            link=rec.get("link"), works=rec.get("works") or [],
            linked_by=rec.get("linked_by"))
        # Shared title words are RECORDED, but into `relevance` -- never into
        # `attribution`. v5 wrote them into attribution, which made two clips
        # sharing "助眠"/"摩擦" report `attributed` with the reason
        # "link to this clip" while no link existed.
        evidence["relevance"] = _relevance(item_titles, rec["titles"])
        reasons = _pursue_reasons(evidence, rec["titles"])
        out.append({
            "channel": rec["channel"],
            "channel_id": rec["channel_id"],
            "own_titles_sampled": len(rec["titles"]),
            "evidence": evidence,
            # kept for callers that only look at the booleans
            "steps": {k: v["verdict"] == "supports" for k, v in evidence.items()},
            "status": _stage(evidence),
            "worth_pursuing": bool(reasons),
            "pursue_reasons": reasons,
            # legacy alias so old callers do not silently read False
            "resolved": bool(reasons),
        })

    out.sort(key=lambda r: (r["status"] == "none", -r["own_titles_sampled"]))
    return {
        "ok": True,
        "tried_queries": tried,
        "candidates": out,
        # "worth_pursuing" is what callers must gate on. `resolved` is kept as a
        # deprecated alias for the same boolean -- it never meant "attributed".
        "worth_pursuing": any(r["worth_pursuing"] for r in out),
        "resolved": any(r["worth_pursuing"] for r in out),
        "note": "worth_pursuing != attributed; attribution needs a link to this clip",
    }


# ------------------------------------------------------------------ bilibili
def verify_bilibili(token: str, item_titles: list[str] | None = None) -> dict:
    """Same contract for bilibili; scoping uses the uploader's own results."""
    authors: dict[str, dict] = {}
    tried: list[dict] = []

    from alias import query_ladder

    for q, _reason in query_ladder(token)[:6]:
        needle = q.split(" ")[0]
        throttle("api.bilibili.com", gap=9.0)
        try:
            j = bili_json(BILI_SEARCH.format(kw=quote(q)))
        except Exception as e:  # noqa: BLE001
            note(f"bili verify failed {q!r}: {e}")
            continue
        if j.get("code") != 0:
            continue
        res = (j.get("data") or {}).get("result") or []
        matched = 0
        for r in res:
            a = (r.get("author") or "").strip()
            mid = r.get("mid")
            if a and needle in a:
                key = str(mid) if mid else a      # mid first, display name second
                rec = authors.setdefault(
                    key, {"author": a, "mid": mid, "own_titles": []})
                rec["own_titles"].append(r.get("title") or "")
                matched += 1
        tried.append({"query": q, "results": len(res), "matched": matched})
        if ladder_may_stop(authors):
            break

    out = []
    for rec in authors.values():
        evidence = _evidence(
            account=rec["author"], account_id=rec["mid"],
            own_titles=rec["own_titles"], item_titles=item_titles,
            link=rec.get("link"), works=rec.get("works") or [],
            linked_by=rec.get("linked_by"))
        evidence["relevance"] = _relevance(item_titles, rec["own_titles"])
        reasons = _pursue_reasons(evidence, rec["own_titles"])
        out.append({
            "author": rec["author"],
            "mid": rec["mid"],
            "own_titles_sampled": len(rec["own_titles"]),
            "evidence": evidence,
            "steps": {k: v["verdict"] == "supports" for k, v in evidence.items()},
            "status": _stage(evidence),
            "worth_pursuing": bool(reasons),
            "pursue_reasons": reasons,
            "resolved": bool(reasons),
        })

    out.sort(key=lambda r: (r["status"] == "none", -r["own_titles_sampled"]))
    return {
        "ok": True,
        "tried_queries": tried,
        "candidates": out,
        "worth_pursuing": any(r["worth_pursuing"] for r in out),
        "resolved": any(r["worth_pursuing"] for r in out),
        "note": "worth_pursuing != attributed; attribution needs a link to this clip",
    }
