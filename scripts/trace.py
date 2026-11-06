#!/usr/bin/env python
"""Trace a video to its earliest upload and to the creator it credits.

This replaces the four-route structure in discover.py. The unit of parallelism
is a TASK, not a route: every independent request is launched at t=0, and the
moment a task yields a lead the successor task is appended from inside that
task's own call stack. Nothing polls.

Two tables, never one:
    earliest_known      which upload came first   (content + chronology only)
    creator_conclusion  who is credited as author (attribution only)

Keeping them apart is the whole point. A work can be the earliest copy of a
clip and still not be the creator's -- that is the normal case for a repost
account, and merging the two produced the wrong answer once already.

Usage:
    python trace.py BV1rCxf6MERM
    python trace.py BV1rCxf6MERM --budget 90 --max-requests 120
    python trace.py BV1rCxf6MERM --routes view,space,related,search
"""
from __future__ import annotations

import json
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from _common import note, setup_stdio

# ---------------------------------------------------------------- constants
KEY_VERSION = 1

# Per-host pacing. These are starting intervals, not locks: several requests to
# the same host may be in flight, they just may not START closer than this.
DEFAULT_GAPS = {
    "api.bilibili.com": 6.0,
    "www.youtube.com": 2.0,
    "i0.hdslb.com": 0.5,
    "i1.hdslb.com": 0.5,
    "yandex.com": 3.0,
    "theasmrindex.com": 3.0,
    "web.archive.org": 2.0,
}

# Which host each task kind bills to.
HOST_OF = {
    "bili.view": "api.bilibili.com",
    "bili.related": "api.bilibili.com",
    "bili.space": "api.bilibili.com",
    "bili.search": "api.bilibili.com",
    "bili.comments": "api.bilibili.com",
    "yt.search": "www.youtube.com",
    "yt.channel": "www.youtube.com",
    "yt.oembed": "www.youtube.com",
    "web.search": "web",
    "cover.phash": "i0.hdslb.com",
    "image.reverse": "yandex.com",
    "archive.channel": "theasmrindex.com",
    "alias.expand": None,
    "media.verify": None,
    "rank": None,
}

# Priority decides WHICH request gets the next slot on a busy host. space ranks
# above search because an uploader's own titles are where a repost account
# leaves the original creator's name; a title search returns copies, not names.
PRIORITY = {
    "bili.view": 100,
    "bili.space": 95,
    # Comments outrank related and search: the thread is the one channel that
    # does not depend on the uploader writing a credit, and on the reference
    # case it was the only place a name appeared. At priority 70 it was the
    # sixth bilibili request (~30 s) and a 40 s budget could miss it entirely.
    "bili.comments": 92,
    "bili.related": 88,
    "yt.channel": 85,
    "bili.search": 80,
    "yt.search": 75,
    "yt.oembed": 60,
    # Above yt.search: the open web indexes deleted channels that YouTube no
    # longer returns, which is the only route to a banned creator.
    "web.search": 82,
    "image.reverse": 55,
    "archive.channel": 50,
    "cover.phash": 40,
    "alias.expand": 30,
    "media.verify": 20,
    "rank": 5,
}

# A lane with a single worker: some runners mutate module-level state that is
# not thread-safe (comments.py swaps its transport in and out), so two of them
# running at once would restore each other's settings.
LANE_OF = {
    "bili.comments": "comments",
    "alias.expand": "cpu",
    "media.verify": "media",
    "rank": "cpu",
}

MOBILE_UA_KINDS = {"bili.space"}

ENRICHMENT_KINDS = {"cover.phash", "image.reverse"}


# ------------------------------------------------------------------- budget
class Budget:
    """Wall clock plus a global request allowance, shared by every task."""

    def __init__(self, seconds: float, requests: int):
        self.deadline = time.monotonic() + seconds
        self.total = requests
        self.left = requests
        self.spent = 0
        self._lock = threading.Lock()

    def take(self, *, enrichment: bool = False) -> bool:
        with self._lock:
            if self.left <= 0 or time.monotonic() > self.deadline:
                return False
            if enrichment:
                reserve = max(1, min(2, self.total // 3))
                if self.left <= reserve:
                    return False
            self.left -= 1
            self.spent += 1
            return True

    def expired(self) -> bool:
        with self._lock:
            return self.left <= 0 or time.monotonic() > self.deadline

    def snapshot(self) -> dict:
        with self._lock:
            return {"requests_left": self.left, "requests_spent": self.spent,
                    "seconds_left": round(max(0.0, self.deadline - time.monotonic()), 1)}


# -------------------------------------------------------------- rate limiter
class HostPacer:
    """One starting interval per host, enforced across threads.

    Sleeping happens OUTSIDE the lock, so a host is rate-limited but not
    exclusive: two requests to the same host can overlap in flight, they just
    cannot begin closer together than the gap. `peek` lets a worker skip a
    cooled-down host and take a ready task elsewhere instead of blocking.
    """

    def __init__(self, gaps: dict[str, float] | None = None, default: float = 3.0):
        self.gaps = dict(DEFAULT_GAPS)
        if gaps:
            self.gaps.update(gaps)
        self.default = default
        self.next_slot: dict[str, float] = {}
        self.lock = threading.Lock()
        self.waits = 0

    def peek(self, host: str | None) -> float:
        """Seconds until this host may be used again. 0 = ready now."""
        if host is None:
            return 0.0
        with self.lock:
            return max(0.0, self.next_slot.get(host, 0.0) - time.monotonic())

    def reserve(self, host: str | None) -> float:
        """Claim the next slot on this host; returns how long to sleep first."""
        if host is None:
            return 0.0
        gap = self.gaps.get(host, self.default)
        with self.lock:
            now = time.monotonic()
            slot = max(now, self.next_slot.get(host, now))
            self.next_slot[host] = slot + gap
            if slot > now:
                self.waits += 1
        return max(0.0, slot - time.monotonic())

    def wait(self, host: str | None) -> None:
        delay = self.reserve(host)
        if delay > 0:
            time.sleep(delay)


# --------------------------------------------------------------------- task
@dataclass
class Task:
    kind: str
    params: dict
    dedup_key: str
    id: str = ""
    host: str | None = None
    lane: str = "net"
    depth: int = 0
    parent: str | None = None
    origin: dict | None = None
    state: str = "READY"
    priority: int = 50
    enrichment: bool = False
    ua: str = "desktop"
    result: "TaskResult | None" = None
    error: str | None = None
    reason: str | None = None
    started_at: float | None = None
    ended_at: float | None = None

    @staticmethod
    def make(kind: str, *, parent: "Task | None" = None, origin: dict | None = None,
             **params) -> "Task":
        return Task(
            kind=kind, params=params, dedup_key=canonical_key(kind, params),
            host=HOST_OF.get(kind), lane=LANE_OF.get(kind, "net"),
            depth=(parent.depth + 1) if parent else 0,
            parent=parent.id if parent else None,
            origin=origin,
            priority=PRIORITY.get(kind, 50),
            enrichment=kind in ENRICHMENT_KINDS,
            ua="mobile" if kind in MOBILE_UA_KINDS else "desktop",
        )


def canonical_key(kind: str, params: dict) -> str:
    """The ONLY thing that decides whether two tasks are the same task."""
    bits = [f"v{KEY_VERSION}", kind]
    for k in sorted(params):
        v = params[k]
        if isinstance(v, str):
            v = v.strip().lower()
        bits.append(f"{k}={v}")
    return "|".join(bits)


@dataclass
class TaskResult:
    candidates: list[dict] = field(default_factory=list)
    leads: list[dict] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    note: str | None = None


# ------------------------------------------------------------------- ledger
class Ledger:
    """Candidates plus the edges that produced them. Provenance is factual."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cands: dict[str, dict] = {}
        self.edges: list[dict] = []
        self.dupes = 0

    def absorb(self, result: TaskResult, task: Task) -> None:
        with self.lock:
            for c in result.candidates:
                key = c.get("key") or f"{c.get('platform')}:{c.get('id')}"
                cur = self.cands.get(key)
                if cur is None:
                    self.cands[key] = dict(c, key=key, edges=[])
                    cur = self.cands[key]
                else:
                    self.dupes += 1
                    # Merge rather than drop: the same video arrives from
                    # several routes and each may know a field the first did
                    # not (view knows mid, search knows the cover, space knows
                    # the uploader). Keeping only the first writer is how the
                    # per-account signal went missing.
                    for k, v in c.items():
                        # `False == 0` in Python. Using a falsy test here
                        # discarded an explicit channel_verified=False, so a
                        # later verified row from a DIFFERENT channel could
                        # overwrite it while keeping the rejected channel's id.
                        # Only fill a field that is genuinely absent.
                        if v is not None and v != "" and k not in cur:
                            cur[k] = v
                    # Channel identity is atomic: it must never be assembled
                    # from two different channels sharing the same video id.
                    if cur.get("channel_id") != c.get("channel_id"):
                        if cur.get("channel_id") is None:
                            cur["channel_id"] = c.get("channel_id")
                        elif c.get("channel_id") is not None:
                            cur["channel_verified"] = False
                            cur["archive_of"] = None
                if task.origin:
                    self.edges.append(dict(task.origin))
                    cur["edges"].append(task.origin)

    def snapshot(self) -> dict:
        with self.lock:
            return {"candidates": list(self.cands.values()),
                    "edges": list(self.edges), "duplicates_merged": self.dupes}


# --------------------------------------------------------------------- pool
class Pool:
    """Task pool. New work is appended from the producing task's own stack."""

    def __init__(self, budget: Budget, pacer: HostPacer, max_tasks: int = 400,
                 workers: int = 8):
        self.budget = budget
        self.pacer = pacer
        self.max_tasks = max_tasks
        self.workers = workers
        self.cv = threading.Condition()
        self.ready: list[Task] = []
        self.tasks: dict[str, Task] = {}
        self.seen: set[str] = set()
        self.done = 0
        self.failed = 0
        self.blocked = 0
        self.stopping = False
        self.stop_reason: str | None = None
        self._next_id = 0
        self.on_result: Callable[[Task], None] | None = None

    # -- scheduling ------------------------------------------------------
    def spawn(self, task: Task) -> bool:
        """Append a task. Called from inside a runner; wakes one worker."""
        with self.cv:
            if task.dedup_key in self.seen:
                return False
            if len(self.tasks) >= self.max_tasks:
                return False
            self.seen.add(task.dedup_key)
            self._next_id += 1
            task.id = f"t{self._next_id}"
            self.tasks[task.id] = task
            self.ready.append(task)
            self.cv.notify()
            return True

    def _pick(self) -> Task | None:
        """Highest priority task whose host is not cooling down."""
        best = None
        best_wait = 0.0
        for i, t in enumerate(self.ready):
            wait = self.pacer.peek(t.host)
            if wait <= 0:
                if best is None or t.priority > self.ready[best].priority:
                    best = i
            elif best is None and (best_wait == 0.0 or wait < best_wait):
                best_wait = wait
        if best is not None:
            return self.ready.pop(best)
        return None

    def next_task(self) -> Task | None:
        with self.cv:
            while True:
                if self.stopping:
                    return None
                t = self._pick()
                if t is not None:
                    t.state = "RUNNING"
                    return t
                if not self.ready and self._all_done():
                    return None
                if self.budget.expired():
                    self.stopping = True
                    self.stop_reason = self.stop_reason or "budget"
                    self.cv.notify_all()
                    return None
                # Wait only until the nearest host cools down; a spawn() from
                # another worker notifies immediately and pre-empts this.
                waits = [self.pacer.peek(t.host) for t in self.ready] or [0.2]
                self.cv.wait(timeout=min(min(waits), 0.2))

    def _all_done(self) -> bool:
        return all(t.state in ("DONE", "FAILED", "BLOCKED", "SKIPPED", "ABORTED")
                   for t in self.tasks.values())

    def finish(self, task: Task) -> None:
        with self.cv:
            self.done += 1
            if task.state == "FAILED":
                self.failed += 1
            elif task.state == "BLOCKED":
                self.blocked += 1
            self.cv.notify_all()

    def stop(self, reason: str) -> None:
        with self.cv:
            if not self.stopping:
                self.stopping = True
                self.stop_reason = reason
            self.cv.notify_all()


# ------------------------------------------------------------------ runners
Runner = Callable[[Task, "Ctx"], TaskResult]


class Ctx:
    """Everything a runner may touch. The network layer is injectable."""

    def __init__(self, item: dict, pool: Pool, budget: Budget, pacer: HostPacer,
                 ledger: Ledger, *, net: dict | None = None):
        # Queries the caller still has to run because this engine has no search
        # backend. Dropping them is how the web channel contributed nothing.
        self.pending_queries: list[dict] = []
        self.identities: list[dict] = []
        self.item = item
        self.pool = pool
        self.budget = budget
        self.pacer = pacer
        self.ledger = ledger
        self.net = net or {}
        self.blocked: list[dict] = []
        self._blocked_lock = threading.Lock()

    def note_blocked(self, what: str, reason: str) -> None:
        with self._blocked_lock:
            self.blocked.append({"what": what, "reason": reason[:160]})

    def blocked_count(self) -> int:
        with self._blocked_lock:
            return len(self.blocked)

    def comment_names(self) -> list[str]:
        """Names a human wrote in the thread. Kept apart from title brands."""
        return [d["name"] for d in self.comment_name_details()]

    def note_identity(self, value: str, source: str = "") -> None:
        with self._blocked_lock:
            if not any(i["value"] == value for i in self.identities):
                self.identities.append({"value": value, "source": source})

    def note_comment_name(self, name: str, hedged: bool = False) -> None:
        with self._blocked_lock:
            if not hasattr(self, "_cnamed"):
                self._cnamed = {}
            # A hedge never upgrades an earlier confident mention.
            self._cnamed[name] = self._cnamed.get(name, True) and hedged

    def comment_name_details(self) -> list[dict]:
        with self._blocked_lock:
            return [{"name": n, "hedged": h}
                    for n, h in getattr(self, "_cnamed", {}).items()]


RUNNERS: dict[str, Runner] = {}

# Kinds that are declared for completeness but never dispatched today. Listing
# them explicitly means the check in run() still catches an accidental gap.
_INTENTIONALLY_EMPTY = {"cover.phash", "media.verify", "alias.expand", "rank"}


def runner(kind: str):
    def deco(fn: Runner) -> Runner:
        RUNNERS[kind] = fn
        return fn
    return deco


def _strip_tags(s: str) -> str:
    """search/type injects <em class="keyword"> around the matched term."""
    return re.sub(r"<[^>]+>", "", s or "")


@runner("bili.view")
def run_view(task: Task, ctx: Ctx) -> TaskResult:
    j = ctx.net["view"](task.params["bvid"])
    if j.get("code") != 0:
        ctx.note_blocked("view", f"code {j.get('code')}")
        return TaskResult(note=f"view code {j.get('code')}")
    d = j.get("data") or {}
    out = TaskResult(candidates=[{
        "platform": "bilibili", "id": d.get("bvid"),
        "title": _strip_tags(d.get("title") or ""),
        "author": (d.get("owner") or {}).get("name"),
        "mid": (d.get("owner") or {}).get("mid"),
        "pubdate": d.get("pubdate") or 0,
        "duration": d.get("duration") or 0,
    }])
    cover = d.get("pic")
    if cover:
        # Declared in HOST_OF but never dispatched -- the confirmation channel
        # that reaches platforms the title search cannot.
        ctx.pool.spawn(Task.make("image.reverse", parent=task,
                                 origin={"src": task.id, "kind": "cover"},
                                 url=cover))
    owner = d.get("owner") or {}
    if owner.get("mid"):
        # An uploader's own timeline is the cheapest place a repost leaves the
        # original creator's name, so it is queued the moment we learn the mid.
        ctx.pool.spawn(Task.make("bili.space", parent=task,
                                 origin={"src": task.id, "kind": "owner"},
                                 mid=owner["mid"], pn=1))
    return out


@runner("bili.space")
def run_space(task: Task, ctx: Ctx) -> TaskResult:
    j = ctx.net["space"](task.params["mid"], task.params.get("pn", 1))
    if j.get("code") != 0:
        ctx.note_blocked("space", f"code {j.get('code')}")
        return TaskResult(note=f"space code {j.get('code')}")
    vlist = (((j.get("data") or {}).get("list") or {}).get("vlist") or [])
    cands, brands = [], {}
    for v in vlist:
        title = _strip_tags(v.get("title") or "")
        cands.append({
            "platform": "bilibili", "id": v.get("bvid"), "title": title,
            "author": v.get("author"),
            # Which account this came from. Without it a candidate cannot be
            # attributed to an uploader, so no per-account or per-ring signal
            # is computable -- the missing field that broke credit_rate.
            "mid": task.params.get("mid"),
            "pubdate": v.get("created") or 0,
            "duration": _secs(v.get("length")),
        })
        for b in brand_tokens(title):
            brands.setdefault(b, 0)
            brands[b] += 1
    leads = []
    for b, n in brands.items():
        leads.append({"kind": "brand", "value": b, "count": n,
                      "source": "space"})
    return TaskResult(candidates=cands, leads=leads)


@runner("bili.related")
def run_related(task: Task, ctx: Ctx) -> TaskResult:
    j = ctx.net["related"](task.params["bvid"])
    if j.get("code") != 0:
        ctx.note_blocked("related", f"code {j.get('code')}")
        return TaskResult(note=f"related code {j.get('code')}")
    cands = []
    for v in (j.get("data") or []):
        cands.append({
            "platform": "bilibili", "id": v.get("bvid"),
            "title": _strip_tags(v.get("title") or ""),
            "author": (v.get("owner") or {}).get("name"),
            "pubdate": v.get("pubdate") or 0,
            "duration": v.get("duration") or 0,
        })
    # Measured precision is 2.6%: 39 neighbours, 1 real copy. Reading titles for
    # a brand is free; treating them as candidates or recursing into them is
    # not, so this runner deliberately spawns nothing.
    leads = []
    for v in (j.get("data") or []):
        for b in brand_tokens(_strip_tags(v.get("title") or "")):
            leads.append({"kind": "brand", "value": b, "count": 1,
                          "source": "related"})
    return TaskResult(candidates=cands, leads=leads)


@runner("bili.search")
def run_search(task: Task, ctx: Ctx) -> TaskResult:
    j = ctx.net["search"](task.params["kw"], task.params.get("page", 1),
                          task.params.get("order", "pubdate"))
    if j.get("code") != 0:
        ctx.note_blocked("search", f"code {j.get('code')}")
        return TaskResult(note=f"search code {j.get('code')}")
    cands = []
    for r in ((j.get("data") or {}).get("result") or []):
        cands.append({
            "platform": "bilibili", "id": r.get("bvid"),
            "title": _strip_tags(r.get("title") or ""),
            "author": r.get("author"), "pubdate": r.get("pubdate") or 0,
            "duration": _secs(r.get("duration")),
            "cover": r.get("pic"),
        })
    return TaskResult(candidates=cands)


@runner("bili.comments")
def run_comments(task: Task, ctx: Ctx) -> TaskResult:
    """Mine the thread for a name. On this case it was the only source."""
    aid = task.params["aid"]
    # Prefer the injected channel so offline tests actually exercise this
    # route. Fall back to the real module only when no stub was supplied.
    fetch = ctx.net.get("fetch_comments")
    if fetch is None:
        import comments as C
        fetch = C.fetch_comments
    try:
        thread, meta = fetch(aid)
    except Exception as e:                          # noqa: BLE001
        ctx.note_blocked("bili.comments", f"fetch failed: {e}")
        return TaskResult(note=f"comment fetch failed: {e}")
    texts = []
    for c in thread:
        texts.append(c.get("text") or "")
        for n in c.get("nested") or []:
            texts.append(n.get("text") or "")
    leads = []
    for txt in texts:
        hedged = _is_hedged(txt)
        for tok in _comment_names(txt):
            leads.append({"kind": "brand", "value": tok, "count": 1,
                          "source": "comments", "hedged": hedged})
            ctx.note_comment_name(tok, hedged=hedged)
    return TaskResult(leads=leads,
                      evidence={"comments_read": len(texts),
                                "declared": meta.get("declared_all_count")})


# "I think", "probably", "should be" -- the writer is guessing.
_HEDGES = ("应该是", "好像是", "可能是", "大概是", "估计是", "似乎是",
           "是不是", "不确定", "也许", "或许", "应该", "好像", "可能",
           "大概", "估计", "似乎", "我猜", "猜是")


def _is_hedged(text: str) -> bool:
    return any(h in (text or "") for h in _HEDGES)


# "原作者是桑塔阿普亚" and "应该是桑塔阿普亚" were yielding 阿普亚 / 普亚:
# the role/hedge prefix glued to the name and shifted the CJK run. Remove the
# leading attribution phrase before tokenising.
_NAME_PREFIX = re.compile(
    r"^(?:应该是这个|应该是|好像是|可能是|大概是|估计是|似乎是|是不是|"
    r"大概原作者是|原作者是|原作者|原创|作者|原up|up主|转载自|搬运自|"
    r"应该|大概|估计|似乎|这是|这个是|是|叫|@)+[\s:：,，。、]*")


def _comment_names(text: str) -> list[str]:
    """CJK runs in a comment that could be a creator's name."""
    from discover import _short_tokens
    cleaned = _NAME_PREFIX.sub("", (text or "").strip())
    return _short_tokens(cleaned) or _short_tokens(text)


_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


@runner("yt.search")
def run_yt_search(task: Task, ctx: Ctx) -> TaskResult:
    hits = ctx.net["ytsearch"](task.params["q"], task.params.get("n", 20))
    cands = []
    for e in hits or []:
        vid = e.get("id") or ""
        author = e.get("channel") or e.get("uploader")
        # A search can return a CHANNEL (UC + 22 chars). Stored as a video it
        # pollutes the table; stored as a channel it is the witness we need, so
        # enumerate its uploads instead.
        if _CHANNEL_ID.match(vid):
            ctx.pool.spawn(Task.make("yt.channel", parent=task,
                                     origin={"src": task.id, "kind": "channel",
                                             "via": author},
                                     cid=vid, name=author, limit=200))
            ctx.pool.spawn(Task.make("archive.channel", parent=task,
                                     origin={"src": task.id, "kind": "channel",
                                             "via": author},
                                     cid=vid, name=author))
            continue
        cands.append({
            "platform": "youtube", "id": vid,
            "title": _strip_tags(e.get("title") or ""),
            "author": author,
            "pubdate": 0, "duration": int(e.get("duration") or 0) or 0,
        })
    return TaskResult(candidates=cands)


@runner("web.search")
def run_web_search(task: Task, ctx: Ctx) -> TaskResult:
    """Ask the open web about a brand.

    Verified: a plain web search for "POMEchan ASMR" returns the YouTube
    channel id (UC1Y-RkDrol_nyXk7vx8wgUA), the handle @Pomeko-odango, the X
    account odangodaisuki99, and the ban date -- none of which any platform API
    in this engine could produce, because the channel is deleted.

    The engine has no search API of its own, so this runner emits the query and
    records it as a lead. The caller (an agent with web_search) executes it.
    """
    q = task.params.get("q")
    if not q:
        return TaskResult(note="no query")
    fn = ctx.net.get("websearch")
    if fn is None:
        # No search backend wired: still emit the query so the caller can run
        # it. Losing the query is how this whole channel stayed invisible.
        ctx.pending_queries.append({"query": q, "for": task.params.get("name")})
        return TaskResult(leads=[{"kind": "web_query", "value": q,
                                  "source": "web", "why": "needs a web search"}])
    try:
        hits = fn(q)
    except Exception as e:                          # noqa: BLE001
        ctx.note_blocked("web.search", str(e))
        return TaskResult(note=f"web search failed: {e}")
    cands, ids = [], set()
    for h in hits or []:
        text = f"{h.get('url') or ''} {h.get('title') or ''} {h.get('snippet') or ''}"
        for m in re.finditer(r"(UC[A-Za-z0-9_-]{22})", text):
            ids.add(m.group(1))
        for m in re.finditer(r"@([A-Za-z0-9_.-]{3,30})", text):
            ids.add("@" + m.group(1))
        for m in re.finditer(r"(?:twitter|x)\.com/([A-Za-z0-9_]{3,20})", text):
            ids.add("x:" + m.group(1))
    for cid in ids:
        if cid.startswith("UC"):
            ctx.pool.spawn(Task.make("archive.channel", parent=task,
                                     origin={"src": task.id, "kind": "web",
                                             "via": cid},
                                     cid=cid, name=task.params.get("name")))
    # Handles and social accounts are identifiers too, and the task accepts any
    # of them. Keep them with the creator rather than discarding them.
    for ident in ids:
        if ident.startswith("@") or ident.startswith("x:"):
            ctx.note_identity(ident, source="web")
    return TaskResult(evidence={"web_ids": sorted(ids)[:10]})


@runner("yt.oembed")
def run_yt_oembed(task: Task, ctx: Ctx) -> TaskResult:
    """Authoritative title + channel for a bare video id.

    Reverse image search returns ids without metadata; this turns one into a
    named channel so the creator can be checked.
    """
    vid = task.params.get("vid")
    if not vid:
        return TaskResult(note="no video id")
    try:
        j = ctx.net["ytoembed"](vid)
    except Exception as e:                          # noqa: BLE001
        ctx.note_blocked("yt.oembed", str(e))
        return TaskResult(note=f"oembed failed: {e}")
    return TaskResult(candidates=[{
        "platform": "youtube", "id": vid,
        "title": j.get("title"), "author": j.get("author_name"),
        "pubdate": 0, "duration": 0,
    }], evidence={"channel_url": j.get("author_url")})


@runner("image.reverse")
def run_image_reverse(task: Task, ctx: Ctx) -> TaskResult:
    """Reverse-search the cover. Reaches platforms the title search cannot.

    Verified by hand in FINAL.md C7: a bilibili cover returned a YouTube video
    id that no title query ever surfaced. This runner was declared in HOST_OF
    but never written, so the whole channel was dead code.
    """
    url = task.params.get("url")
    if not url:
        return TaskResult(note="no cover url")
    try:
        hits = ctx.net["reverse_image"](url)
    except Exception as e:                          # noqa: BLE001
        ctx.note_blocked("image.reverse", str(e))
        return TaskResult(note=f"reverse image failed: {e}")
    cands = []
    for h in hits or []:
        u = h.get("url") or ""
        m = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", u)
        if m:
            vid = m.group(1)
            cands.append({"platform": "youtube", "id": vid,
                          "title": None, "author": None, "pubdate": 0,
                          "duration": 0})
            # The id alone names nobody; oembed is what turns it into a creator.
            ctx.pool.spawn(Task.make("yt.oembed", parent=task,
                                     origin={"src": task.id, "kind": "image",
                                             "via": vid},
                                     vid=vid))
            continue
        m = re.search(r"(BV[0-9A-Za-z]{10})", u)
        if m:
            cands.append({"platform": "bilibili", "id": m.group(1),
                          "title": None, "author": None, "pubdate": 0,
                          "duration": 0})
    return TaskResult(candidates=cands,
                      evidence={"image_hits": len(hits or [])})


@runner("archive.channel")
def run_archive_channel(task: Task, ctx: Ctx) -> TaskResult:
    """Enumerate a DELETED YouTube channel from a third-party archive.

    POMEchan's channel returns "removed for violating Community Guidelines";
    theasmrindex still serves /channel/<UCID> with all 239 titles. Without this
    a named-but-deleted creator can never be confirmed, which is exactly the
    state BV1rCxf6MERM was stuck in.
    """
    cid = task.params.get("cid")
    if not cid:
        # A brand lead carries a name, not an id. Resolve it first, otherwise
        # the runner is dispatched and silently produces nothing.
        name = task.params.get("name")
        if not name:
            return TaskResult(note="no channel id and no name")
        try:
            cid = ctx.net["resolve_channel"](name)
        except Exception as e:                      # noqa: BLE001
            ctx.note_blocked("archive.channel", f"resolve failed: {e}")
            return TaskResult(note=f"cannot resolve {name!r} to a channel")
        if not cid:
            return TaskResult(note=(
                f"no channel id for {name!r}: YouTube search returns only "
                "reuploaders, the archive indexes creators not reuploads, and "
                "its /channels index does not list this creator. A deleted "
                "channel id cannot be derived from a name alone."))
    try:
        rows = ctx.net["archive_channel"](cid)
    except Exception as e:                          # noqa: BLE001
        ctx.note_blocked("archive.channel", str(e))
        return TaskResult(note=f"archive failed: {e}")
    cands = []
    name = task.params.get("name")
    # Verify the archive page actually belongs to this name. Without this the
    # runner echoes the query name onto every row, so ANY channel id found in
    # a web result becomes "the creator's own catalogue".
    verified = False
    if name:
        try:
            # Strict: a verifier returning 1 / "yes" must not be coerced to
            # True. Downstream gates require `is True`.
            verified = ctx.net["verify_channel"](cid, name) is True
        except Exception:                           # noqa: BLE001
            verified = False
    for r in rows or []:
        cands.append({"platform": "youtube", "id": r.get("id"),
                      "title": r.get("title"), "author": name,
                      "archive_of": name,
                      "channel_id": cid,
                      "channel_verified": verified,
                      "pubdate": 0, "duration": 0})
    return TaskResult(candidates=cands,
                      evidence={"archived_titles": len(cands)})


@runner("yt.channel")
def run_yt_channel(task: Task, ctx: Ctx) -> TaskResult:
    """Enumerate a live creator's own uploads (the witness_work_id source)."""
    cid = task.params.get("cid")
    if not cid:
        return TaskResult(note="no channel id")
    try:
        rows = ctx.net["ytchannel"](cid, task.params.get("limit", 200))
    except Exception as e:                          # noqa: BLE001
        ctx.note_blocked("yt.channel", str(e))
        return TaskResult(note=f"channel failed: {e}")
    cands = [{"platform": "youtube", "id": r.get("id"), "title": r.get("title"),
              "author": task.params.get("name"), "pubdate": 0,
              "duration": int(r.get("duration") or 0) or 0}
             for r in rows or []]
    return TaskResult(candidates=cands,
                      evidence={"channel_titles": len(cands)})


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


# ------------------------------------------------------------ brand tokens
_NOT_A_BRAND = {
    # format / resolution / platform
    "asmr", "4k", "8k", "1080p", "720p", "3dio", "3d", "hires", "hi-res",
    "no", "talking", "notalking", "sleep", "relax", "relaxation", "trigger",
    "triggers", "audio", "video", "cos", "cosplay", "jk", "vr", "hd", "uhd",
    "bilibili", "youtube", "douyin", "tiktok", "niconico", "twitch",
    "myspace", "vogue", "studios", "records", "official", "channel",
    # ordinary English words that appear in titles by the thousand. The live
    # run reported these as "brands with 36 accounts", which is nonsense: a
    # preposition cannot be a creator.
    "with", "full", "from", "your", "this", "that", "the", "and", "for",
    "you", "her", "his", "she", "him", "not", "but", "all", "any", "can",
    "will", "just", "like", "make", "made", "more", "most", "much", "very",
    "what", "when", "where", "which", "who", "why", "how", "into", "onto",
    "over", "under", "about", "after", "before", "again", "also", "been",
    "being", "both", "each", "even", "ever", "every", "first", "last",
    "less", "many", "might", "must", "need", "never", "next", "only",
    "other", "our", "out", "own", "same", "should", "some", "still",
    "such", "than", "them", "then", "there", "these", "they", "thing",
    "think", "those", "through", "time", "too", "under", "until", "upon",
    "used", "using", "want", "was", "way", "well", "were", "what", "while",
    "with", "would", "year", "yes", "yet", "one", "two", "three", "new",
    "old", "big", "small", "long", "short", "high", "low", "good", "best",
    "great", "little", "right", "left", "back", "down", "here", "now",
    "emma", "anna", "maria", "sarah", "lisa", "nina", "lena", "mia",
}


# English words that show up because a CJK query was translated, not because
# the uploader wrote them. Searching 桑塔阿普亚 on YouTube returns "Santa" and
# "Lucia"; those are search artefacts, not credits.
_NOT_A_BRAND |= {
    "santa", "lucia", "saint", "christmas", "happy", "merry", "music",
    "sounds", "sound", "video", "reupload", "upload", "credit", "credits",
    "original", "originalvideo", "subscribe", "channel", "official",
    "democrats", "republican", "news", "live", "stream", "highlights",
}


def _looks_like_id(w: str) -> bool:
    """yh-998k, ht38a5, BV1xx... -- an identifier leaked into a title.

    A real brand may contain a digit (studio names rarely do, but handles
    sometimes), so the test is shape-based: a short run of letters glued to a
    run of digits, with no vowel-heavy word structure.
    """
    wl = w.strip(".-_")
    if not any(c.isdigit() for c in wl):
        return False
    if re.fullmatch(r"[A-Za-z]{2,4}[-_]?\d{2,}[A-Za-z0-9]*", wl):
        return True
    if re.fullmatch(r"[A-Za-z0-9]{5,}", wl) and sum(c.isdigit() for c in wl) >= 2:
        return True
    return False


def brand_tokens(title: str) -> list[str]:
    """Latin words in a Chinese title that could be a creator's brand.

    Single-rule heuristics measured 0-9.2% precision on 452 real titles; the
    only reliable signal is that a brand is repeated across DIFFERENT uploaders.
    This returns candidates; the pool decides by cross-account frequency.
    """
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9_.\-]{2,}", title or ""):
        wl = w.lower().strip(".-_")
        if wl in _NOT_A_BRAND:
            continue
        # A token glued from genre words ("asmr_cos", "asmr3dio") is a tag, not
        # a name. Split on the separator and reject when every part is a genre
        # word -- comparing only the whole token lets the compound through.
        parts = [x for x in re.split(r"[_\-.]", wl) if x]
        if len(parts) > 1 and all(x in _NOT_A_BRAND for x in parts):
            continue
        if len(parts) > 1 and any(x.isdigit() for x in parts):
            continue
        # No separator to split on (asmr3dio): remove every genre word and see
        # whether anything is left. Nothing left means it was only genre words.
        residue = wl
        for g in sorted(_NOT_A_BRAND, key=len, reverse=True):
            residue = residue.replace(g, "")
        if not residue.strip("0123456789_-."):
            continue
        if _looks_like_id(w):
            continue
        if any(ch.isdigit() for ch in wl) and len(wl) <= 5:
            continue
        if len(wl) < 4:
            continue
        # A creator brand is usually capitalised mid-title (POMEchan, PPOMO) or
        # contains a digit/case change. A plain lowercase English word is
        # almost always part of the sentence, not a name.
        has_shape = (w[0].isupper() and not w.isupper()) or \
                    any(ch.isdigit() for ch in w) or \
                    (any(c.isupper() for c in w[1:]) and w[0].isupper())
        if not has_shape and w.islower():
            continue
        out.append(w)
    return out


# --------------------------------------------------------------------- run
def run(item: dict, *, net: dict, budget_s: float = 90.0, max_requests: int = 120,
        routes: list[str] | None = None, gaps: dict[str, float] | None = None,
        workers: int = 8, max_tasks: int = 400) -> dict:
    """Launch every independent task at once, then follow the leads."""
    # A kind in HOST_OF with no runner is silently skipped at execution time.
    # That is how image.reverse and archive.channel -- the entire confirmation
    # layer -- stayed dead while the tests stayed green. Refuse to start.
    unimplemented = sorted(set(HOST_OF) - set(RUNNERS) - _INTENTIONALLY_EMPTY)
    if unimplemented:
        raise RuntimeError(
            f"declared task kinds with no runner: {unimplemented}. "
            "Implement them or list them in _INTENTIONALLY_EMPTY.")
    budget = Budget(budget_s, max_requests)
    pacer = HostPacer(gaps)
    ledger = Ledger()
    pool = Pool(budget, pacer, max_tasks=max_tasks, workers=workers)
    ctx = Ctx(item, pool, budget, pacer, ledger, net=net)

    t0 = time.monotonic()
    bvid = item.get("id")

    # t=0: everything that does not need another task's output.
    seeds = []
    if bvid:
        seeds.append(Task.make("bili.view", bvid=bvid))
        seeds.append(Task.make("bili.related", bvid=bvid))
        if item.get("aid"):
            seeds.append(Task.make("bili.comments", aid=item["aid"]))
        if item.get("mid"):
            seeds.append(Task.make("bili.space", mid=item["mid"], pn=1))
        if item.get("title"):
            # Two orders, two result sets: relevance and date overlap almost
            # nothing. Date order is what surfaces the earlier copies.
            for order in ("pubdate", "click"):
                seeds.append(Task.make("bili.search", kw=item["title"],
                                       page=1, order=order))
            seeds.append(Task.make("yt.search", q=item["title"], n=20))
    for s in seeds:
        pool.spawn(s)

    def worker() -> None:
        while True:
            task = pool.next_task()
            if task is None:
                return
            fn = RUNNERS.get(task.kind)
            if fn is None:
                task.state = "SKIPPED"
                task.reason = "no runner"
                pool.finish(task)
                continue
            delay = pacer.reserve(task.host)
            if delay > 0:
                time.sleep(delay)
            task.started_at = time.monotonic()
            try:
                if not budget.take(enrichment=task.enrichment):
                    task.state = "SKIPPED"
                    task.reason = "budget"
                else:
                    res = fn(task, ctx)
                    task.result = res
                    task.state = "DONE"
                    ledger.absorb(res, task)
                    # Instant dispatch: successors are appended here, on this
                    # task's own stack, before the worker returns to the pool.
                    for lead in res.leads:
                        for nxt in plan(lead, task, ctx):
                            pool.spawn(nxt)
            except Exception as e:                  # noqa: BLE001
                task.state = "FAILED"
                task.error = str(e)[:200]
                # A runner that raised did not answer the question. Whether it
                # was a 412, a timeout or a parse error, the channel is missing
                # from the result -- so coverage is incomplete, not clean.
                ctx.note_blocked(task.kind, str(e))
            finally:
                task.ended_at = time.monotonic()
                pool.finish(task)

    threads = [threading.Thread(target=worker, name=f"w{i}", daemon=True)
               for i in range(workers)]
    for th in threads:
        th.start()
    deadline = time.monotonic() + budget_s + 10
    for th in threads:
        left = deadline - time.monotonic()
        if left <= 0:
            break
        th.join(timeout=left)

    wall = round(time.monotonic() - t0, 1)
    snap = ledger.snapshot()
    brands = brand_consensus(snap["candidates"])
    return {
        "ok": True,
        "input": {k: item.get(k) for k in ("platform", "id", "title", "author", "mid")},
        "earliest_known": earliest_known(snap["candidates"], item),
        "creator_conclusion": creator_conclusion(
            brands, snap["candidates"], ctx.comment_name_details(),
            ctx.identities),
        "candidates": sorted(snap["candidates"], key=_rank_key),
        "brands": brands,
        "provenance": snap["edges"],
        "coverage_incomplete": ctx.blocked_count() > 0,
        "blocked_requests": ctx.blocked,
        # What the caller must still do. For a banned creator this is the
        # difference between "found nothing" and "search this name".
        "pending_queries": ctx.pending_queries,
        "diagnosis": diagnosis(
            earliest_known(snap["candidates"], item),
            creator_conclusion(brands, snap["candidates"],
                               ctx.comment_name_details(), ctx.identities),
            ctx.blocked, len(snap["candidates"]), len(snap["edges"])),
        "stop_reason": pool.stop_reason or "frontier_empty",
        "wall_clock_s": wall,
        "requests_spent": budget.spent,
        "tasks_run": pool.done,
        "tasks_failed": pool.failed,
    }


def plan(lead: dict, task: Task, ctx: Ctx) -> list[Task]:
    """Turn a lead into successor tasks. Called from the producing stack."""
    out = []
    if lead.get("kind") != "brand":
        return out
    brand = lead["value"]
    src = lead.get("source") or ""

    # Two kinds of brand lead, two different tests.
    #
    # From a TITLE: the token may be genre vocabulary, so require that several
    # unrelated uploaders repeat it before spending a search on it.
    #
    # From a COMMENT: a person wrote "the source is X". That is a deliberate
    # claim about this specific video, not a statistical pattern. Requiring it
    # to also appear in titles is wrong -- on BV1hSuV6UESm the answer existed
    # only in the thread, because the uploader had stripped every title.
    if src == "comments":
        pass
    else:
        n = sum(1 for c in ctx.ledger.snapshot()["candidates"]
                if brand.lower() in (c.get("title") or "").lower())
        if n < 2:
            return out

    origin = {"src": task.id, "kind": "brand", "via": brand, "from": src}
    out.append(Task.make("yt.search", parent=task, origin=origin,
                         q=brand, n=20))
    # If the creator's channel turns out to be deleted, the archive runner is
    # the only way to reach their own work and upgrade a lead to confirmed.
    out.append(Task.make("archive.channel", parent=task, origin=origin,
                         name=brand, cid=None))
    out.append(Task.make("web.search", parent=task, origin=origin,
                         q=f"{brand} ASMR", name=brand))
    for order in ("pubdate", "click"):
        out.append(Task.make("bili.search", parent=task, origin=origin,
                             kw=brand, page=1, order=order))
    return out


def brand_consensus(cands: list[dict]) -> list[dict]:
    """Which Latin tokens are repeated across DIFFERENT uploaders.

    Ranked by cross-platform corroboration first, then by account count. A name
    that appears in Bilibili titles AND in YouTube reuploads is far stronger
    evidence than one repeated many times inside a single platform, where a tag
    can spread through a repost ring in a day.
    """
    per: dict[str, set] = {}
    plats: dict[str, set] = {}
    for c in cands:
        who = (c.get("author") or "").strip()
        plat = c.get("platform") or "?"
        for b in brand_tokens(c.get("title") or ""):
            per.setdefault(b.lower(), set()).add(who)
            plats.setdefault(b.lower(), set()).add(plat)
    rows = []
    for b, a in per.items():
        if len(a) < 2:
            continue
        rows.append({"brand": b, "accounts": len(a),
                     "platforms": sorted(plats.get(b, set())),
                     "sample": sorted(a)[:4]})
    return sorted(rows, key=lambda d: (-len(d["platforms"]), -d["accounts"]))


def _rank_key(c: dict):
    return (-(c.get("score") or 0), -(c.get("pubdate") or 0))


DURATION_RATIO = (0.5, 1.6)     # a re-cut may differ, a different clip may not
MIN_TITLE_BIGRAMS = 3

# Genre vocabulary carries no identity: every ASMR title contains these, so
# counting them as "title overlap" makes the gate accept anything. Measured on
# a false positive: input vs an unrelated 2024 upload shared exactly
# {AS, MR, SM, 丝袜} and passed a 3-bigram threshold.
_GENRE_WORDS = {
    "asmr", "助眠", "触发音", "触发", "丝袜", "黑丝", "白丝", "网袜", "油丝",
    "蕾丝", "手套", "搓麦", "摩擦", "精油", "大腿", "耳朵", "掏耳", "采耳",
    "耳语", "无人声", "睡眠", "按摩", "解压", "沉浸", "纯享", "福利", "cos",
    "cosplay", "jk", "4k", "3dio", "无损", "hires", "助眠向", "哄睡", "轻语",
    "舔耳", "口腔音", "心跳", "白噪音", "剧情", "角色扮演",
}


def _clean(s: str) -> str:
    """Lowercase and drop punctuation, keeping the words themselves."""
    return re.sub(r"[\s【】\[\]()（）_|/\\,，。、!！?？~～\-\u201c\u201d"
                  r"\u2018\u2019&＆.0-9]+", "", (s or "").lower())


def _bigrams(s: str) -> set[str]:
    s = _clean(s)
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 2 else set()


def _distinctive(title: str, pool: list[dict], limit: int = 12) -> set[str]:
    """Bigrams from `title` that are NOT shared with most other candidates.

    A word that appears in half the pool (ASMR, 助眠, 丝袜) identifies nothing.
    A word that appears only here (脱了放我耳朵上, 黑白蕾丝手丝搓麦) identifies
    the work. This is document frequency, not a hand-written stoplist -- the
    hand-written version deleted the only words worth comparing.
    """
    mine = _bigrams(title)
    if not mine:
        return set()
    others: dict[str, int] = {}
    for c in pool:
        for b in _bigrams(c.get("title") or ""):
            others[b] = others.get(b, 0) + 1
    n = max(1, len(pool))
    rare = {b for b in mine if others.get(b, 0) <= n * 0.25}
    return rare or mine


def _same_subject(cand: dict, item: dict, pool: list[dict] | None = None) -> bool:
    """Could this candidate be the same footage? Duration and title only.

    This is a GATE, not proof. Passing it means "worth calling earlier"; it
    never means the content was verified -- that is media.verify's job, and
    content_basis stays None until it runs.
    """
    d1, d2 = cand.get("duration") or 0, item.get("duration") or 0
    if d1 and d2:
        ratio = d1 / d2
        if not (DURATION_RATIO[0] <= ratio <= DURATION_RATIO[1]):
            return False
    pool = pool or []
    key = _distinctive(item.get("title") or "", pool)
    if not key:
        return False
    shared = key & _bigrams(cand.get("title") or "")
    # A repost of the SAME clip copies the title almost verbatim: measured
    # 12/12 for the real copy against 3/12 for a merely similar video. So the
    # bar is a majority, not a third -- at a third, 丝袜/发音/触发 alone let
    # every other ASMR upload through.
    need = max(2, (len(key) * 3 + 4) // 5)      # ceil(60%)
    return len(shared) >= need


def earliest_known(cands: list[dict], item: dict) -> dict:
    """Which upload came first. Content and chronology only -- never attribution.

    Without the content gate this returned the oldest unrelated video that a
    generic title search happened to surface: 2021 instead of 2026, off by five
    years, because sorting by pubdate alone has no notion of "same clip".
    """
    input_pub = item.get("pubdate") or 0
    bili = [c for c in cands if c.get("platform") == "bilibili"
            and c.get("pubdate") and c.get("id") != item.get("id")
            # "earlier" is meaningless for something published after the input.
            and (not input_pub or c["pubdate"] < input_pub)]
    gated = [c for c in bili if _same_subject(c, item, bili)]
    if not gated:
        return {"work_id": None, "platform": "bilibili", "pubdate": None,
                "delta_days": None, "content_basis": None,
                "confidence": "unknown",
                "reason": ("no candidate passed the content gate "
                           f"({len(bili)} dated candidates were rejected)")}
    gated.sort(key=lambda c: c["pubdate"])
    first = gated[0]
    return {
        "work_id": first["id"], "platform": "bilibili",
        "pubdate": first["pubdate"],
        "title": first.get("title"),
        "delta_days": round((item.get("pubdate", 0) - first["pubdate"]) / 86400, 2)
        if item.get("pubdate") else None,
        # Still None: the gate looked at duration and words, not at the media.
        "content_basis": None,
        "confidence": "likely" if len(gated) > 1 else "unknown",
        "gated_out": len(bili) - len(gated),
    }


def diagnosis(earliest: dict, creator: dict, blocked: list[dict],
              n_cands: int, n_edges: int) -> dict:
    """Where did the chain stop? Say which step failed, not just "not found".

    A bare None tells the user nothing actionable. This distinguishes:
      - no_copy_found      the searches never surfaced an earlier upload
      - copy_found_no_name a copy exists but nobody credited a creator
      - name_is_a_guess    a name exists but the source hedged ("应该是")
      - source_unreachable the creator is named but their own work is gone
      - complete           both fields are filled
    """
    if creator.get("name") and creator.get("level") == "guess":
        return {"stage": "name_is_a_guess",
                "detail": "the only name came from a hedged comment; treat as "
                          "a lead, not an answer"}
    if (creator.get("name") and creator.get("level") == "confirmed"
            and earliest.get("work_id")):
        return {"stage": "complete",
                "detail": "earliest copy and a confirmed creator both found"}
    if creator.get("name") and earliest.get("work_id"):
        return {"stage": "unconfirmed_creator",
                "detail": f"found an earlier upload and a {creator['level']} "
                          "creator, but attribution is not confirmed"}
    if earliest.get("work_id") and not creator.get("name"):
        return {"stage": "copy_found_no_name",
                "detail": f"found an earlier upload ({earliest['work_id']}) "
                          "but no candidate credits a creator"}
    if creator.get("name"):
        return {"stage": "source_unreachable",
                "detail": f"{creator['name']} is named, but no work of theirs "
                          "could be inspected to confirm it"}
    if blocked:
        return {"stage": "blocked",
                "detail": f"{len(blocked)} request(s) were refused; coverage "
                          "is incomplete, so absence is not evidence"}
    return {"stage": "no_copy_found",
            "detail": f"searched {n_cands} candidates over {n_edges} edges and "
                      "found no earlier upload with matching content"}


# Words that mark a channel as belonging to this genre. A witness channel must
# share some of them; a namesake musician shares none.
# CJK genre words are matched as substrings (Chinese has no spaces). Latin
# words must match as WHOLE words -- a bare "ear" matched Learn, Early, Heart,
# Clear, Gear, Year and let 25 Python tutorials pass as ASMR titles.
# CJK words have no spaces, so substring is correct for them. "ASMR" is NOT
# here: as a CJK substring it matched "Chasmrlings tutorial". Latin genre
# words are matched as whole tokens below.
_GENRE_CJK = {
    "助眠", "触发音", "触发", "丝袜", "耳朵", "掏耳", "采耳", "按摩",
    "精油", "大腿", "摩擦", "耳语", "睡眠", "睡眠導入", "オイル",
    "スクランチ",
}
# "no" removed: it matched "No. 1" in any tutorial title. "talking" /
# "notalking" alone do not identify the genre either.
_GENRE_LATIN = {
    "asmr", "sleep", "sleeping", "relax", "relaxation", "tingle", "tingles",
    "massage", "trigger", "triggers", "whisper", "whispers", "scratch",
    "scratching", "tapping", "oil", "oiled", "thigh", "thighs", "foot",
    "feet", "trigger", "asmr",
}
_GENRE_WORD = re.compile(r"[a-z]+")


def _genre_hit(title: str) -> bool:
    low = (title or "").lower()
    if any(g in low for g in _GENRE_CJK):
        return True
    words = set(_GENRE_WORD.findall(low))
    return bool(words & _GENRE_LATIN)


def _genre_overlap(titles: list[str]) -> int:
    return sum(1 for tt in titles if _genre_hit(tt))


def _witness(cands: list[dict], name: str | None) -> dict | None:
    """A work of the NAMED creator, verified by CONTENT, not by name.

    Measured failure: searching for POMEchan surfaced a channel called
    "Pome-chan" whose 11 uploads are all music (Pastel Room, Nyan cat remix,
    太鼓の達人). It is a namesake. A name match alone would have reported it as
    the creator's own catalogue and upgraded the lead to confirmed -- the exact
    mistake this project exists to avoid.

    A witness channel must share genre vocabulary with the input. If it does
    not, it is a different person who happens to share a name.
    """
    if not name:
        return None
    nl = name.lower()

    # 1. A candidate whose AUTHOR carries the name, with genre agreement.
    by_author: dict[str, list[dict]] = {}
    for c in cands:
        who = (c.get("author") or "")
        wl = who.lower()
        if not (wl and (nl in wl or wl in nl)):
            continue
        # An archived row's author is just the query name echoed back by the
        # runner. And any row WITHOUT channel_verified (yt.channel, yt.search,
        # related, space ...) is unverified too -- defaulting it to True let a
        # plain 25-title channel become confirmed. Require the field explicitly.
        if c.get("channel_verified") is not True:
            continue
        by_author.setdefault(who, []).append(c)
    for who, rows in by_author.items():
        titles = [r.get("title") or "" for r in rows]
        if _genre_overlap(titles) < max(1, len(titles) // 3):
            continue                       # namesake: same name, other subject
        best = rows[0]
        return {"witness_work_id": best["id"], "title": best.get("title"),
                "author": who, "catalogue_size": len(rows),
                "genre_matches": _genre_overlap(titles),
                "channel_verified": all(r.get("channel_verified") is True
                                        for r in rows),
                "basis": "author_match"}

    # 2. An ARCHIVED catalogue: the channel itself is gone, but a third party
    #    kept its titles. Measured on POMEchan: theasmrindex serves all 239.
    #    This is the only route when the creator's channel was deleted, which
    #    is exactly the state the reference case was stuck in.
    archived = [c for c in cands
                if c.get("platform") == "youtube"
                and (c.get("archive_of") or "").lower() == nl
                # Strict boolean: a truthy 1 / "yes" / "false" must not pass.
                and c.get("channel_verified") is True]
    if archived:
        titles = [c.get("title") or "" for c in archived]
        # The author_match branch rejects a namesake by genre. The archived
        # branch must do the same, otherwise 25 folding-tutorial titles under
        # a forged UC id become a "confirmed" creator.
        hits = _genre_overlap(titles)
        if hits < max(1, len(titles) // 3):
            return None
        return {"witness_work_id": archived[0]["id"],
                "title": archived[0].get("title"),
                "catalogue_size": len(archived),
                "genre_matches": hits,
                "channel_verified": all(c.get("channel_verified") is True
                                        for c in archived),
                "basis": "archived_catalogue",
                "note": "the creator's channel is gone; this catalogue comes "
                        "from a third-party archive"}
    return None


def known_ids(cands: list[dict], name: str | None,
              identities: list[dict] | None = None) -> dict:
    """Identifiers we have POSITIVELY attributed to the named creator.

    Deliberately conservative. A reuploader's channel id is NOT the creator's,
    and recording it as such would be a wrong answer. Measured: YouTube search
    for POMEchan returns Magaimono, PAN ASMR, Asmr Zone and a musician called
    Pome-chan -- none of them POMEchan.

    Only two sources count:
      - an archived catalogue, which is the creator's own body of work
      - an author whose titles pass the genre test (the namesake guard)
    """
    if not name:
        return {"youtube_channel": [], "handle": [], "note": "no name"}
    nl = name.lower()
    channels, handles, basis = [], [], []

    # Group archived rows by channel id. A channel id is only "the creator's
    # id" when the catalogue under it is verified AND genre-consistent;
    # otherwise a verified channel of folding tutorials would be reported as
    # the ASMR creator's id.
    by_cid: dict[str, list[dict]] = {}
    for c in cands:
        if (c.get("archive_of") or "").lower() != nl:
            continue
        if c.get("channel_verified") is not True:
            continue
        cid = c.get("channel_id")
        if isinstance(cid, str) and re.fullmatch(r"UC[A-Za-z0-9_-]{22}", cid):
            by_cid.setdefault(cid, []).append(c)
    for cid, rows in by_cid.items():
        titles = [r.get("title") or "" for r in rows]
        if _genre_overlap(titles) < max(1, len(titles) // 3):
            continue
        channels.append(cid)
        basis.append("archived_catalogue")

    # Web handles are hints, not positively attributed ids. Keep them in a
    # separate bucket so they can never be read as "the creator's id".
    for i in (identities or []):
        v = i.get("value") or ""
        if v.startswith("@"):
            handles.append(v)
        elif v.startswith("x:"):
            handles.append("x.com/" + v[2:])

    return {
        "youtube_channel": channels,
        "handle": sorted(set(handles)),
        "handle_is_unverified": bool(handles),
        "basis": sorted(set(basis)),
        # When nothing is attributed, say where the id still has to come from
        # rather than leaving an empty object that reads like "no ids exist".
        "unresolved": ([] if channels else
                       ["channel id not derivable from this engine: YouTube "
                        "search returns only reuploaders and the archive does "
                        "not index this creator by name"]),
    }


def creator_conclusion(brands: list[dict], cands: list[dict],
                       comment_names: list[str] | None = None,
                       identities: list[dict] | None = None) -> dict:
    """Who is credited as the author. Attribution only -- content is irrelevant.

    A name a person wrote in the thread outranks anything derived from titles.
    Measured failure: the thread said 桑塔阿普亚, searching that returned
    "Santa" and "Lucia" on YouTube, and the noise displaced the real answer.
    Derived signals must never overwrite a direct human statement.
    """
    details = comment_names or []
    candidates = []

    # A confident statement in the thread outranks derived signals; a hedged
    # one does not. "桑塔阿普亚，应该是这个" is a guess and belongs below a brand
    # that three independent uploaders actually wrote.
    for d in details:
        if d.get("hedged"):
            continue
        candidates.append({"name": d["name"], "level": "weak_lead",
                           "source": "comments", "hedged": False,
                           "independent_evidence_groups": 1})

    # then names repeated across independent uploaders
    for b in brands:
        candidates.append({
            "name": b["brand"],
            "level": "strong_lead" if b["accounts"] >= 3 else "weak_lead",
            "source": "titles",
            "independent_evidence_groups": b["accounts"],
            "sample": b.get("sample", [])[:4],
        })

    # guesses last, clearly marked
    for d in details:
        if d.get("hedged"):
            candidates.append({"name": d["name"], "level": "guess",
                               "source": "comments", "hedged": True,
                               "independent_evidence_groups": 0})

    if not candidates:
        return {"name": None, "level": "unconfirmed",
                "independent_evidence_groups": 0, "candidates": []}

    top = candidates[0]
    wit = _witness(cands, top["name"])
    level = top["level"]
    if (wit and wit.get("catalogue_size", 0) >= 20
            and wit.get("genre_matches", 0) >= max(1, wit["catalogue_size"] // 3)
            and wit.get("channel_verified")
            and level in ("strong_lead", "weak_lead")):
        # A large, genre-consistent catalogue whose channel identity was
        # verified is the strongest attribution obtainable without the
        # creator's own statement. Size alone is not enough: 25 arbitrary
        # titles under a forged UC id used to satisfy this.
        level = "confirmed"
    return {
        "name": top["name"], "level": level,
        "witness": wit,
        "signature_work_id": None,
        "witness_work_id": (wit or {}).get("witness_work_id"),
        "brand_sources": [top["source"]],
        "independent_evidence_groups": top["independent_evidence_groups"],
        # Every distinct claim is kept, so a human's statement is never lost
        # behind a search result that happened to rank first.
        "candidates": candidates,
        "ids": known_ids(cands, top["name"], identities),
    }


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2
    def _val(flag, default):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                return argv[i + 1]
        return default

    # Flags must be consumed as VALUES, not mistaken for the video id.
    skip = set()
    for flag in ("--budget", "--max-requests", "--routes"):
        if flag in argv:
            i = argv.index(flag)
            skip.add(i)
            if i + 1 < len(argv):
                skip.add(i + 1)
    positional = [a for i, a in enumerate(argv)
                  if i not in skip and not a.startswith("--")]
    target = positional[0] if positional else None
    if not target:
        print(__doc__)
        return 2
    try:
        budget_s = float(_val("--budget", "90"))
        max_requests = int(_val("--max-requests", "120"))
    except ValueError as e:
        raise SystemExit(f"invalid numeric flag: {e}")

    from trace_net import live_net
    item = live_net().probe(target)
    out = run(item, net=live_net().channels(),
              budget_s=budget_s, max_requests=max_requests)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
