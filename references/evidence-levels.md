# Evidence contract

## Independent questions

| Field | Question | Interpretation |
|---|---|---|
| account | Which exact platform account exists? | Not creator confirmation |
| provenance | Which inspected comment, credit or link led here? | Preserve source URL and text |
| content_match | Does the actual work overlap the input? | Compare frames/audio; titles do not count |
| attribution | Why is this the creator rather than a reposter? | Require a recorded creator attribution check |
| relevance | Do sampled titles share distinctive terms? | Retrieval only; never upgrade to attribution |

`relevance=no_overlap` does not refute common origin: translated or rewritten
titles may share no terms. Preserve `not_checked` where input is unavailable.
Report source coverage separately from the evidence verdict.

For provenance, a bare URL stays unverified. Supply a record with `url`,
`resolved: true`, matching `account_id`, and the actual relevant `excerpt`
only after inspecting its destination. This does not grant creator attribution.

## Verified attribution record

Pass the expected candidate account ID and input work ID separately to the
validator. The record must contain:

```json
{
  "url": "https://example.com/original/123",
  "work_id": "123",
  "account_id": "UC_candidate",
  "input_work_id": "BVinput",
  "basis": "Explain the inspected evidence linking this input to this creator",
  "evidence_type": "creator_credit",
  "evidence_quote": "Short relevant excerpt actually observed",
  "checked_at": "2026-11-05T12:00:00Z",
  "verified": true
}
```

Allowed evidence types: `creator_credit`, `creator_statement`,
`official_crosslink`. A matching upload or username is insufficient. The
investigator must inspect the cited artefact and judge whether it establishes
creation rather than merely an upload, mention, guess or repost. Record any
contradictions; do not mark unresolved evidence verified.

The code checks required nonempty strings, HTTP(S) URL form, ISO timestamp,
explicit boolean verification, evidence type, candidate account and input work
identity. This is a consistency check on a reviewed record, not automated web
verification. `work_id` identifies the cited source work; `input_work_id`
identifies the clip being traced. They may differ for reposts.

Use the same validator for attribution and early stopping. Bare strings,
`verified=false`, unsupported evidence types, mismatched identities, missing
fields and work comparisons alone cannot confirm attribution or end the search.
Unverified records remain visible with an `incomplete` explanation.

## Wording

- **已确认原作者**: inspected creator-attribution evidence supports this account
  for this input; include the source and reasoning.
- **较强线索**: a plausible account or source link exists, but attribution remains
  unresolved. A matching work alone remains in this category for authorship.
- **尚未确认**: insufficient evidence or multiple unresolved candidates.

A field named `supports` does not override contradictory evidence or missing
inspection. Search helpers currently do not populate verified attribution by
themselves; complete the investigation before making the public claim.

## Audio and visual evidence

Treat experimental audio fingerprints as overlap evidence, not author evidence.
Inspect both raw competitors and localization width. A merged display peak does
not imply a unique offset. Empty `proves` means no positive conclusion was made.
Read spectral profiles to distinguish broad separated bands from narrow peaks.

Visual controls must include a known positive and plausible negatives. Matching
watermarks, backgrounds or one shared intro are not sufficient. Check multiple
content timestamps. Shared music, stock footage and dubbed tracks can link
otherwise different videos. Never translate a score into an uncalibrated
confidence percentage or legacy L1–L4 author-certainty label.
