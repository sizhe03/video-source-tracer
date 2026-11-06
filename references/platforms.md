# Platform endpoints — what answers anonymously, what does not

Every row below was exercised in a real trace. The `anon` column is measured,
not assumed.

## Blocked? try one bypass, then ask

The order is: **one cheap bypass attempt → ask the operator → only then give up.**
Escalate to a request instead of grinding, but do not skip the cheap attempt —
the app-transport trick below lifted a comment thread 8× with two query flags.

Two operator actions beat any workaround:

- **`cookies.txt`** (Netscape format) — lifts the rest of bilibili's reply cap,
  YouTube age gates, and `LOGIN_REQUIRED`.
- **clearing a captcha in-browser** — anything returning "unusual traffic" or a
  slider. The scripts reuse the same IP, so once cleared in the browser the API
  calls generally pass too.

Worked bypasses worth trying before asking:

| Block | Bypass | Result |
|---|---|---|
| bilibili reply cap 3 | add `mobi_app=android&platform=android` | **19 → 25 of 38**, measured |
| 412 on search | prime the cookie jar by fetching the homepage once | `code 0` |
| -352 space/risk | 6 s → 12 s → 24 s backoff, re-prime each retry | recovers |
| YouTube `LOGIN_REQUIRED` | rotate `player_client` (`android_vr`, `tv`, `web_safari`, …) | **all failed**; this one genuinely needs cookies |

## bilibili

| Purpose | Endpoint | anon | Notes |
|---|---|---|---|
| short link expand | `GET https://b23.tv/<code>` (do **not** follow) | yes | the 3xx `Location` carries `up_id`, `share_source`, `share_plat`; the landing page is CORS/risk-guarded |
| video meta | `api.bilibili.com/x/web-interface/view?bvid=` | yes | title, owner, pubdate, duration, dimension, pic, aid, cid, `argue_info` |
| uploader profile | `api.bilibili.com/x/web-interface/card?mid=` | yes | fans, attention, `archive_count`, `like_num`, `sign` |
| uploader's full list | `api.bilibili.com/x/series/recArchivesByKeywords?mid=&ps=50` | yes | paginated by `pn`; works even when `x/space/arc/search` is risk-blocked |
| search, newest first | `api.bilibili.com/x/web-interface/search/type?search_type=video&keyword=&page=1&order=pubdate` | **yes, but** | needs a primed cookie session (see below); `order=pubdate` is the whole trick |
| comments | `api.bilibili.com/x/v2/reply/main?type=1&oid=<aid>&mode=3&next=0` | **capped** | `oid=aid`, not bvid. Bare call returns **3 replies regardless of `all_count`** — measured 3/38, 3/5, 3/3. Pagination cannot move past it (`is_end=true` immediately, `next` repeats). Modes 0/2/3, `plat` 1/2, `nohot` 0/1, and a correctly wbi-signed `x/v2/reply/wbi/main` all yield the same 3. |
| comments, **app transport** | `…/x/v2/reply/main?…&mobi_app=android&platform=android` | **best anon** | **This is the unlock.** Same endpoint plus two plain query flags returns **19** replies with `is_end=false` and a working `next`. Paging `next=2` adds 6 more, then stalls at **25/38** while `all_count` stays 38. Roughly 8× the bare call, no session needed. Remaining replies need a login. |
| comments, full | `api.bilibili.com/x/v2/reply/wbi/main` | **login needed** | requires wbi signature (`_common.bili_wbi_json`) *and* a session; signature alone still returns 3 |
| playurl | `api.bilibili.com/x/player/playurl?bvid=&cid=&qn=64&fnval=1` | yes | `qn=32` when the account tier does not allow 720p+ |
| tag list | `api.bilibili.com/x/tag/archive/tags?bvid=` | yes | useful corpus for keyword expansion |

### The 412 trap

`search/type` answers **412 Precondition Failed** to a cold client even with a
browser UA. The fix is not a cookie file — it is simply visiting
`https://www.bilibili.com/` once through the same cookie jar, which plants
`buvid3` / `b_nut`. After that the identical request returns `code 0`.
`_common.bili_json()` does this priming automatically and backs off 6 s → 12 s
→ 24 s, resetting the prime each retry.

Search also throttles hard: keep ≥ 9 s between search calls, ≥ 2 s for `view`.

## YouTube

| Purpose | Endpoint | anon | Notes |
|---|---|---|---|
| authoritative title + channel | `youtube.com/oembed?url=...&format=json` | yes | **the most reliable field source**; never login-gated |
| channel id from handle | `youtube.com/@<handle>/videos` → regex `channelId` | yes | page is ~1.3 MB; the `videos` tab embeds it, `about` also works |
| exact upload dates | `youtube.com/feeds/videos.xml?channel_id=` | yes | **only the 15 newest**; older videos are absent, so absence ≠ nonexistence |
| thumbnails | `i.ytimg.com/vi/<id>/{maxres,sd,hq}default.jpg` | yes | also `/hq1.jpg` `/maxres3.jpg` — auto storyboard frames, no auth |
| watch page fields | `youtube.com/watch?v=` raw HTML | yes | regex `dateText`, `viewCount.simpleText`, `channelId` |
| search | `yt-dlp --flat-playlist -J "ytsearch40:<q>"` | yes | gives id/title/channel/duration in bulk; the workhorse |
| stream / playurl | Innertube `player` | **no** | `playabilityStatus: LOGIN_REQUIRED` on age-gated or bot-flagged videos |
| storyboards | `i.ytimg.com/sb/<id>/storyboard3_L2/M0.jpg` | **no** | 404 without a session; `storyboards` absent from the watch page too |
| captions | `--write-subs` | **no** | needs session for most videos |

### yt-dlp client matrix (measured)

`--extractor-args "youtube:player_client=<c>"` for `c` in
`web, mweb, web_safari, ios, android, android_vr, android_music, ios_music,
tv, tv_simply, tv_embedded, web_embedded, web_creator, web_music` — **all** of
them returned either "Sign in to confirm you're not a bot" or "Video
unavailable" on the age-gated fixture. Trying more clients is not a bypass;
it only confirms the block. Escalate to cookies, or accept that `content_match`
stays `not_checked` and report the dimensions you do hold.

Mirrors (`pipedapi.*`, `invidious.*`, `cobalt`) were all dead/403 at test
time. `yt.lemnoslife.com` times out. Do not build a plan around them.

## Everything else

| Platform | Anonymous entry point | Note |
|---|---|---|
| douyin | `douyin.com/video/<19-digit id>` | search page returns a fixed 72 KB shell with no results; needs session |
| xiaohongshu | `xhslink.com/<code>` expand | note ids are 24-hex |
| telegram public channels | `t.me/s/<channel>?q=<kw>` | **fully anonymous**, returns rendered HTML; excellent for ASMR/搬运 corpora |
| nicovideo | `nicovideo.jp/watch/<id>` | metadata via `https://ext.nicovideo.jp/api/getthumbinfo/<id>` |
| reverse image | `lens.google.com/uploadbyurl?url=` | 200 anonymously |
| reverse image | `yandex.com/images/search?rpt=imageview&url=` | 200 anonymously, better recall on Asian thumbnails |
| reverse image | `saucenao.com` | 403 without a key — treat as unavailable |

## Rate-limit budget

| Host | gap | symptom when exceeded |
|---|---|---|
| `api.bilibili.com` search | 9 s | 412 |
| `api.bilibili.com` view | 2 s | `-799 请求过于频繁` |
| `api.bilibili.com` space | 3 s | `-352 风控校验失败` |
| `www.youtube.com` | 2 s | occasional 429; HTML still parseable |
| `i.ytimg.com` | 0.3 s | none observed; safe to parallelize 10× |
