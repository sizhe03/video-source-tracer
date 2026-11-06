"""Alias completion: turn a short token from a comment into a verifiable account.

The reference failure this fixes: a comment says `甘楽`. Searching that token
alone returns only the place name 甘楽町 and unrelated channels
(`甘楽デイティー`). Concluding "no such author" there is wrong -- the creator is
`甘楽らむ`.

Measured, to show why a single search cannot decide:

    '甘楽'      -> 甘楽デイティー / Kanra Deity
    '甘楽 ASMR' -> 甘楽らむ                    <- only this works
    '甘樂'      -> 台灣甘樂, THE CAN甘樂文創   <- noise
    '甘乐'      -> 甘乐                        <- different person entirely
    '甘樂らむ'  -> 甘乐千岁 (unrelated)         <- variant poisoning

So the rule is: never conclude from one miss. Expand spellings, attach a genre
hint, and prefer same-platform bridges (other uploads by the same re-uploader,
in-site search for the full name) before going wide.

Output contract: every candidate carries `evidence` describing HOW it was
reached, and unmatched tokens are reported as `unresolved` -- never as "not
found".
"""
from __future__ import annotations

import re

# --- spelling variants -------------------------------------------------------
# CJK aliases are not phonetic, so a token needs per-character variant expansion.
# Keep this table small and explicit: over-expanding creates false candidates.
_CJK_VARIANTS = {
    "楽": "樂乐",
    "樂": "楽乐",
    "乐": "楽樂",
    "ら": "拉",
    "む": "姆",
    "櫻": "樱",
    "さ": "沙",
    "く": "库",
}

# genre hints tried after a bare token fails
_CONTEXT_HINTS = ("ASMR", "助眠", "asmr", "睡眠導入")


def spelling_variants(token: str) -> list[str]:
    """All plausible spellings of a token, original first.

    Only one character is substituted at a time -- combining substitutions
    explodes the space and mostly produces nonsense.
    """
    out = [token]
    for i, ch in enumerate(token):
        alt = _CJK_VARIANTS.get(ch)
        if not alt:
            continue
        for a in alt:
            cand = token[:i] + a + token[i + 1:]
            if cand not in out:
                out.append(cand)
    return out


def query_ladder(token: str) -> list[tuple[str, str]]:
    """Ordered (query, reason) pairs to try for one token."""
    qs: list[tuple[str, str]] = []
    for v in spelling_variants(token):
        tag = "original" if v == token else f"variant:{v}"
        for hint in _CONTEXT_HINTS:
            qs.append((f"{v} {hint}", f"{tag}+context:{hint}"))
        qs.append((v, f"{tag}+bare"))
    return qs


# Suffixes appended when guessing a longer form of a short token.
#
# Review finding: the previous version hard-coded ("らむ", "らむASMR"), i.e. it
# was tuned to the one reference case. That is not a general alias-completion
# method and would bias every other trace toward one Japanese honorific.
# The list below is generic Japanese/Chinese name-particle vocabulary; it is
# still a guess list, so every guess must be verified like any other candidate.
GENERIC_SUFFIXES = (
    "ASMR", "asmr", "助眠", "channel", "ch", "official",
    "さん", "ちゃん", "ch.", "TV",
)


def expand_with_comment_context(token: str, comments: list[dict]) -> list[str]:
    """Longer forms of a short token: thread co-occurrence first, then generic.

    Anything found in the thread wins, because it is real text from a human
    rather than a guess. Generic suffixes are appended last and are marked by
    their low confidence in the returned order.
    """
    seen: list[str] = []
    for c in comments:
        for word in re.split(r"[\s,.;:!?()\[\]{}<>/|~\-]+", c.get("text") or ""):
            w = word.strip()
            if w and w != token and token in w and len(w) <= 24 and w not in seen:
                seen.append(w)
    out = list(seen)
    for suf in GENERIC_SUFFIXES:
        cand = token + suf
        if cand not in out:
            out.append(cand)
    return out
