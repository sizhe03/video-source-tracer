#!/usr/bin/env python
"""Stage 1d - alias completion: short token -> verifiable account.

A comment rarely spells the creator's full name. `甘楽` alone finds nothing; the
creator is `甘楽らむ`. Concluding "no such author" from a single miss is the bug
this script exists to prevent.

Search order, cheapest and most trustworthy first:

  1. SAME-PLATFORM BRIDGE - the re-uploader's other posts often carry a full
     `【name/実写】` bracket, or an in-site search for the suspected full name
     returns dozens of re-uploads whose titles spell it out.
  2. SPELLING VARIANTS - 楽/樂/乐, ら/拉. Kept separate, never merged: `甘樂らむ`
     resolves to an unrelated channel, so a variant must be verified on its own.
  3. CONTEXT LADDER - `<token> ASMR` is what actually surfaces the creator; a
     bare token does not.
  4. THREAD CONTEXT - concatenations with other tokens in the same thread.

A token is `resolved` only when a candidate channel both re-exists under its own
name and posts in the target genre. Otherwise it is `unresolved` - which is a
finding about the search, not a claim that the author does not exist.

Usage:
    python alias.py 甘楽
    python alias.py 甘楽 --genre ASMR --verify
    python alias.py --bvid BV1hM4w6fE6o --verify      # pull tokens from comments
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

from _common import bili_json, emit, note, setup_stdio, throttle
from alias import expand_with_comment_context, query_ladder, spelling_variants

BILI_SEARCH = (
    "https://api.bilibili.com/x/web-interface/search/type"
    "?search_type=video&keyword={kw}&page=1&page_size=50&order=pubdate"
)
BILI_ARCH = (
    "https://api.bilibili.com/x/series/recArchivesByKeywords"
    "?mid={mid}&keywords=&ps=50&pn=1"
)

GENRE_HINTS = (
    "ASMR", "asmr", "助眠", "安眠", "睡眠", "触发音", "舔耳", "耳かき",
    "マイク", "太もも", "ニーハイ", "タイツ", "囁き", "ear", "whisper", "sleep",
)


def _q(s: str) -> str:
    from urllib.parse import quote

    return quote(s)


def yt_search(query: str, n: int = 20) -> list[dict]:
    cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-J", "--no-warnings",
           f"ytsearch{n}:{query}"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=200)
        return json.loads(p.stdout).get("entries") or []
    except Exception as e:  # noqa: BLE001
        note(f"yt search failed {query!r}: {e}")
        return []


def bili_search(kw: str) -> list[dict]:
    throttle("api.bilibili.com", gap=9.0)
    try:
        j = bili_json(BILI_SEARCH.format(kw=_q(kw)))
    except Exception as e:  # noqa: BLE001
        note(f"bili search failed {kw!r}: {e}")
        return []
    if j.get("code") != 0:
        return []
    out = []
    for r in (j.get("data") or {}).get("result") or []:
        out.append(
            {
                "id": r.get("bvid"),
                "title": re.sub(r"<[^>]+>", "", r.get("title") or ""),
                "author": r.get("author"),
                "pubdate": r.get("pubdate"),
            }
        )
    return out


def genre_ok(texts: list[str]) -> bool:
    blob = " ".join(texts).lower()
    return any(k.lower() in blob for k in GENRE_HINTS)


def bridge_from_uploader(mid: int | str) -> list[str]:
    """Full names this re-uploader spells out in OTHER post titles."""
    throttle("api.bilibili.com", gap=3.0)
    try:
        j = bili_json(BILI_ARCH.format(mid=mid))
    except Exception as e:  # noqa: BLE001
        note(f"archive bridge failed: {e}")
        return []
    names: set[str] = set()
    for a in ((j.get("data") or {}).get("archives") or []):
        t = a.get("title") or ""
        for br in re.findall(r"【([^】]{2,20})】", t):
            names.add(br.strip())
    return sorted(names)


def resolve_yt(token: str, item_titles: list[str] | None = None) -> dict:
    """Delegate to the shared, account-scoped checker (see authorcheck.py)."""
    import authorcheck

    res = authorcheck.verify_youtube(token, item_titles)
    res["platform"] = "youtube"
    # The confirmed list used to filter on status == "genre_matches", a value
    # _stage() cannot produce, so a resolved token reported confirmed: []. Read
    # the boolean the checker actually publishes.
    res["confirmed"] = [
        {"channel": c["channel"], "status": c["status"],
         "why": c.get("pursue_reasons") or []}
        for c in res["candidates"] if c.get("worth_pursuing")
    ]
    return res


def resolve_bili(token: str, item_titles: list[str] | None = None) -> dict:
    import authorcheck

    res = authorcheck.verify_bilibili(token, item_titles)
    res["platform"] = "bilibili"
    res["confirmed"] = [
        {"author": c["author"], "status": c["status"],
         "why": c.get("pursue_reasons") or []}
        for c in res["candidates"] if c.get("worth_pursuing")
    ]
    return res


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2

    tokens: list[str] = []
    bvid = None
    item_titles: list[str] = []
    verify = "--verify" in argv

    if "--titles" in argv:
        item_titles = [t.strip() for t in
                       argv[argv.index("--titles") + 1].split("|") if t.strip()]

    if "--bvid" in argv:
        bvid = argv[argv.index("--bvid") + 1]
        from comments import candidates, fetch_comments

        throttle("api.bilibili.com", gap=2.0)
        v = bili_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
        aid = v["data"]["aid"]
        comments_by_text, _meta = fetch_comments(aid)
        if not item_titles:
            item_titles = [v["data"].get("title") or ""]
        got = candidates(comments_by_text)
        all_tok = got["names"] if isinstance(got, dict) else got
        leads = got.get("leads", []) if isinstance(got, dict) else []
        # leads first: a resolved URL is stronger than any guessed name, and it
        # used to be appended after the name list and cut off by [:6]
        named = [c["token"] for c in all_tok if not c.get("is_lead")]
        tokens = [c["token"] for c in leads] + [t for t in named][:6]
        withheld = max(0, len(named) - min(6, len(named)))
        note(f"tokens from comments: {tokens} (withheld {withheld} names, "
             f"{len(leads)} lead(s))")
    else:
        all_tok = []
        withheld = 0
        comments_by_text = []
        tokens = [a for a in argv if not a.startswith("--")][:3]

    if not tokens and not ("--bvid" in argv):
        emit({"ok": False, "error": "no token given"})
        return 1

    # Bridges first: a full name written by the re-uploader itself is real
    # evidence and must be searched BEFORE any guessed spelling variant.
    bridges: list[str] = []
    if bvid:
        try:
            throttle("api.bilibili.com", gap=2.0)
            vv = bili_json(f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}")
            mid = vv["data"]["owner"]["mid"]
            bridges = bridge_from_uploader(mid)
            note(f"bridges from uploader {mid}: {bridges[:8]}")
        except Exception as e:  # noqa: BLE001
            note(f"bridge lookup failed: {e}")

    search_list: list[str] = []
    for b in bridges:
        if b and len(b) <= 40 and b not in search_list:
            search_list.append(b)
    for t in tokens:
        if t not in search_list:
            search_list.append(t)

    report = []
    for tok in search_list:
        note(f"resolving {tok!r}  variants={spelling_variants(tok)}"
             + ("  [bridge]" if tok in bridges else ""))
        entry = {
            "token": tok,
            "from_bridge": tok in bridges,
            "spelling_variants": spelling_variants(tok),
            "thread_context_expansions": [],
            "bridges_available": bridges,
            "youtube": None,
            "bilibili": None,
            "status": "unresolved",
        }
        if verify:
            entry["thread_context_expansions"] = expand_with_comment_context(
                tok, comments_by_text)
            if tok.startswith(("url:", "handle:")):
                # never search a lead as if it were a name: report it as an
                # action item instead of manufacturing an "unresolved" name
                entry["kind"] = "lead"
                entry["action"] = ("resolve this link and read the account it "
                                   "points at; do not search it as an author name")
                entry["status"] = "lead"
                report.append(entry)
                continue
            # item_titles reaches the checker: without it the `linked_to_input`
            # dimension was computed and then discarded, so it could never fire
            entry["youtube"] = resolve_yt(tok, item_titles or None)
            entry["bilibili"] = resolve_bili(tok, item_titles or None)
            confirmed = (entry["youtube"].get("confirmed")
                         + entry["bilibili"].get("confirmed"))
            entry["confirmed"] = confirmed
            if confirmed:
                entry["status"] = "worth_pursuing"
            elif (entry["youtube"].get("candidates")
                  or entry["bilibili"].get("candidates")):
                entry["status"] = "account_found_not_linked"
        else:
            entry["query_ladder"] = [{"query": q, "reason": r}
                                     for q, r in query_ladder(tok)[:10]]
        report.append(entry)

    pursued = [e for e in report if e["status"] == "worth_pursuing"]
    emit(
        {
            "ok": True,
            "tokens_searched": len(search_list),
            "tokens_from_comments": len(tokens),
            "item_titles_used": item_titles,
            "bridges": len(bridges),
            # kept for compatibility, but the word is now "worth_pursuing":
            # these are candidates to check, not settled authors
            "resolved": len(pursued),
            "worth_pursuing": len(pursued),
            "confirmed": [{"token": e["token"], "confirmed": e["confirmed"]}
                          for e in pursued],
            "leads": [e["token"] for e in report if e["status"] == "lead"],
            "withheld": {
                "comment_candidates_not_resolved": withheld,
                "note": "raise the token limit to resolve more; bridges are always searched",
            },
            "unresolved": [e["token"] for e in report
                           if e["status"] not in ("worth_pursuing", "lead")],
            "entries": report,
            "policy": (
                "unresolved means the SEARCH failed, not that the author is "
                "absent; continue with bridges, other re-uploads, watermarks, "
                "and keyframe reverse search before closing the trace"
            ),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
