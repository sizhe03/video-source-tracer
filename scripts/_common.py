"""Shared helpers for video-source-tracer scripts.

Stdlib only (urllib) — this environment has no `requests` and no `PIL`.
All scripts print one JSON object to stdout and human notes to stderr.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)

# --- polite pacing -----------------------------------------------------------
# bilibili returns 412 "Precondition Failed" when the search endpoint is hit
# faster than roughly one request per few seconds from one IP.
DEFAULT_GAP = float(os.environ.get("TRACE_GAP", "6.0"))
_last_hit: dict[str, float] = {}


def throttle(host: str, gap: float | None = None) -> None:
    """Sleep so that consecutive calls to the same host are >= gap apart."""
    g = DEFAULT_GAP if gap is None else gap
    now = time.monotonic()
    prev = _last_hit.get(host)
    if prev is not None:
        wait = g - (now - prev)
        if wait > 0:
            time.sleep(wait)
    _last_hit[host] = time.monotonic()


# --- HTTP --------------------------------------------------------------------
class Response:
    __slots__ = ("status", "headers", "body", "url")

    def __init__(self, status: int, headers, body: bytes, url: str):
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    @property
    def location(self) -> str | None:
        return self.headers.get("Location") if self.headers else None

    def text(self, enc: str = "utf-8") -> str:
        return self.body.decode(enc, "ignore")

    def json(self):
        return json.loads(self.text())


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None  # never follow; the 3xx response surfaces as HTTPError


_OPENER_NO_REDIRECT = urllib.request.build_opener(_NoRedirect)
_OPENER_FOLLOW = urllib.request.build_opener()


def http(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 25.0,
    follow: bool = True,
    mobile: bool = False,
    data: bytes | None = None,
) -> Response:
    """GET (or POST when `data`) with an explicit redirect policy."""
    hd = {
        "User-Agent": UA_MOBILE if mobile else UA_DESKTOP,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.7",
    }
    if headers:
        hd.update(headers)
    req = urllib.request.Request(url, data=data, headers=hd)
    opener = _OPENER_FOLLOW if follow else _OPENER_NO_REDIRECT
    try:
        with opener.open(req, timeout=timeout) as r:
            return Response(r.status, dict(r.headers), r.read(), r.geturl())
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
        except Exception:
            pass
        return Response(e.code, dict(e.headers or {}), body, url)


def http_json(url: str, **kw) -> dict:
    r = http(url, **kw)
    if r.status != 200:
        raise RuntimeError(f"HTTP {r.status} for {url}")
    return r.json()


# --- bilibili warm-up --------------------------------------------------------
# The search endpoint answers 412 "Precondition Failed" to a cold client.
# Fetching the homepage first plants buvid3/b_nut in a cookie jar, after which
# the same request succeeds anonymously (no account involved).
class _BiliSession:
    """Cookie-jar backed session.

    Anonymous it is enough for metadata; a real session (SESSDATA) is what
    raises the comment page cap above 3. The jar is only ever read from a file
    the caller names explicitly -- nothing is persisted by this module.
    """

    def __init__(self) -> None:
        import http.cookiejar

        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar)
        )
        self.primed = False
        self.cookie_file: str | None = None

    def load_cookies(self, path: str) -> int:
        """Load a Netscape cookies.txt. Returns how many cookies were accepted."""
        import http.cookiejar

        if not os.path.isfile(path):
            die(f"cookie file not found: {path}")
        before = len(list(self.jar))
        try:
            self.jar.load(path, ignore_discard=True, ignore_expires=True)
        except Exception as e:  # noqa: BLE001
            # some exporters write Mozilla format the stdlib rejects; fall back
            try:
                cj = http.cookiejar.MozillaCookieJar(path)
                cj.load(ignore_discard=True, ignore_expires=True)
                for c in cj:
                    self.jar.set_cookie(c)
            except Exception as e2:  # noqa: BLE001
                die(f"cannot parse cookie file ({e}; also {e2})")
        self.cookie_file = path
        self.primed = False
        return len(list(self.jar)) - before

    def has_session(self) -> bool:
        names = {c.name for c in self.jar}
        return bool(names & {"SESSDATA", "bili_jct", "DedeUserID"})

    def prime(self) -> None:
        if self.primed:
            return
        try:
            req = urllib.request.Request(
                "https://www.bilibili.com/", headers={"User-Agent": UA_DESKTOP}
            )
            self.opener.open(req, timeout=25).read()
            self.primed = True
        except Exception as e:  # noqa: BLE001
            note(f"bilibili warm-up failed ({e}); requests may 412")

    def get_json(self, url: str, headers: dict[str, str] | None = None) -> dict:
        self.prime()
        hd = {"User-Agent": UA_DESKTOP, "Referer": "https://www.bilibili.com/"}
        if headers:
            hd.update(headers)
        req = urllib.request.Request(url, headers=hd)
        try:
            with self.opener.open(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code} for {url}") from e


_bili = _BiliSession()

# --- wbi signing -------------------------------------------------------------
# x/v2/reply/wbi/main truncates to a few "hot" replies without a valid
# signature; the legacy x/v2/reply/main caps anonymously at the same point.
# Signing is anonymous-safe: it only needs the public nav keys.
_MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]
_KEYS: dict[str, str] = {}


def _wbi_keys() -> tuple[str, str] | None:
    if _KEYS.get("mixin"):
        return _KEYS["img"], _KEYS["sub"]
    try:
        nav = _bili.get_json("https://api.bilibili.com/x/web-interface/nav")
    except Exception as e:  # noqa: BLE001
        note(f"wbi key fetch failed: {e}")
        return None
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]
    if not img or not sub:
        return None
    _KEYS.update(img=img, sub=sub, mixin="".join((img + sub)[i] for i in _MIXIN_TAB)[:32])
    return img, sub


def wbi_sign(params: dict) -> str:
    """Return the signed query string for a wbi endpoint."""
    import hashlib
    import time as _t
    from urllib.parse import quote, urlencode

    if not _wbi_keys():
        raise RuntimeError("wbi keys unavailable")
    p = dict(params)
    p["wts"] = int(_t.time())
    # values must be stripped of !'()* before signing
    clean = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in p.items()}
    q = urlencode(sorted(clean.items()))
    p["w_rid"] = hashlib.md5((q + _KEYS["mixin"]).encode()).hexdigest()
    return urlencode(sorted(p.items()))


def bili_wbi_json(path: str, params: dict) -> dict:
    """GET a wbi-signed bilibili endpoint through the primed session."""
    qs = wbi_sign(params)
    url = f"https://api.bilibili.com{path}?{qs}"
    return bili_json(url, headers={"Referer": "https://www.bilibili.com/"})


# --- cookie entry points -----------------------------------------------------
def use_cookies(path: str | None) -> int:
    """Optionally attach a cookies.txt. Returns cookies loaded (0 = anonymous)."""
    if not path:
        return 0
    n = _bili.load_cookies(path)
    note(f"loaded {n} cookies from {os.path.basename(path)}")
    if not _bili.has_session():
        note("WARNING: no SESSDATA/bili_jct in the jar - this will still behave "
             "anonymously")
    return n


def session_state() -> dict:
    return {
        "cookie_file": _bili.cookie_file,
        "authenticated": _bili.has_session(),
        "cookies": len(list(_bili.jar)),
    }


def cookie_arg(argv: list[str]) -> str | None:
    """Pull --cookies <path> or TRACE_COOKIES out of argv."""
    if "--cookies" in argv:
        i = argv.index("--cookies")
        if i + 1 < len(argv):
            return argv[i + 1]
    return os.environ.get("TRACE_COOKIES") or None


def strip_opt(argv: list[str], flag: str, arity: int = 1) -> list[str]:
    """Remove a flag (and its value) from argv so positional parsing stays simple."""
    out: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == flag:
            i += 1 + arity
            continue
        out.append(argv[i])
        i += 1
    return out


def bili_json(url: str, headers: dict[str, str] | None = None, retries: int = 2) -> dict:
    """GET a bilibili API endpoint through the primed session.

    Default is **one** retry (6 s then 12 s), matching the SKILL.md policy of
    "one bypass attempt, then ask". An earlier version silently retried three
    times with 6/12/24 s waits, which contradicted the documentation and burned
    a minute on a wall that was not going to open.
    """
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return _bili.get_json(url, headers)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt + 1 >= retries:
                break
            wait = 6.0 * (2**attempt)
            note(f"bilibili request failed ({e}); one retry in {wait:.0f}s "
                 f"then stop and ask the operator")
            time.sleep(wait)
            _bili.primed = False  # re-prime: the buvid may have rotated
    raise RuntimeError(f"bilibili request failed: {last}")


# --- output ------------------------------------------------------------------
def emit(obj: dict) -> None:
    """Print the machine-readable result and exit 0."""
    json.dump(obj, sys.stdout, ensure_ascii=False, indent=1, default=str)
    sys.stdout.write("\n")


def note(msg: str) -> None:
    sys.stderr.write(f"[trace] {msg}\n")
    sys.stderr.flush()


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    sys.stderr.write(f"[trace] ERROR: {msg}\n")
    sys.stderr.flush()
    raise SystemExit(code)


def setup_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[attr-defined]
        except Exception:
            pass


# --- ffmpeg ------------------------------------------------------------------
_FFMPEG_CANDIDATES = (
    r"D:\FormatFactory\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
    r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
)


def ffmpeg() -> str:
    """Locate ffmpeg: PATH first, then a few known local installs."""
    import shutil

    env = os.environ.get("TRACE_FFMPEG")
    if env and os.path.isfile(env):
        return env
    hit = shutil.which("ffmpeg")
    if hit:
        return hit
    for cand in _FFMPEG_CANDIDATES:
        if os.path.isfile(cand):
            return cand
    die("ffmpeg not found; set TRACE_FFMPEG or add it to PATH")
    return "ffmpeg"  # unreachable


# --- small text utils --------------------------------------------------------
_NORM_RE = re.compile(r"[\s_\-/|:：,，。、!！?？♡♥❤️\[\]()（）【】「」『』#]+")


def norm_title(s: str) -> str:
    s = s or ""
    s = re.sub(r"<[^>]+>", "", s)
    return _NORM_RE.sub(" ", s).strip().lower()


_CJK = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_LATIN_RUN = re.compile(r"[a-z0-9]+")


def segments(s: str) -> list[tuple[str, str]]:
    """Split a normalised string into ('cjk'|'latin', text) runs.

    Runs never span a space or a punctuation mark, which is what stops
    `ASMR 助眠` from producing the pseudo-term `asmr助`: bigrams are built
    inside a run, never across two.
    """
    s = norm_title(s)
    out: list[tuple[str, str]] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if _CJK.match(ch):
            j = i
            while j < n and _CJK.match(s[j]):
                j += 1
            out.append(("cjk", s[i:j]))
            i = j
        elif ch.isalnum():
            j = i
            while j < n and s[j].isalnum() and not _CJK.match(s[j]):
                j += 1
            out.append(("latin", s[i:j]))
            i = j
        else:
            i += 1
    return out


def tokens(s: str) -> list[str]:
    """Tokenize for Jaccard: CJK -> unigrams + in-run bigrams, latin -> words.

    Bigrams are generated WITHIN a CJK run only. The previous version built them
    across the whole token list, so `ASMR 助眠` yielded `asmr助` -- a term that
    exists in no title and that a generic-word filter could not recognise.
    """
    out: list[str] = []
    for kind, text in segments(s):
        if kind == "cjk":
            out.extend(text)
            out.extend(text[i:i + 2] for i in range(len(text) - 1))
        else:
            out.append(text)
    return list(dict.fromkeys(out))


def jaccard(a: str, b: str) -> float:
    ta, tb = set(tokens(a)), set(tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def fmt_dur(seconds: int | None) -> str:
    if not seconds:
        return "?"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
