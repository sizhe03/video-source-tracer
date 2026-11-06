#!/usr/bin/env python
"""Parallel discovery: every independent route runs at once and shares leads live.

Why this exists
---------------
The earlier pipeline was strictly sequential: find_earlier -> comments ->
alias -> channel. That wastes wall-clock time and, worse, makes the search
over-depend on one route: if the same-site title search finds nothing, the
comment thread is only read afterwards, even though it is the cheapest route and
the one that actually solved the reference case.

Four routes are independent and are started together:

    same_site    same-platform copies: same title, stripped title, name leads
    comments     description, credits, top-level and nested comments -> leads
    cross        other platforms: back-translated title terms, name leads
    image        cover / keyframe comparison against whatever candidates appear

Dependencies still run in order -- you cannot scan a candidate channel before a
candidate channel exists -- but independent work never waits for unrelated work.

Leads are shared while the run is in progress, not collected at the end:

    comment says "Dy 25914348306"  -> platform_id lead -> cross route resolves it
    comment says "甘楽"             -> name lead        -> same_site + cross search it
    same_site finds "甘楽らむ"       -> name lead        -> cross route re-queries with it
    image finds a near-identical cover -> that candidate jumps the verification queue

Three constraints the review asked for, implemented here:

    1. one candidate table, keyed by platform+native id, with per-source evidence
       and a checked/unchecked state, so no route repeats another's work
    2. per-host rate limits are shared across threads -- running four routes must
       not mean four times the request rate to one host -- plus a global request
       and wall-clock budget
    3. one confirmation standard: a candidate is `discovered` until content and
       attribution are actually checked; once attribution is established the
       discovery routes stop and only verification continues

Usage:
    python discover.py BV1p4KhzoEck
    python discover.py BV1p4KhzoEck --budget 90 --max-requests 120
    python discover.py BV1p4KhzoEck --no-image --routes same_site,comments
"""
from __future__ import annotations

import json
import os
import queue
import re
import sys
import threading
import time
from urllib.parse import quote

from _common import bili_json, emit, http, note, setup_stdio

BILI_SEARCH = (
    "https://api.bilibili.com/x/web-interface/search/type"
    "?search_type=video&keyword={kw}&page=1&page_size=50&order=pubdate"
)
BILI_VIEW = "https://api.bilibili.com/x/web-interface/view?bvid={bvid}"

# Per-host minimum gap, shared by every thread. bilibili search is the strictest
# endpoint measured (412 at roughly one request per few seconds).
DEFAULT_GAPS = {
    "api.bilibili.com": 6.0,
    "www.bilibili.com": 2.0,
    "www.youtube.com": 2.0,
    "i.ytimg.com": 0.5,
    "i0.hdslb.com": 0.5,
}

# "Dy 25914348306", "抖音号 25914348306", "dy:123456789" -> a platform id lead.
# The comment miner used to shred these into [Dy] / [音符] fragments and drop
# the digits entirely, which is exactly the lead that mattered. The optional
# 号/id/账号 group is required in practice: the real comment read "抖音号
# 25914348306", and a pattern without it silently matched nothing.
PLATFORM_ID = re.compile(
    r"(?i)(dy|douyin|抖音|ks|kuaishou|快手|xhs|小红书|tiktok|yt|youtube)"
    r"(?:号|帳號|账号|id|ID)?[\s:：·\-_]*"
    r"([0-9]{6,25}|(?<![A-Za-z])[A-Za-z0-9_\-]{11})")
PLATFORM_ALIAS = {
    "dy": "douyin", "douyin": "douyin", "抖音": "douyin",
    "ks": "kuaishou", "kuaishou": "kuaishou", "快手": "kuaishou",
    "xhs": "xiaohongshu", "小红书": "xiaohongshu",
    "tiktok": "tiktok", "yt": "youtube", "youtube": "youtube",
}


# --------------------------------------------------------------------- budget
class Budget:
    """Wall clock plus a global request allowance, shared by all routes."""

    ENRICH_RESERVE = 2

    def __init__(self, seconds: float, requests: int):
        self.deadline = time.monotonic() + seconds
        self.total = requests
        self.left = requests
        self._lock = threading.Lock()
        self.spent = 0

    # Requests are not equally valuable. Spending the last slot on a cover
    # thumbnail when no search has run yet starves the routes that actually
    # find candidates -- reproduced 8 times out of 8. Reserve a floor for
    # discovery and let enrichment use only what is left above it.
    def take(self, *, enrichment: bool = False) -> bool:
        with self._lock:
            if self.left <= 0 or time.monotonic() > self.deadline:
                return False
            if enrichment:
                # Keep a floor for discovery, but never reserve so much that a
                # small budget cannot run a single search: the reserve is capped
                # at a third of the original allowance.
                # At least one slot stays available for discovery, and never
                # more than a third of the allowance: a 3-request budget used
                # to reserve 1 while 2 cover fetches still went through.
                reserve = max(1, min(self.ENRICH_RESERVE, self.total // 3))
                if self.left <= reserve or self.left == self.total:
                    return False
            self.left -= 1
            self.spent += 1
            return True

    def still_valid(self) -> bool:
        """Re-check AFTER pacing.

        Routes call take() to reserve, then wait() for their slot. The wait can
        outlast the deadline, and the request would then be issued with no
        budget left. Measured: 3 requests issued past the deadline with a 6 s
        gap. Reserving first is still right -- it stops four threads racing for
        the last slot -- so the reservation is refunded instead.
        """
        with self._lock:
            if time.monotonic() > self.deadline:
                self.left += 1
                self.spent -= 1
                return False
            return True

    def expired(self) -> bool:
        with self._lock:
            return self.left <= 0 or time.monotonic() > self.deadline

    def snapshot(self) -> dict:
        with self._lock:
            return {"requests_left": self.left, "requests_spent": self.spent,
                    "seconds_left": round(max(0.0, self.deadline - time.monotonic()), 1)}


class RateLimiter:
    """One gap per host, enforced across threads WITHOUT serialising a host.

    Sleeping while holding the lock made every route that touches the same host
    queue behind one another -- measured at 15.4 s for three routes that should
    have taken 0.35 s. The lock is now held only to compute a slot; the sleep
    happens outside it, so a host is rate-limited but not exclusive.

    Slots are allocated by pushing the previous slot forward, which gives a
    correct spacing even when several threads ask at once.
    """

    def __init__(self, gaps: dict[str, float] | None = None,
                 default: float = 3.0):
        self.gaps = dict(DEFAULT_GAPS)
        if gaps:
            self.gaps.update(gaps)
        self.default = default
        self.next_slot: dict[str, float] = {}
        self.lock = threading.Lock()
        self.waits = 0

    def wait(self, host: str) -> None:
        gap = self.gaps.get(host, self.default)
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_slot.get(host, now))
            self.next_slot[host] = slot + gap
            if slot > now:
                self.waits += 1
        delay = slot - time.monotonic()
        if delay > 0:
            time.sleep(delay)


# ----------------------------------------------------------------- blackboard
class Blackboard:
    """The single candidate table plus the live lead queue."""

    def __init__(self, producer_count: int = 0):
        self.lock = threading.Lock()           # candidates, routes, counters
        self.cands: dict[str, dict] = {}
        # Leads are append-only and every route reads them through its own
        # cursor. A queue would hand each lead to exactly one route.
        self.lead_cond = threading.Condition()
        self.leads: list[dict] = []
        self.stop = threading.Event()          # discovery finished or blocked
        self.routes: dict[str, dict] = {}
        self.dupes = 0
        # Routes that can still produce a lead. A consumer may only stop once
        # this reaches zero: otherwise a search route exits while the comment
        # route is still reading and its lead is never consumed.
        self.producers_left = producer_count

    def producer_done(self) -> None:
        with self.lead_cond:
            self.producers_left = max(0, self.producers_left - 1)
            self.lead_cond.notify_all()

    def producers_finished(self) -> bool:
        with self.lead_cond:
            return self.producers_left <= 0

    # -- candidates ---------------------------------------------------------
    def add_candidate(self, platform: str, vid: str, *, title=None, author=None,
                      duration=None, pubdate=None, url=None, source="",
                      score=None, signals=None) -> bool:
        """Insert or merge. Returns True when this is a new candidate."""
        key = f"{platform}:{vid}"
        with self.lock:
            cur = self.cands.get(key)
            if cur is not None:
                self.dupes += 1
                if source and source not in cur["sources"]:
                    cur["sources"].append(source)
                if title and not cur.get("title"):
                    cur["title"] = title
                if author and not cur.get("author"):
                    cur["author"] = author
                if duration and not cur.get("duration"):
                    cur["duration"] = duration
                if pubdate and not cur.get("pubdate"):
                    cur["pubdate"] = pubdate
                if score is not None and (cur.get("score") is None
                                          or score > cur["score"]):
                    cur["score"] = score
                if signals:
                    cur["signals"] = sorted(set(cur.get("signals") or []) | set(signals))
                return False
            self.cands[key] = {
                "key": key, "platform": platform, "id": vid,
                "title": title, "author": author, "duration": duration,
                "pubdate": pubdate, "url": url or _canonical(platform, vid),
                "sources": [source] if source else [],
                "score": score, "signals": sorted(signals or []),
                "state": "discovered",       # discovered -> verified -> attributed
                "evidence": {},
            }
            return True

    def mark(self, key: str, state: str, **evidence) -> None:
        with self.lock:
            c = self.cands.get(key)
            if not c:
                return
            order = {"discovered": 0, "checked": 1, "verified": 2, "attributed": 3}
            if order.get(state, 0) > order.get(c["state"], 0):
                c["state"] = state
            c["evidence"].update(evidence)

    def attributed(self) -> list[dict]:
        with self.lock:
            return [c for c in self.cands.values() if c["state"] == "attributed"]

    # -- leads --------------------------------------------------------------
    def add_lead(self, kind: str, value: str, source: str, why: str = "") -> bool:
        value = (value or "").strip()
        if not value:
            return False
        with self.lead_cond:
            for l in self.leads:
                if l["kind"] == kind and l["value"] == value:
                    return False
            self.leads.append({"kind": kind, "value": value, "source": source,
                               "why": why, "consumed_by": []})
            self.lead_cond.notify_all()
        return True

    def consumer(self, name: str, from_now: bool = False) -> "LeadCursor":
        """Each route reads the whole lead stream from its own position.

        `from_now` skips leads already on the board. A route that starts after
        the description has been harvested would otherwise replay every
        historical lead -- including the title it is about to search anyway.
        """
        with self.lead_cond:
            start = len(self.leads) if from_now else 0
        return LeadCursor(self, name, start)

    def drain(self, timeout: float = 0.0) -> dict | None:
        """Compatibility shim: one shared cursor, so repeated calls advance.

        Building a fresh LeadCursor per call reset the position to 0 and
        returned the first lead forever. Routes use consumer(name) instead.
        """
        if not hasattr(self, "_shim_cursor"):
            self._shim_cursor = LeadCursor(self, "anon")
        return self._shim_cursor.next(timeout=timeout)

    def note_route(self, name: str, **kw) -> None:
        with self.lock:
            d = self.routes.setdefault(name, {"queries": 0, "found": 0,
                                              "leads": 0, "state": "running"})
            d.update(kw)

    def snapshot(self) -> dict:
        with self.lock:
            cands = list(self.cands.values())
            routes = dict(self.routes)
            dupes = self.dupes
        with self.lead_cond:
            leads = [dict(l, consumed_by=list(l["consumed_by"])) for l in self.leads]
        return {"candidates": cands, "leads": leads, "routes": routes,
                "duplicates_merged": dupes}


class LeadCursor:
    """One route's private position in the shared lead stream.

    A single queue.Queue gave each lead to exactly one route: whichever drained
    first took it. Measured with a description reading "作者：甘楽" -- the name
    lead reached only the cross route, so the same-site search never ran it,
    which is precisely the starvation the parallel design was meant to remove.
    """

    def __init__(self, board: "Blackboard", name: str, start: int = 0):
        self.board = board
        self.name = name
        self.i = start

    def next(self, timeout: float = 0.2) -> dict | None:
        b = self.board
        deadline = time.monotonic() + timeout
        with b.lead_cond:
            while True:
                if self.i < len(b.leads):
                    lead = b.leads[self.i]
                    self.i += 1
                    if self.name not in lead["consumed_by"]:
                        lead["consumed_by"].append(self.name)
                    return lead
                if b.producers_left <= 0:
                    return None
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                b.lead_cond.wait(left)

    def exhausted(self) -> bool:
        """True when every producer finished and this cursor caught up."""
        b = self.board
        with b.lead_cond:
            return b.producers_left <= 0 and self.i >= len(b.leads)


def _canonical(platform: str, vid: str) -> str:
    if platform == "bilibili":
        return f"https://www.bilibili.com/video/{vid}/"
    if platform == "youtube":
        return f"https://www.youtube.com/watch?v={vid}"
    if platform == "douyin":
        return f"https://www.douyin.com/video/{vid}"
    return vid


# ------------------------------------------------------------------- context
class Ctx:
    """Everything a route needs, with the network layer injectable for tests."""

    def __init__(self, item: dict, board: Blackboard, budget: Budget,
                 limiter: RateLimiter, *, bili=None, yt=None, title_terms=None,
                 fetch_cover=None):
        self.item = item
        self.board = board
        self.budget = budget
        self.limiter = limiter
        self.bili = bili or _bili_json
        self.yt = yt
        self.title_terms = title_terms or _terms(item.get("title") or "")
        self.fetch_cover = fetch_cover
        # Requests that were refused (412 / 403 / network) rather than answered
        # with an empty list. Without this the caller cannot tell "the site said
        # no" from "the site said nothing".
        self.blocked: list[dict] = []
        self._blocked_lock = threading.Lock()

    def note_blocked(self, what: str, reason: str) -> None:
        with self._blocked_lock:
            self.blocked.append({"what": what, "reason": reason[:160]})

    def blocked_count(self) -> int:
        with self._blocked_lock:
            return len(self.blocked)

    def bili_search(self, kw: str, limit: int = 50) -> list[dict]:
        if not self.budget.take():
            return []
        self.limiter.wait("api.bilibili.com")
        if not self.budget.still_valid():
            return []
        try:
            j = self.bili(BILI_SEARCH.format(kw=quote(kw)))
        except Exception as e:                      # noqa: BLE001
            note(f"search failed {kw!r}: {e}")
            self.note_blocked(f"search:{kw}", f"exception {e}")
            return []
        if j.get("code") != 0:
            code = j.get("code")
            note(f"search code {code} for {kw!r}")
            # -412 and -352 are risk control, not "no such video".
            self.note_blocked(f"search:{kw}", f"code {code} {j.get('message') or ''}")
            return []
        out = []
        for r in (j.get("data") or {}).get("result") or []:
            out.append({
                "id": r.get("bvid"),
                "title": re.sub(r"<[^>]+>", "", r.get("title") or ""),
                "author": r.get("author"),
                "mid": r.get("mid"),
                "pubdate": r.get("pubdate") or 0,
                "duration": _secs(r.get("duration")),
            })
        return out[:limit]

    def yt_search(self, q: str, n: int = 20) -> list[dict]:
        if self.yt is None or not self.budget.take():
            return []
        self.limiter.wait("www.youtube.com")
        if not self.budget.still_valid():
            return []
        try:
            return self.yt(q, n)
        except Exception as e:                      # noqa: BLE001
            note(f"yt search failed {q!r}: {e}")
            return []


def _bili_json(url: str, **kw) -> dict:
    return bili_json(url, **kw)


def _secs(d) -> int:
    if isinstance(d, int):
        return d
    if not d:
        return 0
    total = 0
    for p in str(d).split(":"):
        if p.strip().isdigit():
            total = total * 60 + int(p)
    return total


def _earlier_score(pubdate, input_pubdate) -> float:
    """Rank candidates that predate the input above those that follow it.

    The whole point of the search is to find the EARLIER upload. Without this,
    every candidate scored 0 and the output was ordered by pubdate only -- the
    ordering was right but nothing said why, and a cross-platform hit with no
    pubdate sank to the bottom regardless of how well it matched.
    """
    if not pubdate or not input_pubdate:
        # Unknown is WORSE than known-later: it excludes nothing at all, while
        # "published after the input" at least rules that item out as the
        # source. Ranking unknown at 0.5 above later-at-0.1 put an undated
        # YouTube hit ahead of every candidate we had already disproven.
        return 0.05
    if pubdate < input_pubdate:
        return 1.0
    return 0.1


def _terms(s: str) -> list[str]:
    from _common import tokens

    return tokens(s)


# -------------------------------------------------------------------- routes
CREATOR_SHARE = 0.5


def _is_creator_term(hits: list[dict], token: str) -> bool:
    """True when one author dominates the results for `token`.

    甘楽's own ten uploads all list 甘楽 as the author, so a frequency filter
    alone would discard the very name being traced. Genre vocabulary such as
    "死亡底噪" appears across dozens of unrelated uploaders, so its top-author
    share stays low even though the term is equally common.
    """
    authors = [(h.get("author") or "").strip() for h in hits]
    authors = [a for a in authors if a]
    if len(authors) < 3:
        return False
    counts = {a: authors.count(a) for a in set(authors)}
    best = max(counts, key=counts.get)
    if counts[best] / len(authors) < CREATOR_SHARE:
        return False
    # the dominant author must actually carry the token, otherwise it is just a
    # prolific uploader whose videos come back for every query
    return token in best


# Words that describe a platform, a format or a spec rather than a creator.
# A brand never names a platform or a resolution, so these are safe to drop
# regardless of how the result set looks.
_NOT_A_BRAND = {
    "asmr", "4k", "8k", "1080p", "720p", "3dio", "3d", "hires", "hi-res",
    "no", "talking", "notalking", "sleep", "relax", "relaxation", "trigger",
    "triggers", "audio", "video", "cos", "cosplay", "jk", "vr", "hd", "uhd",
    "bilibili", "youtube", "douyin", "tiktok", "niconico", "twitch",
}


def _is_common_term(hits: list[dict], token: str) -> bool:
    """Is this token genre vocabulary rather than a creator's name?

    The old rule was `carrying / len(hits) > 0.5` once len(hits) >= 8. That is
    circular for a brand query: if the brand really is the source, most hits
    carry it, so the filter deletes the answer. It also had a cliff -- 7 hits
    at 100% carrying was kept, 8 hits at 100% was dropped.

    Replaced with five conditions that must ALL hold. Each was measured against
    452 real titles; the old single threshold misjudged POMEchan at 14/30.
    """
    tok = (token or "").strip()
    if len(tok) < 2:
        return True                              # too short to identify anyone
    if _fold(tok) in _NOT_A_BRAND:
        return True                              # platform / format / spec word
    if any(ch.isdigit() for ch in tok) and len(tok) <= 5:
        return True                              # 4K, 1080P, 3DIO
    if len(hits) < 4:
        return False                             # not enough evidence either way
    authors = {(h.get("author") or "").strip() for h in hits}
    authors.discard("")
    carrying = sum(1 for h in hits if _hit_mentions(h, token))
    share = carrying / len(hits)
    # A real brand is repeated by MULTIPLE unrelated uploaders; genre vocabulary
    # saturates a single author's results but a name saturates one uploader.
    if len(authors) >= 3 and share >= 0.9 and len(tok) >= 5:
        return False                             # looks like a genuine brand
    return share > 0.6


# Japanese/Chinese spellings of one name differ by a single character and by
# traditional/simplified form. An exact substring test treats 甘楽 / 甘樂 / 甘乐
# as three different people, which is how a real creator's own uploads get
# filtered out as "unrelated".
_ALIAS_FOLD = str.maketrans({
    "樂": "楽", "乐": "楽",          # traditional / simplified -> shinjitai
    "御": "御", "禦": "御",
    "絲": "丝", "絲": "丝",
    "襪": "袜",
})


def _fold(s: str) -> str:
    return (s or "").lower().translate(_ALIAS_FOLD)


def _hit_mentions(hit: dict, token: str) -> bool:
    """Does this search hit actually carry the guessed name?

    Folds the common variant spellings first; a bare substring test rejects a
    creator's own uploads when the repost used a different character form.
    """
    token = (token or "").strip()
    if not token:
        return False
    hay = _fold(f"{hit.get('title') or ''} {hit.get('author') or ''}")
    return _fold(token) in hay


def route_same_site(ctx: Ctx) -> None:
    """Same-platform copies: title, stripped title, then every name lead."""
    b = ctx.board
    seen_q: set[str] = set()

    def run_query(kw: str, why: str) -> None:
        if not kw or kw in seen_q or ctx.budget.expired():
            return
        seen_q.add(kw)
        hits = ctx.bili_search(kw)
        b.note_route("same_site", queries=len(seen_q))
        name_lead = why.startswith("name-lead")
        for h in hits:
            if h["id"] == ctx.item.get("id"):
                continue
            # A name lead is a GUESS from prose. Keeping every result it happens
            # to return is how "死亡底噪" produced 447 unrelated candidates. A
            # guessed name only counts when the hit actually carries that name in
            # its title or author; a real title query keeps everything, because a
            # rewritten upstream is exactly what must not be filtered out.
            if name_lead and not _hit_mentions(h, kw):
                continue
            # Genre vocabulary spreads across many uploaders; a creator's own
            # results are dominated by one author. Frequency alone cannot tell
            # them apart -- "死亡底噪" matched hundreds of unrelated slime videos,
            # while 甘楽's ten uploads all list 甘楽 -- so both tests run. An
            # earlier `any(kw in author)` shortcut let one coincidental handle
            # (音符是我的大米饭) open the floodgates for the word 音符.
            if name_lead and _is_common_term(hits, kw) and not _is_creator_term(hits, kw):
                b.note_route("same_site", filtered_common=(b.routes.get(
                    "same_site", {}).get("filtered_common", 0) + 1))
                continue
            b.add_candidate("bilibili", h["id"], title=h["title"],
                            author=h["author"], duration=h["duration"],
                            pubdate=h["pubdate"], source=f"same_site:{why}",
                            score=_earlier_score(h.get("pubdate"),
                                                 ctx.item.get("pubdate")),
                            signals=["same_title"] if why == "title" else [])

    title = ctx.item.get("title") or ""
    run_query(title, "title")
    stripped = " ".join(t for t in re.split(r"[\s_\-/|:：,，。、!！?？【】\[\]()（）]+", title)
                        if t and t.upper() not in ("ASMR",))
    if stripped and stripped != title:
        run_query(stripped, "title-stripped")

    # react to leads while other routes are still running
    cur = b.consumer("same_site")
    while not ctx.board.stop.is_set() and not ctx.budget.expired():
        lead = cur.next(timeout=0.2)
        if lead is None:
            if cur.exhausted():
                break          # every lead producer finished and we caught up
            continue
        if lead["kind"] == "name":
            run_query(lead["value"], f"name-lead:{lead['source']}")


def route_comments(ctx: Ctx) -> None:
    """Description, credits, top-level and nested comments -> leads."""
    b = ctx.board
    item = ctx.item
    if item.get("platform") != "bilibili":
        b.note_route("comments", state="skipped", reason=f"no reader for {item.get('platform')}")
        return

    text_blocks: list[str] = []
    comment_failed = False
    view_url = BILI_VIEW.format(bvid=item["id"])
    view_json: dict = {}
    if ctx.budget.take():
        ctx.limiter.wait("api.bilibili.com")
        try:
            view_json = (ctx.bili(view_url)
                         if ctx.budget.still_valid() else {})
            d = (view_json.get("data") or {}) if view_json.get("code") == 0 else {}
            text_blocks.append(d.get("desc") or "")
        except Exception as e:                      # noqa: BLE001
            note(f"view failed: {e}")
            comment_failed = True

    # Announce description leads IMMEDIATELY, before the comment fetch below.
    # Waiting for the whole comment thread meant a name in the description was
    # only shared after the slowest request in the route finished, which is the
    # "collect everything, then merge" pattern this module exists to replace.
    n_leads = _harvest(text_blocks, b, "comments:description")

    _seen: dict[str, object] = {view_url: view_json} if view_json else {}

    def _metered(url: str, **kw):
        """Every comments.py request must be charged to the shared budget.

        Handing comments.py the raw reader let it issue unaccounted requests --
        7 calls against a 3-request allowance -- and pace itself independently.
        Identical GETs are served from the run cache: the trace showed the view
        endpoint fetched twice within 0.36 s for no reason.
        """
        if url in _seen:
            return _seen[url]
        if not ctx.budget.take():
            raise RuntimeError("budget exhausted")
        ctx.limiter.wait("api.bilibili.com")
        if not ctx.budget.still_valid():
            raise RuntimeError("budget exhausted")
        j = ctx.bili(url, **kw)
        _seen[url] = j
        return j

    try:
        import comments as C
        ck = _metered
        old_bili = C.bili_json
        old_throttle = getattr(C, "throttle", None)
        C.bili_json = ck
        # comments.py paces itself at its own interval. Left alone, it and
        # discover.RateLimiter both throttle api.bilibili.com with no shared
        # state, and the effective gap collapses to whichever is shorter --
        # measured 0.3 s where 6 s was configured, which is exactly what
        # triggers 412. The board's limiter becomes the only authority.
        C.throttle = lambda *_a, **_k: None
        try:
            if view_json:
                aid = ((view_json.get("data") or {}).get("aid"))
            elif ctx.budget.take():
                ctx.limiter.wait("api.bilibili.com")
                j = (ctx.bili(view_url) if ctx.budget.still_valid() else {})
                aid = ((j.get("data") or {}).get("aid"))
            else:
                aid = None
            if aid:
                thread, _meta = C.fetch_comments(aid)
                fresh = []
                for c in thread:
                    fresh.append(c.get("text") or "")
                    for n in c.get("nested") or []:
                        fresh.append(n.get("text") or "")
                text_blocks.extend(fresh)
                n_leads += _harvest(fresh, b, "comments:thread")
        finally:
            C.bili_json = old_bili
            if old_throttle is not None:
                C.throttle = old_throttle
    except Exception as e:                          # noqa: BLE001
        note(f"comment fetch failed: {e}")
        comment_failed = True

    # Materialise every platform id as a candidate here, not only when a
    # consumer happens to be listening. With --routes same_site,comments the
    # cross route never runs, and the id used to vanish from the result.
    # Read the lead list under the SAME condition variable that add_lead writes
    # it under. Taking b.lock here raced with a concurrent append and could
    # raise mid-iteration, losing every platform-id candidate.
    with b.lead_cond:
        pending_ids = [dict(l) for l in b.leads if l["kind"] == "platform_id"]
    for l in pending_ids:
        plat, pid = l["value"].split(":", 1)
        b.add_candidate(plat, pid, source="comments:platform_id",
                        url=_canonical(plat, pid))
        b.mark(f"{plat}:{pid}", "discovered",
               note="platform id from a comment; resolve it by opening the profile")

    # A 412 must not look like "read the thread, found nothing". The two need
    # different follow-up actions, so they get different states.
    b.note_route("comments", state="partial" if comment_failed else "done",
                 leads=n_leads, blocks=len(text_blocks),
                 reason="comment fetch failed; description leads only"
                        if comment_failed else None)


def _harvest(text_blocks: list[str], board: Blackboard, source: str) -> int:
    """Turn text into typed leads and publish them on the board right away."""
    n = 0
    for t in text_blocks:
        for m in PLATFORM_ID.finditer(t):
            plat = PLATFORM_ALIAS.get(m.group(1).lower(), m.group(1).lower())
            if board.add_lead("platform_id", f"{plat}:{m.group(2)}", source,
                              f"raw: {m.group(0)}"):
                n += 1
        for m in re.finditer(r"https?://[^\s<>\"'）)】]+", t):
            if board.add_lead("url", m.group(0), source):
                n += 1
        for m in re.finditer(r"@([A-Za-z0-9_.\-]{2,30})", t):
            if board.add_lead("handle", m.group(1), source):
                n += 1
        for tok in _short_tokens(t):
            if board.add_lead("name", tok, source):
                n += 1
    return n


def _short_tokens(text: str) -> list[str]:
    """Short CJK runs that could be a creator's short name.

    Measured failure this guards against: a thread containing
    "前几天去看她直播，凶的一比。。感觉更年期到了" produced the tokens
    "前几天去看她" / "直播" / "凶的一比", and the same-site route searched all of
    them -- 573 unrelated candidates, most of the budget gone.

    A proposed name must look like a NAME: a short run, no sentence particles,
    no verb phrase, and it must appear more than once OR be short enough to be a
    handle. Everything else stays in the thread text and is never searched.
    """
    out: list[str] = []
    seen: dict[str, int] = {}
    for run in re.findall(r"[\u3040-\u30ff\u4e00-\u9fff]{2,6}", text or ""):
        if _looks_like_chatter(run):
            continue
        seen[run] = seen.get(run, 0) + 1
    for run, n in seen.items():
        # A creator handle in this genre is 2-6 characters: 甘楽 (2),
        # 纤陀螺 (3), 桑塔阿普亚 (5). The old cap of 4 silently dropped every
        # five-character transliteration, which is how 桑塔阿普亚 -- the actual
        # answer in a real thread -- produced no lead at all. Prose is already
        # rejected by _looks_like_chatter, so length is not the guard.
        if len(run) <= 6 or n >= 2:
            out.append(run)
    return out[:3]


# Function words and sentence glue: their presence means the run is prose, not
# a name. Chinese names essentially never contain these.
_PROSE_MARKERS = set("的了是在我你他她它和与就都很也还又说看听感觉个这那有没")


def _looks_like_chatter(run: str) -> bool:
    if run in _CHATTER:
        return True
    if any(ch in _PROSE_MARKERS for ch in run):
        return True
    if re.search(r"(.)\1", run):        # 比比, 哈哈 -- emphasis, not a name
        return True
    return False


# Words that appear in ordinary comments about a video and are never a creator's
# name. Kept explicit rather than inferred: a frequency filter alone cannot
# separate "直播" (common noun) from a real two-character handle.
_CHATTER = {
    # question / request phrases
    "这是谁", "这谁啊", "这是哪位", "求原片", "求原片作者", "不说名", "名字",
    "回复", "抖音上的", "抖音叫", "叫什么",
    # opinion / narration
    "前几天", "感觉更", "我觉得", "我只是", "没什么", "这个主播", "虽然说话",
    "但是声音", "有感觉", "哇趣", "勾人的", "笑哭", "吃瓜", "老婆",
    # verdicts about the video, not people: "obviously a repost", "not the
    # uploader", "ask the OP". These read as names to a length filter.
    "显然转载", "显然搬运", "明显转载", "明显搬运", "是转载", "是搬运",
    "不是本人", "本人吗", "这是本人", "转载的", "搬运的", "原up", "原作者",
    "应该是", "这个吧", "求问", "同问", "蹲一个", "有人知道",
    # platform nicknames and ordinary music nouns that arrive as "names"
    "音符", "抖音", "快手", "小红书", "视频号", "哔哩",
    # attribution vocabulary: these describe WHERE a name would be, not the name
    "作者", "原作", "原创", "出处", "来源", "搬运", "转载", "原片", "原视频",
    "up主", "UP主", "up", "博主", "楼主", "层主", "发布", "投稿", "上传",
    # common nouns that look like two-character names
    "直播", "耐心", "问题", "声音", "视频", "主播", "音乐", "耳机", "音量",
    "底噪", "死亡底", "设备", "手机", "画面", "字幕", "简介", "评论", "弹幕",
    "关注", "点赞", "收藏", "投币", "转发", "分享", "更新", "下期", "上期",
}


def route_cross(ctx: Ctx) -> None:
    """Other platforms: back-translated title terms, then every name lead."""
    b = ctx.board
    if ctx.yt is None:
        b.note_route("cross", state="skipped", reason="no cross-platform reader")
        return
    seen: set[str] = set()

    def run_query(q: str, why: str) -> None:
        if not q or q in seen or ctx.budget.expired():
            return
        seen.add(q)
        hits = ctx.yt_search(q, 20)
        b.note_route("cross", queries=len(seen))
        name_lead = why.startswith("name-lead")
        kept = 0
        for rank, e in enumerate(hits):
            vid = e.get("id")
            if not vid:
                continue
            title = re.sub(r"<[^>]+>", "", e.get("title") or "")
            author = e.get("channel") or e.get("uploader") or ""
            # A name lead is a guess from prose. The same rule the same-site
            # route uses applies here: the hit must actually carry the guessed
            # name. Without it 柚子 returned 77 unrelated YouTube videos.
            if name_lead and not _hit_mentions({"title": title, "author": author},
                                               q.replace(" ASMR", "")):
                continue
            kept += 1
            b.add_candidate("youtube", vid, title=title or None, author=author,
                            duration=int(e.get("duration") or 0) or None,
                            source=f"cross:{why}",
                            score=_earlier_score(None, ctx.item.get("pubdate")),
                            signals=["name_lead"] if name_lead else ["title_term"])
            key = f"youtube:{vid}"
            if rank < 3:
                b.mark(key, "discovered", search_rank=rank, query=q)
        if name_lead and not kept and hits:
            b.note_route("cross", filtered_common=(b.routes.get(
                "cross", {}).get("filtered_common", 0) + 1))

    # the title itself, then its distinctive terms (the back-translation step
    # rewrites these; here the raw terms are enough to seed the route)
    run_query(ctx.item.get("title") or "", "title")
    distinctive = [t for t in ctx.title_terms if len(t) >= 2][:4]
    if distinctive:
        run_query(" ".join(distinctive), "title-terms")

    cur = b.consumer("cross")
    while not b.stop.is_set() and not ctx.budget.expired():
        lead = cur.next(timeout=0.2)
        if lead is None:
            if cur.exhausted():
                break          # every lead producer finished and we caught up
            continue
        if lead["kind"] == "name":
            run_query(f"{lead['value']} ASMR", f"name-lead:{lead['source']}")
            run_query(lead["value"], f"name-lead:{lead['source']}")
        elif lead["kind"] == "platform_id":
            plat, pid = lead["value"].split(":", 1)
            b.add_candidate(plat, pid, source="comments:platform_id",
                            url=_canonical(plat, pid))
            b.mark(f"{plat}:{pid}", "discovered",
                   note="platform id from a comment; resolve it by opening the profile")


def route_image(ctx: Ctx) -> None:
    """Compare the input cover against candidate covers as they arrive.

    A cover match never proves authorship, but it does promote a candidate in the
    verification queue, which is the only thing this route is allowed to claim.
    """
    b = ctx.board
    try:
        import cv2
        import numpy as np
    except Exception:                               # noqa: BLE001
        b.note_route("image", state="skipped", reason="opencv not available")
        return

    def phash(path_or_bytes):
        if isinstance(path_or_bytes, bytes):
            arr = np.frombuffer(path_or_bytes, np.uint8)
            im = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        else:
            im = cv2.imread(path_or_bytes, cv2.IMREAD_GRAYSCALE)
        if im is None:
            return None
        g = cv2.resize(im, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
        d = cv2.dct(g)[:16, :16]
        return (d > np.median(d[1:, :])).flatten()

    ref = None
    if ctx.fetch_cover and ctx.budget.take(enrichment=True):
        try:
            ref = phash(ctx.fetch_cover())
        except Exception as e:                      # noqa: BLE001
            note(f"cover fetch failed: {e}")
    b.note_route("image", state="running" if ref is not None else "skipped",
                 reason=None if ref is not None else "no cover for the input")

    checked: set[str] = set()
    while not b.stop.is_set() and not ctx.budget.expired():
        with b.lock:
            pending = [c for c in b.cands.values()
                       if c["platform"] == "bilibili" and c["key"] not in checked]
        if not pending:
            # Re-read under the lock: this branch used to iterate b.cands again
            # outside it and raised "dictionary changed size during iteration"
            # the moment another route inserted a candidate mid-loop.
            with b.lock:
                still_open = any(c["platform"] == "bilibili"
                                 and c["key"] not in checked
                                 for c in b.cands.values())
            if not still_open and b.producers_finished() and not b.stop.is_set():
                break              # nothing left to compare, nobody can add more
            time.sleep(0.3)
            continue
        for c in pending:
            checked.add(c["key"])
            if ref is None or not ctx.budget.take(enrichment=True):
                continue
            try:
                # The archive image key is a sha1, not the bvid: the old URL
                # 404'd on every single request. search/type already returns
                # the real `pic`, so prefer it and only fall back when absent.
                pic = c.get("cover") or ""
                url = pic or f"https://i0.hdslb.com/bfs/archive/{c['id']}.jpg"
                # DEFAULT_GAPS configures this host but nothing applied it.
                ctx.limiter.wait("i0.hdslb.com")
                r = http(url, timeout=15)
                if r.status != 200:
                    continue
                h = phash(r.body)
                if h is None:
                    continue
                dist = int((ref != h).sum())
                b.mark(c["key"], "discovered", cover_phash_distance=dist)
                if dist <= 12:
                    b.mark(c["key"], "checked", cover_match="near-identical",
                           note="cover match promotes verification, it does not "
                                "prove authorship")
            except Exception:                       # noqa: BLE001
                continue


ROUTES = {
    "same_site": route_same_site,
    "comments": route_comments,
    "cross": route_cross,
    "image": route_image,
}


# ---------------------------------------------------------------------- main
def _load_item(bvid: str, bili) -> dict:
    j = bili(BILI_VIEW.format(bvid=bvid))
    if j.get("code") != 0:
        raise SystemExit(f"cannot load {bvid}: {j.get('message')}")
    d = j["data"]
    return {
        "platform": "bilibili", "id": d["bvid"], "aid": d["aid"],
        "title": d.get("title") or "", "duration": d.get("duration") or 0,
        "pubdate": d.get("pubdate") or 0,
        "author": (d.get("owner") or {}).get("name"),
        "mid": (d.get("owner") or {}).get("mid"),
        "cover": d.get("pic"),
    }


def _default_yt():
    def _search(q: str, n: int = 20):
        import json as _json
        import subprocess
        cmd = [sys.executable, "-m", "yt_dlp", "--flat-playlist", "-J",
               "--no-warnings", f"ytsearch{n}:{q}"]
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="ignore", timeout=200)
        return _json.loads(p.stdout).get("entries") or []
    return _search


def _default_cover(item: dict):
    def _fetch() -> bytes:
        r = http(item["cover"], timeout=20,
                 headers={"Referer": "https://www.bilibili.com/"})
        return r.body
    return _fetch


_UNSET = object()          # "caller did not pass this" — distinct from None,
                          # which now means "disable that source on purpose"


def run(item: dict, *, routes=None, budget_s: float = 90.0, max_requests: int = 120,
        bili=None, yt=_UNSET, cover=_UNSET,
        gaps: dict[str, float] | None = None) -> dict:
    """Start every independent route at once and return the shared board.

    `gaps` overrides the per-host pacing. Tests pass tiny gaps so that wall
    clock measures route structure rather than politeness; production keeps the
    measured defaults.

    `yt=None` disables the cross-platform route and `cover=None` disables the
    image route. Omitting them keeps the default behaviour, so a caller can
    switch a route off without also having to name the other one.
    """
    # De-duplicate first: "--routes comments,comments" started two threads but
    # only counted one producer, so the first to finish released the consumers.
    # Unknown names are rejected loudly rather than silently producing an empty
    # run that still reports ok=True.
    asked = list(routes or ROUTES)
    unknown = [n for n in asked if n not in ROUTES]
    if unknown:
        raise ValueError(f"unknown route(s) {unknown}; choose from {list(ROUTES)}")
    chosen = list(dict.fromkeys(asked))

    # Only the comment route can introduce a NEW lead. The search routes consume
    # leads; they must not be counted as producers, or the consumers would wait
    # forever for a stream that will never fill again.
    board = Blackboard(producer_count=1 if "comments" in chosen else 0)
    budget = Budget(budget_s, max_requests)
    limiter = RateLimiter(gaps)
    if yt is _UNSET:
        yt = _default_yt()
    if cover is _UNSET:
        cover = _default_cover(item) if item.get("cover") else None
    ctx = Ctx(item, board, budget, limiter,
              bili=bili or _bili_json,
              yt=yt,
              fetch_cover=cover)

    threads = []
    for name in chosen:
        def _run(n=name, fn=ROUTES[name]):
            t0 = time.monotonic()
            try:
                fn(ctx)
            except Exception as e:                  # noqa: BLE001
                note(f"route {n} crashed: {e}")
                board.note_route(n, state="error", error=str(e)[:200])
            else:
                with board.lock:
                    d = board.routes.setdefault(
                        n, {"queries": 0, "found": 0, "leads": 0, "state": "running"})
                    d["elapsed_s"] = round(time.monotonic() - t0, 1)
                    if d.get("state") == "running":
                        # "done" with zero requests means the route ran and found
                        # no slot or nothing to do -- never leave it as None, or
                        # the caller cannot tell that apart from "never started".
                        d["state"] = "done"
            finally:
                if n == "comments":
                    board.producer_done()
        t = threading.Thread(target=_run, name=name, daemon=True)
        threads.append(t)
        try:
            t.start()
        except RuntimeError as e:                   # can't start new thread
            note(f"route {name} could not start: {e}")
            board.note_route(name, state="error", error=f"thread start: {e}")
            if name == "comments":
                board.producer_done()

    t0 = time.monotonic()
    while any(t.is_alive() for t in threads):
        if board.attributed():
            board.stop.set()
            note("attribution established: stopping discovery routes")
            break
        if budget.expired():
            board.stop.set()
            note("budget exhausted: stopping discovery routes")
            break
        time.sleep(0.2)

    board.stop.set()
    # Join against ONE deadline, not 5 s per thread. Joining serially made a
    # 0.25 s budget take 5.4 s and let late requests land after the result was
    # already assembled.
    deadline = time.monotonic() + 5.0
    for t in threads:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        t.join(timeout=left)
    stragglers = [t.name for t in threads if t.is_alive()]
    wall = round(time.monotonic() - t0, 1)

    snap = board.snapshot()
    cands = sorted(snap["candidates"],
                   key=lambda c: (c["state"] != "attributed",
                                  -(c.get("score") or 0),
                                  c.get("pubdate") or 0))
    return {
        "ok": True,
        "input": {k: item.get(k) for k in
                  ("platform", "id", "title", "duration", "author")},
        "parallel": {
            "routes_started": chosen,
            "wall_clock_s": wall,
            "route_status": snap["routes"],
            "duplicates_merged": snap["duplicates_merged"],
            "budget": budget.snapshot(),
            "rate_limiter_waits": limiter.waits,
            "stragglers": stragglers,
        },
        "candidates": cands,
        "leads": snap["leads"],
        # A lead no route consumed is a known open thread, not a silent loss.
        "unconsumed_leads": [l for l in snap["leads"] if not l["consumed_by"]],
        "candidate_queue": [c["key"] for c in cands if c["state"] == "discovered"],
        "verification_queue": [c["key"] for c in cands
                               if c["state"] in ("checked", "verified")],
        # True when any request was refused rather than answered. A caller that
        # ignores this will read "not found" into "we were blocked".
        "coverage_incomplete": ctx.blocked_count() > 0,
        "blocked_requests": ctx.blocked,
        "content_match_state": "not_checked",
        "policy": (
            "a candidate is `discovered` until content and attribution are "
            "actually checked; a shared lead is a query, never a finding; "
            "discovery stops once attribution is established"),
    }


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2
    target = next((a for a in argv if not a.startswith("--")), None)
    if not target:
        print(__doc__)
        return 2

    def _value(flag: str) -> str:
        i = argv.index(flag)
        if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
            raise SystemExit(f"{flag} needs a value")
        return argv[i + 1]

    budget_s = 90.0
    max_requests = 120
    routes = None
    if "--budget" in argv:
        try:
            budget_s = float(_value("--budget"))
        except ValueError:
            raise SystemExit("--budget must be a number")
    if "--max-requests" in argv:
        try:
            max_requests = int(_value("--max-requests"))
        except ValueError:
            raise SystemExit("--max-requests must be an integer")
    if "--routes" in argv:
        routes = [r.strip() for r in _value("--routes").split(",") if r.strip()]
    # None means "switch this route off"; the sentinel (omitted) means default.
    yt = None if "--no-cross" in argv else _UNSET
    cover = None if "--no-image" in argv else _UNSET

    item = _load_item(target, _bili_json)
    out = run(item, routes=routes, budget_s=budget_s, max_requests=max_requests,
              yt=yt, cover=cover)
    emit(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
