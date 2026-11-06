#!/usr/bin/env python
"""Stage 3c - channel timeline sweep.

This is the path that actually cracked the reference trace. Search engines rank
by relevance, which buries a 12-minute upload published six months ago under a
dozen newer look-alikes. A creator's own channel has no such ranking: you can
walk it chronologically and filter purely by duration.

Why duration: re-upload crops are predictable. A 723 s master becomes 662 s,
then 631 s. So a 590-760 s window around the known copy catches the master.

Usage:
    python channel.py UCu8MICHevQra3S5n2bHYgQw --duration 631 --window 100
    python channel.py @KanraRamu --duration 631 --terms "太もも,マイク,靴下"
    python channel.py UCu8MICHevQra3S5n2bHYgQw --all

When several candidates land on the same duration, duration alone cannot
separate them. Pass --terms with the distinctive nouns recovered from the
back-translated title; matches are then ranked by term hits first, length
second.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

from _common import emit, fmt_dur, http, note, setup_stdio


def resolve_channel(arg: str) -> str | None:
    """Accept UC... or @handle and return the UC id."""
    if re.fullmatch(r"UC[\w-]{22}", arg):
        return arg
    handle = arg.lstrip("@")
    note(f"resolving handle @{handle}")
    r = http(f"https://www.youtube.com/@{handle}/videos", timeout=30)
    if r.status != 200:
        return None
    t = r.text()
    m = re.search(r'"channelId":"(UC[\w-]{22})"', t) or re.search(
        r"channel/(UC[\w-]{22})", t
    )
    return m.group(1) if m else None


def list_videos(cid: str, limit: int = 400) -> list[dict]:
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist", "-J", "--no-warnings",
        "--playlist-end", str(limit),
        f"https://www.youtube.com/channel/{cid}/videos",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="ignore", timeout=420)
    if p.returncode != 0:
        note(f"channel listing failed: {(p.stderr or '')[-200:]}")
        return []
    d = json.loads(p.stdout)
    out = []
    for e in d.get("entries") or []:
        out.append(
            {
                "id": e.get("id"),
                "title": e.get("title"),
                "duration": int(e.get("duration") or 0),
                "views": e.get("view_count"),
            }
        )
    note(f"channel lists {len(out)} videos")
    return out


def main(argv: list[str]) -> int:
    setup_stdio()
    if not argv:
        print(__doc__)
        return 2

    target = argv[0]
    duration = None
    window = 100
    terms: list[str] = []
    show_all = "--all" in argv

    if "--duration" in argv:
        i = argv.index("--duration")
        duration = int(argv[i + 1])
    if "--window" in argv:
        i = argv.index("--window")
        window = int(argv[i + 1])
    if "--terms" in argv:
        i = argv.index("--terms")
        terms = [t.strip() for t in argv[i + 1].split(",") if t.strip()]

    cid = resolve_channel(target)
    if not cid:
        emit({"ok": False, "error": "could not resolve channel", "input": target})
        return 1

    vids = list_videos(cid)
    if not vids:
        emit({"ok": False, "error": "no videos listed", "channel_id": cid})
        return 1

    if duration and not show_all:
        lo, hi = duration - window, duration + window
        # A window is an ordering hint, not an exclusion: a shortened clip of a
        # long stream, or a full version of a short teaser, sits far outside it.
        inside = [v for v in vids if lo <= v["duration"] <= hi]
        outside = [v for v in vids if not (lo <= v["duration"] <= hi)]
        hits = inside + outside
        for v in hits:
            title = v.get("title") or ""
            v["in_window"] = lo <= v["duration"] <= hi
            v["term_hits"] = [t for t in terms if t.lower() in title.lower()]
            # YouTube localises titles per viewer locale, so the same upload can
            # surface in English while the creator's own title is Japanese.
            # Count an "upstream" bonus for being longer than the known copy:
            # it is the single strongest structural hint in a crop chain.
            v["longer_than_ref"] = v["duration"] > duration
        # Order of trust:
        #   1. term hits      -> WHAT the video is about (semantic)
        #   2. longer than ref -> WHERE it sits in the chain (structural)
        #   3. duration       -> tiebreak only
        # A 688 s unrelated clip must never outrank a 723 s true upstream.
        hits.sort(key=lambda v: (not v["in_window"], -len(v["term_hits"]),
                                 not v["longer_than_ref"],
                                 -(v["duration"] if v["longer_than_ref"] else 0)))
        emit(
            {
                "ok": True,
                "channel_id": cid,
                "total_videos": len(vids),
                "filter": {"duration_ref": duration, "window": window,
                           "range": [lo, hi]},
                "matches": [
                    {**v, "delta_s": v["duration"] - duration,
                     "duration_h": fmt_dur(v["duration"])}
                    for v in hits
                ],
                "ranking_rule": (
                    "1) term hits desc  2) longer-than-reference first  "
                    "3) duration desc as tiebreak"
                ),
                "terms_used": terms,
                "bilingual_warning": (
                    "YouTube localises titles; if a term never matches, "
                    "re-run with the English rendering of the same phrase"
                ),
                "note": (
                    "a longer match is the likely upstream cut; "
                    "check several before choosing"
                ),
            }
        )
        return 0

    vids.sort(key=lambda v: -(v["duration"] or 0))
    emit(
        {
            "ok": True,
            "channel_id": cid,
            "total_videos": len(vids),
            "matches": [
                {**v, "duration_h": fmt_dur(v["duration"])} for v in vids[:60]
            ],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
