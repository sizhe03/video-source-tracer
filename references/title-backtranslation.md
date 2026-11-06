# Title back-translation — the step that actually finds the original

## Why this matters more than any tool

Searching YouTube for `用麦克风在姐姐的大腿上摩擦 丝袜触发音` returns **zero**
results. Searching for `太もも マイク 挟む ASMR` returns the creator.

The B站 re-uploader did not invent a title. They **translated** one. The
translation is lossy in surface form and faithful in structure, so the
reverse direction is highly recoverable — as long as you translate *segment by
segment*, never as a whole sentence.

## Worked example (this is the canonical case)

|  | text |
|---|---|
| B站 | `【ASMR】用麦克风在姐姐的大腿上摩擦_丝袜触发音` |
| YouTube 原题 | `【ASMR】シスターさんの太ももにマイクを擦り付けて脱ぎたて靴下をマイクにすりすり♡/タッピング/吐息/呼吸音/脚フェチ【甘楽らむ/実写】` |

Segment mapping:

| 中文片段 | 日文片段 | 类型 |
|---|---|---|
| 姐姐 | シスターさん | 人称 |
| 大腿 | 太もも | 身体部位 |
| 用麦克风…摩擦 | マイクを擦り付けて | 动作 |
| 丝袜触发音 | 脱ぎたて靴下をマイクにすりすり | 动作+物料（被压缩成一个词） |

Note the loss: `脱ぎたて靴下をマイクにすりすり` (freshly-removed socks rubbed
on the mic) collapsed into `丝袜触发音`. That is why keyword search fails —
**the most distinctive phrase was deleted**. Which gives the retrieval rule:

> Search the language of the *original*, using the phrases the translator
> would have **dropped**, not the ones they kept.

## Reverse-translation table

### Person / role
| 中文 | 日文 | 英文 |
|---|---|---|
| 姐姐 | シスターさん / お姉さん | sister / big sister |
| 妹妹 | 妹 | little sister |
| 女仆 | メイド | maid |
| 老师 | 先生 / 教師 | teacher |
| 女友 | 彼女 | girlfriend |

### Garment (highest retrieval value — very specific in Japanese)
| 中文 | 日文 |
|---|---|
| 白丝 / 白色过膝袜 | 白ニーハイ / 白タイツ |
| 黑丝 / 连裤袜 | 黒ストッキング / 黒タイツ / パンスト |
| 网袜 | 網タイツ |
| 吊带袜 | ガーター / ニーハイ |
| 丝袜（泛指） | タイツ / ストッキング |
| 刚脱下的袜子 | 脱ぎたて靴下 |

### Action / sound
| 中文 | 日文 |
|---|---|
| 摩擦 | 擦り付けて / こすりつける / すりすり |
| 夹 | 挟む |
| 贴 / 蹭 | 密着 / ぺちぺち |
| 刮擦 | スクラッチ / スクラッチング |
| 敲击 | タッピング |
| 掏耳 | 耳かき |
| 触发音 | トリガー音 / 音 |
| 无人声 | 声なし / No Talking |
| 助眠 | 睡眠導入 |

### Equipment (near-unique identifiers — search these first)
`3Dio` · `KU100` · `SR3D` · `RODE NT5` · `Blue Yeti` · `TASCAM`

### Genre wrapper
`【ASMR】…【作者名/実写】` — the trailing `【name/実写】` bracket is where the
creator self-identifies. **Always try to reconstruct that bracket**; it is the
single most decisive token in the whole title.

## Re-upload rewrite patterns (what you must normalize away)

| Pattern | Example |
|---|---|
| `/` → `_` | `…摩擦/丝袜…` → `…摩擦_丝袜…` |
| drop `♡ ♥ ❤️` and `【】` | common on every Chinese mirror |
| append a disclaimer | `正常穿搭，无任何不良引导` — **burned in over the video**, absent from the original |
| prefix the genre | `【ASMR】` prepended even when the original had it |
| compress 3 phrases to 1 | `脱ぎたて靴下をマイクにすりすり` → `丝袜触发音` |
| translate author bracket away | `【甘楽らむ/実写】` → nothing |
| transliterate the creator | `甘楽らむ` → `甘楽` / `Kanra Ramu` / `らむち` |

## Search ordering (do it in this order, stop when you get signal)

1. **Unique noun** — `脱ぎたて靴下`, `3Dio`, `KU100`, garment names
2. **Action verb in original language** — `擦り付けて`, `挟む`
3. **Person/role** — `シスターさん`
4. **Genre** — `ASMR`, `実写ASMR`
5. Never: the translated title as a whole

## YouTube localises titles — match bilingually

A creator's canonical title is Japanese; YouTube serves an English rendering of
the same upload depending on viewer locale. In the reference trace the target
video appeared as:

```
[ASMR] Rubbing a microphone against a sister's thigh and rubbing her freshly removed socks...
```

not as `【ASMR】シスターさんの太ももにマイクを擦り付けて…`. Searching only the
Japanese terms scored that video **0 hits** and buried it behind an unrelated
clip.

So every `--terms` list must carry both renderings of the same noun:

```
太もも,thigh  マイク,microphone  靴下,socks  シスター,sister  タイツ,tights
```

Mechanical translations to expect (YouTube's own MT, not a translator's):

| 日文 | YouTube English |
|---|---|
| 太もも | thigh(s) |
| マイクを擦り付けて | rubbing a microphone against |
| 脱ぎたて靴下 | freshly removed socks |
| すりすり | rubbing / stroking |
| 声なし | No Talking / no voice |
| 実写 | live action / real footage |
| 脚フェチ | leg fetish |

## Multi-language fan-out

Fan translations exist on the same axis. If Japanese search fails, try the
English mechanical translation of the *Japanese*, not of the Chinese:

```
太ももにマイクを擦り付けて  →  rubbing a microphone against thighs
脱ぎたて靴下                →  freshly removed socks
```

Korean and Traditional Chinese mirrors (`/zh-tw/`, `아스MR`) often carry the
Japanese title verbatim — those hits are worth more than re-translated ones.
