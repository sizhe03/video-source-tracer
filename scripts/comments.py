#!/usr/bin/env python
"""Stage 1c - mine comments for the answer, then VERIFY each candidate.

Why this exists: on the reference trace the creator's name appeared verbatim in
a comment (`甘楽`) on the second-hand re-upload, and a human reading the same
comment log walked straight past it. Reading is not the hard part; *recognising*
a two-character answer among thirty lines of chatter is.

So this script does not summarise comments. It extracts short residue tokens and
then goes and SEARCHES for each one. A candidate only becomes a finding when a
search returns an author whose name contains it.

The anonymous cap is 3 replies regardless of the real count, and pagination
does not move past it. Attaching a cookies.txt (SESSDATA) lifts the cap; the
script reports which mode it ran in.

Usage:
    python comments.py BV1hM4w6fE6o                        # candidates only
    python comments.py BV1hM4w6fE6o --verify               # verify each one
    python comments.py BV1hM4w6fE6o --cookies cookies.txt  # lift the 3-reply cap
    python comments.py BV1hM4w6fE6o --verify --top 20
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

from alias import query_ladder
from _common import (
    bili_json,
    bili_wbi_json,
    cookie_arg,
    emit,
    note,
    session_state,
    setup_stdio,
    strip_opt,
    throttle,
    use_cookies,
)
import authorcheck

BILI_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
BILI_REPLY = "https://api.bilibili.com/x/v2/reply/main?type=1&oid={aid}&mode={mode}&next={nxt}"
BILI_SUB = (
    "https://api.bilibili.com/x/v2/reply/reply"
    "?type=1&oid={aid}&root={root}&ps=20&pn={pn}&mobi_app=android&platform=android"
)
BILI_SEARCH = (
    "https://api.bilibili.com/x/web-interface/search/type"
    "?search_type=video&keyword={kw}&page=1&order=totalrank"
)

# a channel that is going to be a source for this kind of clip must make
# content in this vocabulary; used to reject channels that merely exist
GENRE_HINTS = (
    "ASMR", "asmr", "助眠", "安眠", "睡眠", "触发音", "舔耳", "耳かき",
    "マイク", "太もも", "ニーハイ", "タイツ", "スクラッチ", "囁き",
    "ear", "whisper", "sleep", "tingles",
)

# chatter that can never be a creator name
STOPWORDS = {
    "正常穿搭", "无不良引导", "无任何不良引导", "求原片", "求原片作者", "这是谁",
    "这是谁啊", "这谁啊", "这是哪位", "这是哪个up", "这是哪位啊", "有的", "收藏啦",
    "第一", "好耶", "喜欢", "好听", "厉害", "谢谢", "哈哈", "笑死", "太强了",
    "什么", "为什么", "怎么回事", "听不懂", "有文字的吗", "什么鬼", "已三连",
    "up加油", "纯不良引导", "觉得这视频会消失啊", "我觉得我太压抑了",
}
_BRACKET_TAG = re.compile(r"\[[^\]]{1,8}\]")          # bilibili emoji: [笑] [doge]
_LAUGH = re.compile(r"[哈h]{3,}|[草w]{3,}", re.I)
_TIME_ONLY = re.compile(r"^\d{1,2}[:：]\d{2}$")
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\u2b00-\u2bff]+"
)

# --- typed evidence ---------------------------------------------------------
# Order matters: a URL is the strongest lead, a handle second, then names/words.
URL_RE = re.compile(r"https?://[^\s<>\"'）)】]+", re.I)
HANDLE_RE = re.compile(r"@([A-Za-z0-9_.\-]{2,30})")
# a bare domain or a platform word plus an id-like token
DOMAIN_RE = re.compile(
    r"\b(?:youtube\.com|youtu\.be|bilibili\.com|b23\.tv|douyin\.com|"
    r"t\.me|twitter\.com|x\.com|instagram\.com|nicovideo\.jp)\b", re.I)


def clean(text: str) -> str:
    """Normalise for matching, WITHOUT destroying leads.

    URLs and handles are removed from the *cleaned* form because they are not
    names, but they are extracted separately by `evidence()` and always reported.
    """
    t = text or ""
    t = URL_RE.sub(" ", t)
    t = HANDLE_RE.sub(" ", t)
    t = _BRACKET_TAG.sub(" ", t)
    t = _EMOJI.sub(" ", t)
    t = _LAUGH.sub(" ", t)
    t = re.sub(r"[\s,.。，、!！?？:：;；\"'“”‘’()（）【】「」~～\-—_/|]+", " ", t)
    return t.strip()


def evidence(text: str) -> dict:
    """Typed leads from one comment. Nothing is discarded silently."""
    raw = text or ""
    urls = URL_RE.findall(raw)
    handles = HANDLE_RE.findall(raw)
    domains = DOMAIN_RE.findall(raw)
    cleaned = clean(raw)
    return {
        "raw": raw,
        "urls": urls,
        "handles": handles,
        "domains": sorted({d.lower() for d in domains}),
        "cleaned": cleaned,
        "has_link": bool(urls),
    }


def _is_noise(token: str) -> bool:
    t = token.strip()
    if not t:
        return True
    if _TIME_ONLY.match(t):
        return True
    if t in STOPWORDS:
        return True
    # pure digits / pure punctuation
    if not re.search(r"[A-Za-z\u3040-\u30ff\u4e00-\u9fff]", t):
        return True
    return False


def _name_like(token: str) -> bool:
    """A name is short, has no sentence punctuation, and is not a whole clause."""
    t = token.strip()
    if not t or len(t) > 24:
        return False
    if len(t.split()) > 3:
        return False
    return True


def candidates(comments: list[dict]) -> list[dict]:
    """Candidates for an author name, PLUS non-name leads kept separate.

    Changes from the previous version:
      * a long comment is no longer dropped — its short fragments are mined and
        its raw text is retained
      * URLs / handles / domains become first-class `lead` entries, ranked above
        any guessed name, because they are direct evidence
    """
    bag: dict[str, dict] = {}
    leads: list[dict] = []

    def add_name(raw: str, like: int, where: str, depth: int = 0) -> None:
        t = clean(raw)
        if _is_noise(t):
            return
        # Split FIRST, then judge. Judging length first was the bug: a 25-char
        # reply containing the name was rejected before its fragments existed.
        frags = [f.strip() for f in re.split(r"[\s,。，、!！?？:：;；]+", t)
                 if f.strip()]
        if len(frags) > 1 and depth < 2:
            for f in frags:
                add_name(f, like, where, depth + 1)
            return
        if not _name_like(t):
            # still too long as a single fragment: keep its opening phrase, which
            # is where "这是XXX的作品" style answers put the name
            head = t[:16]
            if not _is_noise(head):
                cur = bag.setdefault(head, {"token": head, "count": 0,
                                            "likes": 0, "where": []})
                cur["count"] += 1
                cur["likes"] += max(like, 0)
                if where not in cur["where"]:
                    cur["where"].append(where)
            return
        cur = bag.setdefault(t, {"token": t, "count": 0, "likes": 0, "where": []})
        cur["count"] += 1
        cur["likes"] += max(like, 0)
        if where not in cur["where"]:
            cur["where"].append(where)

    def add_lead(ev: dict, like: int, where: str, user: str | None) -> None:
        for u in ev["urls"]:
            leads.append({"kind": "url", "value": u, "like": like,
                          "where": where, "by": user})
        for h in ev["handles"]:
            leads.append({"kind": "handle", "value": h, "like": like,
                          "where": where, "by": user})
        if not ev["urls"]:
            for d in ev["domains"]:
                leads.append({"kind": "domain", "value": d, "like": like,
                              "where": where, "by": user})

    for c in comments:
        ev = evidence(c["text"])
        add_lead(ev, c.get("like", 0), "top", c.get("user"))
        add_name(ev["cleaned"], c.get("like", 0), "top")
        for n in c.get("nested") or []:
            nev = evidence(n["text"])
            add_lead(nev, n.get("like", 0), "nested", n.get("user"))
            add_name(nev["cleaned"], n.get("like", 0), "nested")

    ranked = sorted(bag.values(), key=lambda x: (-x["count"], -x["likes"]))
    seen = set()
    uniq_leads = []
    for ld in sorted(leads, key=lambda x: (-x["like"])):
        key = (ld["kind"], ld["value"])
        if key in seen:
            continue
        seen.add(key)
        uniq_leads.append(ld)
    # Leads are returned in their OWN list, not appended to the name ranking.
    # Appending put them last, so `candidates(...)[:top]` silently cut the most
    # direct evidence in the thread (a resolved URL beat 15 guessed names).
    lead_rows = [
        {"token": f'{ld["kind"]}:{ld["value"]}', "count": ld["like"],
         "likes": ld["like"], "where": [ld["where"]],
         "lead": ld, "is_lead": True}
        for ld in uniq_leads
    ]
    return {"names": ranked, "leads": lead_rows}


def _fetch_nested(aid: int, root: int, declared: int, max_pages: int = 6) -> list[dict]:
    """All sub-replies of one comment, paging past the first 20.

    Measured: the reference answer (`甘樂`) sat in a sub-reply on a thread whose
    top-level replies carried no name at all. A fixed `pn=1&ps=20` therefore
    dropped the answer whenever it was the 21st reply -- and review reproduced
    exactly that (25 nested replies declared, 20 collected).
    """
    out: list[dict] = []
    seen: set = set()
    for pn in range(1, max_pages + 1):
        throttle("api.bilibili.com", gap=3.0)
        try:
            s = bili_json(BILI_SUB.format(aid=aid, root=root, pn=pn))
        except Exception:  # noqa: BLE001
            break
        if s.get("code") != 0:
            break
        data = s.get("data") or {}
        reps = data.get("replies") or []
        if not reps:
            break
        new = 0
        for x in reps:
            rp = x.get("rpid")
            if rp in seen:
                continue
            seen.add(rp)
            new += 1
            out.append({
                "user": (x.get("member") or {}).get("uname"),
                "text": (x.get("content") or {}).get("message") or "",
                "like": x.get("like") or 0,
            })
        page = data.get("page") or {}
        if len(out) >= declared or new == 0:
            break
        if page.get("count") and len(out) >= int(page["count"]):
            break
    return out


def fetch_comments(aid: int, max_pages: int = 8) -> tuple[list[dict], dict]:
    """Top-level comments across sort modes, plus nested replies.

    Transport selection matters more than anything else here. Measured on a
    38-reply thread:

      x/v2/reply/main                      3 replies, is_end=true, cannot page
      x/v2/reply/wbi/main (signed)         3 replies, identical
      + mobi_app=android&platform=android 19 replies, is_end=false, pages
      then next=2,3                        6 more, then stops at 25/38

    So the app-style param roughly 8x's the anonymous yield. `mobi_app` is a
    plain query flag, not a signature, and needs no session -- it just selects
    the app's looser reply policy. Remaining replies past the stall point
    generally require a real session (the cursor says `is_end` while
    `all_count` is still larger).
    """
    out: list[dict] = []
    seen: set[int] = set()
    meta: dict = {"transports": [], "declared_all_count": None}

    TRANSPORTS = (
        ("mobi_app", "&mobi_app=android&platform=android"),
        ("plain", ""),
    )

    def pull(url: str) -> dict | None:
        try:
            return bili_json(url, headers={"Referer": "https://www.bilibili.com/"})
        except Exception as e:  # noqa: BLE001
            note(f"reply fetch failed: {e}")
            return None

    for tname, extra in TRANSPORTS:
        if len(seen) and meta["declared_all_count"] and len(seen) >= meta["declared_all_count"]:
            break
        got_any = False
        for mode in (3, 2):
            nxt = 0
            for _ in range(max_pages):
                throttle("api.bilibili.com", gap=3.0)
                url = (
                    "https://api.bilibili.com/x/v2/reply/main"
                    f"?type=1&oid={aid}&mode={mode}&next={nxt}&ps=20{extra}"
                )
                j = pull(url)
                if not j or j.get("code") != 0:
                    break
                d = j.get("data") or {}
                cur = d.get("cursor") or {}
                if meta["declared_all_count"] is None:
                    meta["declared_all_count"] = cur.get("all_count")
                reps = d.get("replies") or []
                new = 0
                for r in reps:
                    rpid = r.get("rpid")
                    if rpid in seen:
                        continue
                    seen.add(rpid)
                    new += 1
                    got_any = True
                    out.append(
                        {
                            "rpid": rpid,
                            "user": (r.get("member") or {}).get("uname"),
                            "text": (r.get("content") or {}).get("message") or "",
                            "like": r.get("like") or 0,
                            "rcount": r.get("rcount") or 0,
                            "nested": [],
                        }
                    )
                    if r.get("rcount"):
                        out[-1]["nested"] = _fetch_nested(
                            aid, rpid, r.get("rcount") or 0)
                cur_next = cur.get("next") or 0
                if cur.get("is_end") or not cur_next or cur_next == nxt or new == 0:
                    break
                nxt = cur_next
            if got_any:
                break
        if got_any:
            meta["transports"].append(tname)
    return out, meta



def verify_bilibili(token: str, item_titles: list[str] | None = None) -> dict:
    import authorcheck

    return authorcheck.verify_bilibili(token, item_titles)


def _yt_search(query: str, n: int = 20) -> list[dict]:
    cmd = [
        sys.executable, "-m", "yt_dlp", "--flat-playlist", "-J", "--no-warnings",
        f"ytsearch{n}:{query}",
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=200)
        return json.loads(p.stdout).get("entries") or []
    except Exception as e:  # noqa: BLE001
        note(f"yt search failed for {query!r}: {e}")
        return []


def verify_youtube(token: str, item_titles: list[str] | None = None) -> dict:
    """Delegate to the shared, account-scoped checker.

    Review finding: this module and `alias_resolve.py` had near-identical logic,
    and both certified a channel using OTHER channels' genre-matching videos.
    `authorcheck` scopes every step to the candidate account and reports the
    steps separately.
    """
    return authorcheck.verify_youtube(token, item_titles)


def resolve_candidates(comments: list[dict], top: int) -> list[dict]:
    """Name candidates (capped at `top`) minus any direct lead.

    Leads come back FIRST and are never counted against the name budget. The
    previous shape appended them to the same list and the caller did
    `candidates(...)[:top]`, so a thread with 15 guessed names dropped the only
    resolved URL in it (measured: 2 leads at rank 17-18 -> 0 after [:15]).
    """
    got = candidates(comments)
    names = got["names"] if isinstance(got, dict) else got
    leads = got.get("leads", []) if isinstance(got, dict) else []
    return leads + names[:top]


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2
    ck = cookie_arg(argv)
    loaded = use_cookies(ck)
    argv = strip_opt(argv, "--cookies")
    if not argv:
        print(__doc__)
        return 2
    bvid = argv[0]
    do_verify = "--verify" in argv
    top = 15
    if "--top" in argv:
        top = int(argv[argv.index("--top") + 1])

    throttle("api.bilibili.com", gap=2.0)
    v = bili_json(BILI_VIEW.format(bvid=bvid))
    if v.get("code") != 0:
        emit({"ok": False, "error": "view failed", "bvid": bvid})
        return 1
    d = v["data"]
    aid = d["aid"]
    note(f"{bvid}: {d['stat'].get('reply')} replies declared")

    comments, meta = fetch_comments(aid)
    note(f"collected {len(comments)} top-level comments")
    if not comments:
        emit({"ok": True, "bvid": bvid, "comments": 0, "candidates": [],
              "session": session_state(),
              "note": "no comments — nothing to mine here"})
        return 0

    cands = resolve_candidates(comments, top)
    note(f"{len(cands)} candidate tokens")

    if do_verify:
        for c in cands:
            # A lead is a URL/handle, not a name: searching it as a name is
            # guaranteed to miss and was previously reported as "no finding".
            if c.get("is_lead"):
                c["bilibili"] = {"worth_pursuing": False, "candidates": [],
                                 "note": "lead: resolve the URL, do not search it "
                                         "as an author name"}
                c["youtube"] = dict(c["bilibili"])
                c["is_finding"] = False
                c["action"] = ("open the link and read the uploader it actually "
                               "points at")
                continue
            c["bilibili"] = verify_bilibili(c["token"])
            c["youtube"] = verify_youtube(c["token"])
            c["is_finding"] = bool(
                authorcheck.worth_pursuing(c["bilibili"])
                or authorcheck.worth_pursuing(c["youtube"])
            )

    if do_verify:
        cands.sort(key=lambda c: (not c.get("is_finding"), -c["count"], -c["likes"]))
    findings = [c for c in cands if c.get("is_finding")] if do_verify else []
    leads = [c for c in cands if c.get("is_lead")]
    unverified_leads = [c for c in leads if not c.get("is_finding")]

    emit(
        {
            "ok": True,
            "bvid": bvid,
            "aid": aid,
            "comments": len(comments),
            "declared_all_count": meta.get("declared_all_count"),
            "transports": meta.get("transports"),
            "session": session_state(),
            "cap_note": (
                "anonymous bilibili caps replies at 3 regardless of all_count; "
                "pass --cookies to read the full thread"
                if not session_state()["authenticated"]
                else "authenticated"
            ),
            "verified": do_verify,
            "findings": findings,
            "leads": leads,
            "candidates": cands,
            "next": _next_hint(findings, unverified_leads, meta),
        }
    )
    return 0


def _next_hint(findings: list[dict], leads: list[dict], meta: dict) -> str:
    if findings:
        top = findings[0]
        return (f"confirm {top['token']!r} with probe.py / channel.py, then check "
                f"that channel for the clip itself")
    if leads:
        return (f"{len(leads)} direct lead(s) present (URL/handle) -- open them "
                f"before searching for an author name; they were not searched")
    declared = meta.get("declared_all_count")
    got = meta.get("collected")
    if declared and got is not None and got < declared:
        return (f"only {got}/{declared} replies readable anonymously; pass "
                f"--cookies, or try deep_sweep.py / retitle_probe.py")
    return "no candidate verified as an author name; continue without comments"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
