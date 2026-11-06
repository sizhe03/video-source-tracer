# Discovery completeness — why title search misses real copies

The hardest lesson from the reference trace. A third, **earlier** re-upload
existed the whole time and every text-based method missed it.

```
BV1BuNgzREmz   877 s   2026-03-08   奶油泡芙焦糖玛奇朵
标题：甘樂助眠 可爱女仆的摩擦哄睡💕白丝吊带抹茶你的耳朵asmr(๑•ᴗ•๑)♡
简介：YouTube转载
tags：催眠 吊带袜 小姐姐 asmr 触发音 白丝女仆 丝袜摩擦 夹腿 哄睡助眠
播放：82,550
```

Compare with the copy we traced:

```
BV1cU5C68EEi   662 s   2026-05-13   shao御
标题：用麦克风在姐姐的大腿上摩擦/丝袜触发音
```

The March upload is **two months earlier, 215 s longer, 1080p, and its title
contains `甘樂`** — it is simultaneously an earlier hand AND the bridge between
the short name and the full one. Missing it is a real defect, not a detail.

## What each method actually found

| method | found it? | why |
|---|---|---|
| `find_earlier.py` title search | **no** | re-titled; no shared substring |
| search `甘樂助眠` / `甘乐助眠` | **no** | rank far below page 1 |
| search `白丝吊带` `女仆 摩擦` `丝袜 哄睡` | **no** | same |
| search by tag (`白丝女仆`, `丝袜摩擦`) | **no** | same |
| `visual_pool` thumbnail pHash (48 candidates) | **no** | cover art was redone; nearest unrelated thumb was 112 bits, real one never entered pool |
| `retitle_probe` frame prints | **no** | not in the harvested pool at all |
| `/x/web-interface/archive/related` from 3 seeds | **no** | not related-linked either |
| search the literal string `BV1BuNgzREmz` | **yes** | direct id lookup works |

So: it *is* indexed, reachable, 82k views — but ranked effectively invisibly
under every natural query. **bilibili search is not exhaustive and must never be
treated as a coverage guarantee.**

## Consequences for the skill

1. **Never claim "all re-uploads".** Write what was searched and what was ruled
   out. `find_earlier.py` returning `no_credible_match` means *this query set
   did not find one*, not *none exists*.
2. **A duration band is the strongest cheap filter** that survives re-titling:
   `0.5–1.6 ×` reference catches 877 s from 662 s.
3. **Frame-level comparison beats thumbnails** when covers are redone — but it
   can only rank what is already in the pool, so the pool is the real bottleneck.
4. **Enumerate suspected uploaders directly.** Once any one hand is known, pull
   its `mid` and walk `recArchivesByKeywords` with `pn` pagination. That does not
   depend on search ranking at all. (`BV1BuNgzREmz` is not on page 1 of its own
   author's 50-item listing — so paginate, do not stop after one page.)
5. **Ask the operator what they already know.** In this trace the operator had
   found the `甘樂` comment themselves; one question would have redirected the
   whole search. When a trace stalls, saying "I searched X, Y, Z and found
   nothing — do you already know a name, a channel, or another copy?" is cheaper
   than another round of queries.

## Reporting rule

```
搜索范围：<queries used>, <pages>, <duration band>
已排除：<candidates tested with controls>
未覆盖：<what could not be reached — login walls, search ranking, pagination limits>
```

Never write "已找全" / "all copies". Write "在已搜索范围内找到 N 条；未能排除仍有
更早版本" unless coverage was genuinely exhaustive.
