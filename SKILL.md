---
name: bb03-trace-video-source
description: Trace a video's original creator and original upload across platforms from a URL, short link, clip, screenshot, title or incomplete credit. Use for 找原作者、找原视频、查出处、搬运来源、同视频转载 and reverse video search on Bilibili, YouTube, Douyin, Xiaohongshu, TikTok, Instagram, X and other platforms. Inspect repost descriptions and nested comments, expand partial names, and verify content and creator attribution separately.
whenToUse: 用户给了一个视频链接、短链、BV号、截图、标题或不完整的署名，想知道原视频是谁拍的、最初发在哪里、这个搬运号是从哪搬的。触发词包括 找原作者、原作者是谁、找原视频、原出处、查出处、这个视频哪来的、搬的谁的、搬运来源、同视频转载、谁先发的、最早发布、油管原片、抖音原号、reverse video search、find original creator、original upload、who made this video、repost source。适用于 Bilibili、YouTube、抖音、快手、小红书、TikTok、Instagram、X 等平台的视频溯源。
---

# Trace video sources

Find the creator and original upload through a documented chain of evidence.
Treat platform scripts as helpers; use the available platform connector or page
reader when a script does not support that service. Respect a user-requested
search method, such as image search instead of title search.

## Start every independent route at once

```bash
python scripts/trace.py BV1p4KhzoEck
python scripts/trace.py BV1p4KhzoEck --budget 120 --max-requests 200
```

`trace.py` is the entry point. Every independent request is launched at t=0 and
a lead is dispatched the moment it appears -- there is no phase ordering.
`--budget` is seconds; `--max-requests` counts HTTP calls for the whole run.

It returns three things:

| field | answers | never claims |
|---|---|---|
| `earliest_known` | which upload came first, and by how many days | who made it |
| `creator_conclusion` | who is credited as the author, plus ids | that the credit is true |
| `diagnosis` | where the chain stopped | — |

`creator_conclusion.level` is one of `strong_lead` / `weak_lead` / `guess` /
`unconfirmed`. The engine NEVER outputs `confirmed`: string evidence (titles,
archive pages, name matching) cannot prove authorship, so attribution is
always delivered as a lead for a human to confirm. `strong_lead` means a
verified channel catalogue or several independent uploaders agree; `guess`
means the only name came from a hedged comment. `ids` holds channel/handle
identifier leads from string evidence, marked `status=lead_from_string_evidence` with a `confirm_by` hint for a
human to open the channel; `pending_queries` lists web searches still
worth running.

`discover.py` remains available for a narrower job -- same-platform copies,
comments, cross-platform search and cover hashing only. It does not do
attribution and does not query the open web.

Four routes are independent and run **concurrently**, not in a fixed order:

| route | what it does |
|---|---|
| `same_site` | same-platform copies: exact title, stripped title, then every name lead |
| `comments` | description, credits, top-level and nested comments -> typed leads |
| `cross` | other platforms via YouTube only: title terms, then every name lead |
| `image` | cover pHash against candidate covers as they appear (cover only -- no keyframe or audio logic here) |

**Dependencies still run in order.** You cannot compare a cover before a
candidate exists. Only independent work is parallelised. Channel scanning and
audio comparison are separate scripts -- this module only produces the
candidate table they consume.

**Leads are shared while the run is in progress**, not merged at the end:

- a comment says `抖音号 25914348306` -> a `platform_id` lead -> a candidate row
- a comment says `甘楽` -> a `name` lead -> both search routes re-query it
- only `comments` mints leads (`_harvest`); the search routes consume them. A hit found by same-site search does not become a new lead
- image finds a near-identical cover -> that candidate moves up the verify queue

Three constraints the module enforces:

1. **One candidate table.** Keyed by `platform:native_id`, each row carries its
   sources, evidence and state (`discovered` -> `checked` -> `verified` ->
   `attributed`). A video found by two routes is one row, and `duplicates_merged`
   reports how many merges happened.
2. **Shared rate limits and a global budget.** The per-host gap is enforced
   across threads for API calls, so four routes do not mean four times the
   request rate to one host. `--budget` (seconds) and `--max-requests` cap the
   whole run. Cover images are the exception: they are fetched from
   `i0.hdslb.com` and are charged to the budget but paced separately.
   `comments.py`'s own throttle is disabled for the duration of the run so the
   board's limiter is the only pacing authority.
3. **One confirmation standard.** A candidate stays `discovered` until content
   and attribution are actually checked. `content_match_state` is hardcoded to
   `not_checked`: **this module never compares content and never attributes
   anything**. It writes only `discovered` and `checked`; `verified` and
   `attributed` come from the verification scripts. The run therefore stops on
   budget exhaustion, not on a reached conclusion.

A lead is a **query, never a finding**. `platform_id` rows are pointers to go and
open, not identities.

## Workflow

1. Normalize the input and preserve its canonical URL and native ID.
2. Run `trace.py`. It launches view / related / space / search (both `order=pubdate`
   and `order=click`) / comments / ytsearch / cover reverse-search together.
3. Read `earliest_known`, `creator_conclusion` and `diagnosis`. If
   `pending_queries` is non-empty, run those web searches -- they are how a
   deleted creator's channel id is recovered.
4. Expand partial names through spelling variants, genre context and related reposts.
5. Check candidate accounts and locate the specific work inside them.
6. Compare content with `auprint.py` / `verify.py`; keep content identity and
   creator attribution in separate fields. A same-audio result proves the clip
   is the same, never that a given account made it.
7. Report the supported conclusion, the diagnosis stage, and coverage.

**Search order matters.** Bilibili's default relevance sort buries older
uploads: a measured title query returned 50 rows with neither known copy
present, while `order=pubdate` returned 6 rows containing both. Always ask for
date order as well.

**A blocked request is not an empty result.** Check `coverage_incomplete`
before concluding that something does not exist.

Do not treat a failed query, unavailable comment thread or failed audio matcher
as proof that a source does not exist. Continue independent paths when one is blocked.

## Normalize and discover

```bash
python scripts/resolve.py "https://b23.tv/kwoerN3"
python scripts/probe.py BV1TrxQ64EXo
python scripts/find_earlier.py BV1TrxQ64EXo
python scripts/find_earlier.py --yt "distinctive words" --duration 723
```

For screenshots, discover candidates with available reverse-image search,
watermarks, visible credits, subtitles and distinctive objects. A thumbnail pool
is incomplete, especially when reposts change their covers. Once candidates
exist, compare multiple frames; image similarity cannot identify an author by itself.

Use several query families: exact and shortened titles, distinctive objects and
actions, spelling variants, translations and genre-specific phrases. Try
same-platform search as well as external search. Preserve plausible candidates
with rewritten titles and different lengths. Duration, rank, resolution and date
are retrieval hints, not evidence of creator identity.

`find_earlier.py` emits these priorities:

| Bucket | Interpretation |
|---|---|
| `examine_first` | High retrieval priority; content remains unverified |
| `worth_checking` | Candidate needs content comparison and credit inspection |
| `struct_only` | Structural hints without title support; inspect credits/comments |
| `rank_only` | Ranking alone; broaden queries before expensive verification |
| `unlikely` | No supporting retrieval signal in this search |

`title_supported_candidates` describes titles only. `content_match_state` is
`not_checked` at discovery. A later upload can still be valuable because its
comments identify the original. Compare dates as chronology only after checking
that the items contain the same content.

## Execute both task queues

Keep `comment_candidates` and `verification_candidates` as independent queues.
The same candidate may appear in both. Read comments on same-title reposts too;
they may contain the only full name or original URL. Prioritize inexpensive
credit inspection before downloading full media.

Every comment task includes `platform`, `url`, `read_with`, `status`, `evidence`,
`blocked_reason` and `on_blocked`. The script creates pending tasks; it does not
perform or persist browser work. Maintain their state in the investigation log:

- `pending`: not read yet.
- `completed`: record the actual inspected scope, evidence URL/comment ID,
  relevant text, and whether nested replies were opened. Finding nothing in a
  partial view does not mean the entire thread was checked.
- `blocked`: record the observed access problem and retain the unfinished task.

Use `scripts/comments.py BV... --verify` only for Bilibili. For YouTube and other
platforms, use the specified page-reading route or an appropriate connector.
Read descriptions, pinned comments, source-related threads and nested replies.
Do not pass another platform's video ID to the Bilibili API. Read descriptions
separately when the comments script does not expose them.

After extracting a direct URL or handle, open/resolve it before using it as a
name query. Preserve every direct lead even when the name-candidate budget is full.
A generated task or search result is not a completed source check.

## Complete partial names

A comment such as `甘樂` is a starting token, not necessarily a full name.
Search separately for `甘樂`, `甘楽`, `甘乐`, then add the observed genre and
content vocabulary. A related repost may expose the full credit `甘楽らむ`.
Use this example to learn the method; do not assume it identifies another clip.

```bash
python scripts/alias_resolve.py 甘樂 --verify
python scripts/alias_resolve.py --bvid BV1hM4w6fE6o --verify
```

Try, in order suited to available evidence:

1. Other posts by the same reposter; title brackets, descriptions and credits.
2. Same-platform search for the token and spelling variants.
3. Token plus the video's genre, objects, actions and likely source language.
4. Other reposts' comments, full-name credits and official account cross-links.
5. If text leads fail: reverse-image search on distinctive frames, watermarks,
   spoken names, subtitle OCR and candidate-channel timelines.

Keep differently spelled and same-name accounts separate until public evidence
links them. Genre similarity and a matching work inside an account do not prove
that the account created it. The current automatic genre hints emphasize ASMR;
for other genres inspect candidate works directly rather than trusting that gate.
See `references/alias-completion.md` and `references/title-backtranslation.md`.

## Record attribution explicitly

Separate account existence, provenance, content match, creator attribution and
retrieval relevance. See `references/evidence-levels.md` for the exact contract.

`authorcheck.py` accepts attribution only from a recorded, explicitly verified
creator credit/statement or official cross-link tied to the candidate account
and the input work. The caller must actually inspect the evidence; the function
checks the record's consistency, not the truth of a web page. Never set a flag
just to obtain an `attributed` result.

Bare links, unverified comments, wrong-account records and content matches alone
must not grant attribution or stop the alias search. Current search helpers do
not automatically populate the verified attribution record. Preserve uncertainty
until the investigator completes that check.

## Verify content with controls

```bash
python scripts/verify.py pixels A.jpg B.jpg
python scripts/verify.py frames A.mp4 B.mp4
python scripts/verify.py sift A.jpg B.jpg --neg N1.jpg --neg N2.jpg --pos P.jpg
python scripts/auprint.py --video ORIG.mp4 --video COPY.mp4
```

Use a known positive pair and plausible same-genre negatives. Check that matching
features cover the subject rather than a watermark, shared background or player
chrome. Compare multiple timestamps and consistent offsets. A single matching
frame may be a reused intro or stock shot. Numeric thresholds are heuristics,
not transferable confidence percentages. Internal legacy L1–L4 labels in helper
outputs must not become author-confidence labels in the report.

For cross-platform discovery, use bilingual terms and candidate-channel timelines:

```bash
python scripts/channel.py "@candidate" --duration 631 --terms "object,translated-object"
```

Widen duration windows if no source is found. Clips can be cropped, concatenated,
slowed or prefixed with an intro. Use `retitle_probe.py` as a candidate ranker,
then verify; it cannot prove an original author.

## Interpret experimental audio results

| Verdict | Permitted interpretation |
|---|---|
| `same_audio_segment` | Fingerprint evidence supports an audio overlap; verify visually |
| `ambiguous_alignment` | Several candidate alignments; no unique offset established |
| `insufficient_evidence` | Inadequate evidence or poor localization; do not assert difference |
| `incomparable_tonal_input` | Narrow spectral peaks dominate; method cannot decide |
| `no_match` / `incomparable` | This method did not establish overlap; common origin remains possible |

Display grouping must not remove raw competing peaks from the decision. Inspect
`raw_alignments`, `rival_lag_votes`, `alignment_width_bins` and
`alignment_interval_s`. A broad flat top or shallow unresolved competition must
remain undecided. The 0.85 valley ratio only groups the displayed peaks; it is
not a probability or an independent reason to claim a match.

Inspect `spectral_profile_a/b`: occupied envelope width, envelope fill,
within-band fill, strongest-six-bin amplitude share and narrow-component share.
Empty gaps between broad noise bands do not by themselves imply pure tones.
These features and all thresholds remain experimental.

Matching audio can be shared music, dubbing or a stock track used in unrelated
videos. It establishes neither identical video nor creator identity. Time-stretch
and edited-segment alignment are not implemented. Do not claim the matcher can
reliably detect those limitations on arbitrary recordings.

## Access and incomplete coverage

Try an available public route first. Use platform connectors where sufficient.
When an observed login wall or human challenge blocks the task, explain the
specific missing access and use the supported interactive login/handoff process.
Reuse access already authorized in the session; do not repeatedly ask permission.
Do not solve CAPTCHAs or cycle sessions to evade persistent access controls.

Do not request passwords or print session secrets. Use a scoped cookie file only
when the user chooses that supported route. Keep credentials out of evidence logs
and packages. Continue other public candidates while a path is blocked.

Treat numerical endpoint limits and access tables in `references/platforms.md`
as historical observations requiring current verification, not universal platform
rules. Distinguish zero comments from a failed or truncated fetch.

## Report

Lead with the supported author/source result and direct links. Distinguish finding
an account, finding matching content, and establishing original creator attribution.
Include the repost chain, why each link matters, pending comment tasks, tested
comparisons and search scope. Never claim all reposts were found from a finite
search result set. Do not infer absence from `no_credible_match`.

When relying on the audio path, state that validation used synthetic signals and
mocked interfaces; real recordings, encoding changes, different genres and online
access have not been comprehensively validated. Use results to assist inspection.

## Tools and references

Run from this skill directory with Python 3.10+. Media helpers need NumPy,
OpenCV where applicable, ffmpeg (`TRACE_FFMPEG` override) and yt-dlp for supported
YouTube paths. Check a script's usage before supplying options; cookie support
varies by entry point. Run `python tests/run_all.py` for the offline regression set.

- `resolve.py`, `probe.py`: normalize links and inspect metadata.
- `trace.py`: the entry point. Task pool with instant dispatch; returns
  `earliest_known` / `creator_conclusion` / `diagnosis`.
- `trace_net.py`: the network channels `trace.py` uses, injectable for tests.
- `discover.py`: a narrower concurrent discovery pass (copies, comments, cross
  platform, cover hash). No attribution, no web search.
- `find_earlier.py`, `deep_sweep.py`, `channel.py`: discover candidates.
- `comments.py`, `alias_resolve.py`, `authorcheck.py`: inspect leads and accounts.
- `verify.py`, `auprint.py`, `retitle_probe.py`: comparison and candidate ranking.
- `references/discovery-completeness.md`: discovery blind spots and coverage.
- `references/platforms.md`: historical endpoint observations and access routes.
- `references/title-backtranslation.md`: multilingual query construction.
- `references/alias-completion.md`: partial-name expansion examples.
- `references/evidence-levels.md`: evidence and attribution contract.
- `references/calibration-log.md`: the measured numbers behind every threshold,
  which ones are stale, and what has never been tested. Read it before
  re-deriving a constant.
