"""Find the original of a damaged file online and use it as the reference.

A file that lost its index can only be rebuilt from something encoded the *same* way, and the
most reliable such thing is the file itself, still sitting where it was downloaded from. So this
module asks the only question that matters: what was this file called, and where does that name
still live?

    identify    -> title from the filename and whatever metadata survived
    search      -> web results for that title (Tavily if TAVILY_API_KEY is set, else DuckDuckGo)
    harvest     -> direct media links and share links (TeraBox, MediaFire, ...) off those pages
    try         -> download each candidate, keep the first that probes as a plausible twin

Honesty about the odds: a re-encoded copy of the same video will *not* work as a reference --
different encoder, different SPS/PPS -- and the repair verification will reject it. What works is
the same upload found again. The tool therefore prefers the file with a matching name and size
and never claims success without a clean decode.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import fetch

TIMEOUT = 20
# A HEAD request sieves these for a few KB each, so keeping the list long is cheap and it
# is what lets the real twin survive behind a wall of 10-second demo clips.
MAX_CANDIDATES = 24
SEARCH_LIMIT = 10

# Filenames harvested from download sites are dressed up: site names, handles, resolutions,
# "[1080p]", "EPORNER", "Telegram @chan", "x265". A reference is found by the words left over.
NOISE_WORDS = {
    "telegram", "official", "download", "downloaded", "full", "hd", "fhd", "uhd", "sd",
    "mp4", "mkv", "avi", "mov", "webm", "x264", "x265", "h264", "h265", "aac", "hevc",
    "1080p", "720p", "480p", "360p", "2160p", "4k", "8k", "60fps", "30fps",
    "eporner", "pornhub", "xvideos", "xhamster", "spankbang", "mixdrop", "doodstream",
    "streamtape", "vidhide", "vidsrc", "upstream", "streamwish", "filemoon", "lulustream",
    "javmix", "javgg", "missav", "supjav", "bokep", "viral", "indo", "indonesia", "terbaru",
    "new", "latest", "uncensored", "leaked", "premium", "exclusive", "eksklusif", "part",
}
BRACKETED = re.compile(r"[\[\(\{][^\]\)\}]{1,24}[\]\)\}]")
HANDLE = re.compile(r"@[\w.]+")
DOTTED = re.compile(r"^[\w-]+\.(?:com|net|org|xyz|cc|to|io|me|tv|app|link|club|top|vip)$", re.IGNORECASE)
# En and em dashes are separators here, not confusable prose characters: this regex is about
# punctuation between title words, so RUF001's "did you mean a hyphen" is the wrong question.
SEPARATORS = re.compile(r"[._\-–—|]+")  # noqa: RUF001


@dataclass
class Identity:
    path: Path
    name: str
    title: str
    codec: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0
    size: int = 0
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        bits = [self.title or self.name]
        if self.codec:
            bits.append(self.codec)
        if self.width and self.height:
            bits.append(f"{self.width}x{self.height}")
        if self.duration:
            bits.append(f"{self.duration:.1f}s")
        return " · ".join(bits)


def strip_suffix(name: str) -> str:
    stem = Path(name).stem
    return stem


def clean_title(name: str) -> str:
    """The searchable part of a downloaded filename, minus site dressing."""
    text = strip_suffix(name)
    text = BRACKETED.sub(" ", text)
    text = HANDLE.sub(" ", text)
    text = SEPARATORS.sub(" ", text)
    words = [w for w in text.split() if w and w.lower() not in NOISE_WORDS
             and not DOTTED.match(w) and not any(ch.isdigit() for ch in w)]
    # Words that are pure digits are episode numbers and timestamps; they help nothing here.
    cleaned = " ".join(words).strip()
    return cleaned or strip_suffix(name)


def identify(path: Path, probe=None) -> Identity:
    """Title from the filename; codec/size/duration when the container still answers."""
    path = Path(path)
    identity = Identity(path=path, name=path.name, title=clean_title(path.name),
                        size=path.stat().st_size if path.exists() else 0)
    if path.suffix.lower() not in fetch.MEDIA_SUFFIXES:
        identity.notes.append("not a media file by extension")
    if probe is not None:
        info = probe(path) or {}
        if info.get("ok"):
            identity.codec = (info.get("streams") or [""])[0]
            identity.duration = float(info.get("duration") or 0.0)
            identity.width = int(info.get("width") or 0)
            identity.height = int(info.get("height") or 0)
        else:
            identity.notes.append("container unreadable; going by the filename alone")
    return identity


def build_queries(identity: Identity, limit: int = 3) -> list[str]:
    title = identity.title.strip()
    if not title:
        return []
    queries = [f'"{title}" mp4', f'"{title}"', f'{title} download']
    if identity.width and identity.height:
        queries.insert(1, f'"{title}" {identity.height}p mp4')
    return queries[:limit]


# --------------------------------------------------------------------------- search backends

def parse_ddg(html: str) -> list[str]:
    """DuckDuckGo's HTML and lite endpoints: results are wrapped in /l/?uddg=<encoded url>."""
    urls: list[str] = []
    for match in re.finditer(r'href="//duckduckgo\.com/l/\?uddg=([^"&]+)', html):
        url = urllib.parse.unquote(match.group(1))
        if url.startswith("http"):
            urls.append(url)
    for match in re.finditer(r'href="(https?://[^"]+)"[^>]*class="result__a"', html):
        urls.append(match.group(1))
    for match in re.finditer(r'class="result-link"[^>]*href="(https?://[^"]+)"', html):
        urls.append(match.group(1))
    return urls


def parse_bing(html: str) -> list[str]:
    """Bing hides the real target in `ck/a?...&u=a1<base64url>`; decode it back out."""
    urls: list[str] = []
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.DOTALL):
        raw = re.search(r"[?&]u=a1([A-Za-z0-9_\-%]+)", block)
        if raw:
            value = urllib.parse.unquote(raw.group(1))
            value += "=" * (-len(value) % 4)
            try:
                target = base64.urlsafe_b64decode(value).decode("utf-8", "replace")
            except (ValueError, binascii.Error):
                target = ""
            if target.startswith("http"):
                urls.append(target)
                continue
        direct = re.search(r'href="(https?://(?!www\.bing\.com)[^"]+)"', block)
        if direct:
            urls.append(direct.group(1).replace("&amp;", "&"))
    return urls


def parse_tavily(payload: dict) -> list[str]:
    results = payload.get("results") if isinstance(payload, dict) else None
    urls = []
    for item in results or []:
        url = item.get("url") if isinstance(item, dict) else None
        if isinstance(url, str) and url.startswith("http"):
            urls.append(url)
        # A snippet often quotes the file's own URL; that is the best candidate there is.
        for match in MEDIA_LINK.finditer(str(item.get("content") or "") if isinstance(item, dict)
                                         else ""):
            urls.append(match.group(0))
    return urls


def _search_tavily(query: str, api_key: str) -> list[str]:
    body = json.dumps({"api_key": api_key, "query": query, "max_results": SEARCH_LIMIT,
                       "search_depth": "basic"}).encode()
    request = urllib.request.Request("https://api.tavily.com/search", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return parse_tavily(json.loads(response.read().decode("utf-8", "replace")))
    except (urllib.error.URLError, ValueError) as exc:
        raise fetch.FetchError(f"Tavily search failed: {exc}") from exc


def _search_ddg(query: str) -> list[str]:
    data = urllib.parse.urlencode({"q": query}).encode()
    request = urllib.request.Request("https://html.duckduckgo.com/html/", data=data,
                                     headers=fetch._headers())
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return parse_ddg(response.read(2 << 20).decode("utf-8", "replace"))
    except urllib.error.URLError as exc:
        raise fetch.FetchError(f"DuckDuckGo search failed: {exc}") from exc


def _search_ddg_lite(query: str) -> list[str]:
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=fetch._headers()),
                                    timeout=TIMEOUT) as response:
            return parse_ddg(response.read(2 << 20).decode("utf-8", "replace"))
    except urllib.error.URLError as exc:
        raise fetch.FetchError(f"DuckDuckGo lite search failed: {exc}") from exc


def _search_bing(query: str) -> list[str]:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": "30"})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=fetch._headers()),
                                    timeout=TIMEOUT) as response:
            return parse_bing(response.read(3 << 20).decode("utf-8", "replace"))
    except urllib.error.URLError as exc:
        raise fetch.FetchError(f"Bing search failed: {exc}") from exc


def _tvly_command() -> list[str]:
    """`node <cli.js>` instead of the npm .CMD shim: the shim breaks on usernames with spaces."""
    exe = shutil.which("tvly") or shutil.which("tvly.cmd")
    if not exe:
        raise fetch.FetchError("tvly is not installed")
    shim = Path(exe)
    entry = shim.parent / "node_modules" / "@tavily" / "cli" / "dist" / "cli.js"
    if not entry.exists():
        found = sorted(shim.parent.glob("node_modules/*/*/dist/cli.js"))
        if not found:
            raise fetch.FetchError("tvly is installed but its script was not found")
        entry = found[0]
    node = shutil.which("node") or str(shim.parent / "node.exe")
    return [node, str(entry)]


def _search_tvly(query: str) -> list[str]:
    """The `tvly` CLI, when the user already has it signed in -- no key to paste."""
    command = [*_tvly_command(), "search", query.replace('"', " "), "--json"]
    try:
        finished = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise fetch.FetchError(f"tvly could not run: {exc}") from exc
    if finished.returncode != 0:
        detail = (finished.stderr or finished.stdout or "").strip().splitlines()
        raise fetch.FetchError(f"tvly search failed: {detail[-1][:160] if detail else 'no output'}")
    try:
        return parse_tavily(json.loads(finished.stdout))
    except ValueError as exc:
        raise fetch.FetchError(f"tvly returned something that is not JSON: {exc}") from exc


def _search_tavily_or_skip(query: str) -> list[str]:
    """The Tavily HTTP API, when a key is configured; otherwise stand aside for the next backend."""
    key = (os.environ.get("TAVILY_API_KEY") or os.environ.get("MEDIARESCUE_TAVILY_KEY") or "").strip()
    if not key:
        raise fetch.FetchError("no TAVILY_API_KEY set")
    return _search_tavily(query, key)


BACKENDS = (
    ("Tavily API", _search_tavily_or_skip),
    ("tvly CLI", _search_tvly),
    ("DuckDuckGo", _search_ddg),
    ("DuckDuckGo lite", _search_ddg_lite),
    ("Bing", _search_bing),
)


JUNK_RESULT = re.compile(r"\.(?:css|js|png|jpe?g|gif|svg|ico|woff2?|json|xml)(?:\?|$)", re.IGNORECASE)
ENGINE_HOSTS = ("bing.com", "google.", "duckduckgo.com", "microsoft.com", "yahoo.com")


def _host(url: str) -> str:
    """Pages carry junk like `//[www.terabox.com` that urlsplit refuses outright; skip those."""
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def usable_result(url: str) -> bool:
    """Search engines return their own stylesheets and ad redirects; none of those is a source."""
    host = _host(url)
    if not host or any(host.endswith(h) or h in host for h in ENGINE_HOSTS):
        return False
    return not JUNK_RESULT.search(urllib.parse.urlsplit(url).path or url)


def search(query: str) -> list[str]:
    """Web results for `query`, from the first backend that answers usefully."""
    trouble: list[str] = []
    for name, backend in BACKENDS:
        try:
            urls = [u for u in backend(query) if usable_result(u)]
        except fetch.FetchError as exc:
            trouble.append(f"{name}: {exc}")
            continue
        if urls:
            return urls
        trouble.append(f"{name}: no usable results")
    raise fetch.FetchError("no search engine answered (" + "; ".join(trouble[:3]) + ")")


# --------------------------------------------------------------------------- harvesting

MEDIA_LINK = re.compile(r'https?://[^\s"\'<>]+?\.(?:mp4|mkv|m4v|webm|mov)(?:\?[^\s"\'<>]*)?', re.IGNORECASE)
SHARE_LINK = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)
RELATIVE_MEDIA = re.compile(
    r'(?:src|href)=["\']((?!https?:|//)[^"\']+?\.(?:mp4|mkv|m4v|webm|mov)(?:\?[^"\']*)?)["\']', re.IGNORECASE)


def extract_media_links(html: str, base: str = "") -> list[str]:
    """Direct media files on a page, plus links to hosts `fetch` knows how to resolve."""
    found: list[str] = []
    for match in MEDIA_LINK.finditer(html):
        found.append(_absolute(match.group(0), base))
    # Pages usually write the player source as a relative path; same file, no scheme.
    for match in RELATIVE_MEDIA.finditer(html):
        found.append(_absolute(match.group(1), base))
    for match in SHARE_LINK.finditer(html):
        url = match.group(0).rstrip('",)')
        host = _host(url)
        if not host:
            continue
        if any(h in host for h in fetch.TERABOX_HOSTS) or any(
                h in host for h in ("mediafire.com", "pixeldrain.com", "gofile.io",
                                    "drive.google.com", "dropbox.com", "1drv.ms")):
            found.append(url)
    return found


def _absolute(url: str, base: str) -> str:
    if not base or url.startswith("http"):
        return url
    return urllib.parse.urljoin(base, url)


def normalise(url: str) -> str:
    """github.com/<u>/<r>/blob/<path> serves a page; raw.githubusercontent.com serves the file."""
    match = re.match(r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/blob/(.+)", url)
    if match:
        return f"https://raw.githubusercontent.com/{match.group(1)}/{match.group(2)}/{match.group(3)}"
    return url


def rank(candidates: list[str], identity: Identity) -> list[str]:
    """Same-word-in-the-URL beats everything else; shares beat pages; duplicates removed."""
    words = {w.lower() for w in identity.title.split() if len(w) > 2}
    # "Big Buck Bunny" appears as BigBuckBunny720p30s.mp4 often enough that comparing the squashed
    # forms is what finds the twin rather than a differently named clip of the same show.
    squash = re.sub(r"[^a-z0-9]", "", identity.title.lower())

    def score(url: str) -> tuple[int, int]:
        lowered = url.lower()
        name = re.sub(r"[^a-z0-9]", "", urllib.parse.urlsplit(lowered).path.rsplit("/", 1)[-1])
        hits = sum(1 for w in words if w in lowered)
        title_in_name = 3 if squash and squash[:12] in name else 0
        is_share = 1 if any(h in lowered for h in (*fetch.TERABOX_HOSTS,
            "mediafire.com", "pixeldrain.com", "gofile.io")) else 0
        return (hits + title_in_name + is_share, -len(url))

    seen: set[str] = set()
    ordered = []
    for url in sorted(candidates, key=score, reverse=True):
        key = url.split("?")[0]
        if key not in seen:
            seen.add(key)
            ordered.append(url)
    return ordered


def harvest(identity: Identity, max_candidates: int = MAX_CANDIDATES, report=None) -> list[str]:
    """Every plausible URL for the original, best first."""
    pool: list[str] = []
    for query in build_queries(identity):
        if report:
            report(f"searching: {query}")
        try:
            results = search(query)
        except fetch.FetchError as exc:
            if report:
                report(f"search failed: {exc}")
            continue
        for page in results[:SEARCH_LIMIT]:
            if MEDIA_LINK.fullmatch(page) or any(h in page.lower() for h in fetch.TERABOX_HOSTS):
                pool.append(page)
                continue
            try:
                html = fetch._get_text(page)
            except fetch.FetchError:
                continue
            pool.extend(extract_media_links(html, base=page))
        if len(pool) >= max_candidates:
            break
    return rank([normalise(u) for u in pool], identity)[:max_candidates]


def plausible(candidate: Path, identity: Identity, probe=None) -> tuple[bool, str]:
    """Is this download worth trying as the twin of the damaged file?"""
    if not candidate.exists() or candidate.stat().st_size == 0:
        return False, "empty download"
    if not fetch.looks_like_media(candidate):
        return False, "not a media file"
    # The damaged file still knows how big it is. A twin is the same upload, so its size lands
    # within a few percent; a different cut of the same title does not. Without this check the
    # first random result gets grafted and only the decode counter notices.
    if identity.size:
        ratio = candidate.stat().st_size / identity.size
        if not 0.75 <= ratio <= 1.35:
            return False, (f"size differs ({fetch.describe(candidate.stat().st_size)} vs "
                           f"{fetch.describe(identity.size)} on disk)")
    if probe is None:
        return True, "media file"
    info = probe(candidate) or {}
    if not info.get("ok"):
        return False, "downloaded file does not probe"
    if identity.duration and info.get("duration"):
        allowed = max(2.0, identity.duration * 0.02)
        if abs(float(info["duration"]) - identity.duration) > allowed:
            return False, (f"duration differs ({info['duration']:.1f}s vs "
                           f"{identity.duration:.1f}s)")
    if identity.height and info.get("height") and int(info["height"]) != identity.height:
        return False, f"resolution differs ({info.get('height')}p vs {identity.height}p)"
    return True, "matches size/shape of the damaged file"
