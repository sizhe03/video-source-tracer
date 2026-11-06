# Alias completion — a short token is not a name

A comment says `甘楽`. Searching that token alone returns the place name
甘楽町, or a different channel (`甘楽デイティー / Kanra Deity`). The creator is
`甘楽らむ`.

**Concluding "no such author" there is the bug this file exists to prevent.**
A failed search is a fact about the search, never about the world.

## Measured: why one query can never decide

| query | what comes back | useful? |
|---|---|---|
| `甘楽` | 甘楽デイティー / Kanra Deity, 甘楽町 travel videos | no |
| `甘楽 ASMR` | **甘楽らむ** (20/20 of results) | **yes** |
| `甘樂` | 台灣甘樂, THE CAN甘樂文創 | no |
| `甘樂 ASMR` | 台灣甘樂 | no (genre gate rejects it) |
| `甘乐` | 甘乐 — a *different* person | no |
| `甘樂らむ` | 甘乐千岁 (unrelated vtuber) | **poison** |

Two lessons:

1. **The bare token is the worst query.** A genre hint is not decoration; it is
   what moves the result set into the right subculture.
2. **Variants can poison.** `甘樂らむ` looks like the traditional form of
   `甘楽らむ` but resolves to someone else. So variants are tried as *separate
   searches*, keyed by their own spelling — never concatenated blindly and never
   merged into one candidate pool.

## The ladder (in order, stop when confirmed)

```
1. bridge     other uploads by the same re-uploader → 【name】 brackets
              in-site search for the suspected full name
2. variant    same token, other spellings (楽/樂/乐, ら/拉)
3. context    <every spelling> + ASMR / 助眠 / 睡眠導入
4. thread     concatenations with other tokens in the same comment thread
```

A step that returns 20 results with zero channel matches is a **miss for that
step**, not for the token. Only a *confirmed* candidate ends the ladder.

## Confirmation (three stages, all required)

```
a. the token appears in a channel name
b. that exact channel name re-searched returns mostly that same channel
c. the channel's own titles contain genre vocabulary
```

Stage (c) exists because `爱看` matches 爱看动漫Animation and KK爱看 — both real
channels, and "the channel exists" is not the same as "this channel is the
author of this clip".

## CJK variant table

Keep it **small and explicit**. Combinatorial substitution explodes and mostly
manufactures nonsense; substitute one character at a time.

| char | variants |
|---|---|
| 楽 | 樂 乐 |
| 樂 | 楽 乐 |
| 乐 | 楽 樂 |
| ら | 拉 |
| む | 姆 |
| さ | 沙 |
| く | 库 |

Korean and Traditional Chinese mirrors frequently keep the Japanese title
verbatim — those hits are worth more than re-translated ones.

## The full evidence chain to aim for

```
comment short name   →   a re-upload that carries the full signature
                     →   candidate channel
                     →   content match on the original clip
```

If the chain stops at the channel and never reaches the clip, say so:
**"channel confirmed, original clip not yet verified"** — do not upgrade the
claim to match the confidence of the channel identification.

## When the comments carry no name at all

Do not stop. Move to non-textual leads:

- other re-uploads of the same clip (their title / description / comments)
- watermarks, burned-in subtitles, on-screen text
- spoken name, if any
- reverse image search on a keyframe (Lens / Yandex, both anonymous)

Then repeat the ladder on whatever new text those produce.
