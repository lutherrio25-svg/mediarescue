"""Fetch an intact reference file from a link when the user only has a share URL.

The reference's *content* never matters, only the way it was encoded -- which is why a link is
often all that is available: the intact twin of a damaged download sits in a free cloud drive,
not on this disk.

Free drives do not serve files, they serve pages that call an API, so a link has to be resolved
before it can be downloaded:

  TeraBox (terabox.com and its mirror domains)  share API -> per-file download link
  MediaFire                                     scrape the signed download button
  pixeldrain                                    documented /api/file/<id> endpoint
  GoFile                                        contents API -> direct file link
  Google Drive / Dropbox / OneDrive             rewrite the share URL to the direct endpoint
  Mega                                          refused: links are decrypted in the browser

Everything here is standard library. A resolver returns candidate URLs *with* the headers that
host insists on (TeraBox and MediaFire check Referer, GoFile needs a guest token), and the
parsing half of each resolver is a pure function so it can be tested without the network.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

MAX_BYTES = 4 << 30          # a reference is a video, not a disk image; refuse runaway downloads
CHUNK = 1 << 20
TIMEOUT = 30
# Share hosts serve a page to scripts and a file to browsers; the UA alone often decides which.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")
MEDIA_SUFFIXES = (".mp4", ".mkv", ".avi", ".mov", ".m4v", ".webm", ".flv", ".ts", ".wmv",
                  ".mpg", ".mpeg", ".3gp", ".mp3", ".m4a", ".wav", ".flac", ".ogg")

TERABOX_HOSTS = ("terabox.com", "1024terabox.com", "teraboxapp.com", "terabox.app",
                 "teraboxshare.com", "terasharefile.com", "terafileshare.com",
                 "terasharelink.com", "4funbox.com", "mirrobox.com", "nephobox.com",
                 "momerybox.com", "teraboxlink.com", "freeterabox.com", "terabox.club")


class FetchError(RuntimeError):
    """A link that looked usable but did not produce a media file."""


@dataclass(frozen=True)
class Source:
    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Resolved:
    sources: list[Source]
    name: str = ""          # the remote filename, when the resolver knows it
    note: str = ""          # what was picked out of a folder, for the human


def is_url(text: str) -> bool:
    lowered = (text or "").strip().lower()
    return lowered.startswith(("http://", "https://"))


def describe(size: int) -> str:
    if size >= 1 << 30:
        return f"{size / (1 << 30):.1f} GB"
    if size >= 1 << 20:
        return f"{size / (1 << 20):.1f} MB"
    return f"{size / 1024:.0f} KB"


def looks_like_media(path: Path) -> bool:
    """Enough of a sniff to tell a video from the HTML page that wraps it.

    A share page comes back as text/html with a 200 status, so the first bytes are the only
    honest check: ISO-BMFF, RIFF, EBML, Ogg, FLAC, MP3/AAC, FLV.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(16)
    except OSError:
        return False
    if len(head) < 12:
        return False
    if head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip"):
        return True
    if head[:4] == b"RIFF" and head[8:12] in (b"AVI ", b"WAVE"):
        return True
    if head[:4] in (b"\x1aE\xdf\xa3", b"OggS", b"fLaC", b"FLV\x01"):
        return True
    if head[:3] == b"ID3":
        return True
    return head[0] == 0xFF and (head[1] & 0xE0) == 0xE0      # MPEG audio / ADTS


# --------------------------------------------------------------------------- http helpers

def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    base = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
    base.update(extra or {})
    return base


def _open(url: str, headers: dict[str, str] | None = None, data: bytes | None = None):
    request = urllib.request.Request(url, data=data, headers=_headers(headers))
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def _get_text(url: str, headers: dict[str, str] | None = None) -> str:
    try:
        with _open(url, headers) as response:
            return response.read(8 << 20).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{urllib.parse.urlsplit(url).netloc} answered HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"could not reach {urllib.parse.urlsplit(url).netloc}: "
                         f"{exc.reason}") from exc


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    text = _get_text(url, headers)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise FetchError("that page did not return the data a share link should — it may have "
                         "shown a captcha or a login wall; try the direct download link") from exc


# --------------------------------------------------------------------------- TeraBox

def terabox_shorturl(url: str) -> str:
    # `sharing` must be tried before `s`, or "/sharing/1AbC" matches the bare `s` and yields "haring".
    match = re.search(r"/(?:sharing|s)/([\w-]{6,})", url)
    if match:
        return match.group(1).rstrip("/")
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    for key in ("surl", "shorturl"):
        if query.get(key):
            return query[key][0]
    raise FetchError("that does not look like a TeraBox share link (expected .../s/<id>)")


def terabox_cookie() -> str:
    return os.environ.get("MEDIARESCUE_TERABOX_COOKIE", "").strip()


def terabox_errno(errno) -> str:
    if str(errno) in ("2", "-9", "9"):
        return f"TeraBox says this share does not exist or was removed (errno {errno})"
    if str(errno) == "105":
        return ("TeraBox says the share link is invalid or expired (errno 105). Share links stop "
                "working when the owner deletes the file or the link is replaced — open it in a "
                "browser to confirm it still lists the file")
    if str(errno) in ("4001301", "4001302", "-6"):
        return ("TeraBox wants a signed-in session for this share. Open the link in your browser, "
                "sign in, then copy the Cookie header into MEDIARESCUE_TERABOX_COOKIE and run "
                "again")
    return (f"TeraBox refused the request (errno {errno}). If the link opens in your browser, "
            f"copy that page's Cookie header into MEDIARESCUE_TERABOX_COOKIE")


def parse_terabox_list(payload: dict, shorturl: str, cookie: str = "",
                       page: str = "https://www.terabox.com/") -> Resolved:
    """Pick the best media file out of a share listing.

    A share is usually a folder, and only one file has to be an intact video of the same encoder.
    Largest media file wins: it is the one a reference is most likely to match, and the user does
    not have to know which file to name.
    """
    entries = payload.get("list") or []
    media = [e for e in entries
             if not e.get("isdir")
             and str(e.get("server_filename", "")).lower().endswith(MEDIA_SUFFIXES)]
    if not media:
        raise FetchError("that TeraBox share holds no video or audio file")
    best = max(media, key=lambda e: int(e.get("size") or 0))
    name = best.get("server_filename") or "reference.mp4"
    headers = {"Referer": page}
    if cookie:
        headers["Cookie"] = cookie
    sources: list[Source] = []
    if best.get("dlink"):
        sources.append(Source(best["dlink"], headers))
    if best.get("fs_id"):
        sources.append(Source("https://www.terabox.com/api/download?app_id=250528&web=1"
                              f"&channel=dubox&clienttype=0&fs_id={best['fs_id']}"
                              f"&shorturl={shorturl}", headers))
    if not sources:
        raise FetchError("TeraBox listed the file but offered no download link for it")
    return Resolved(sources, name=name,
                    note=f"picked {name} ({describe(int(best.get('size') or 0))}) from the share")


def resolve_terabox(url: str) -> Resolved:
    """Public share API: shorturlinfo names the share, share/list yields per-file download links."""
    shorturl = terabox_shorturl(url)
    cookie = terabox_cookie()
    page = f"https://www.terabox.com/s/{shorturl}"
    headers = {"Referer": page}
    if cookie:
        headers["Cookie"] = cookie
    info = _get_json("https://www.terabox.com/api/shorturlinfo?app_id=250528&web=1&channel=dubox"
                     f"&clienttype=0&shorturl={shorturl}&root=1", headers)
    if str(info.get("errno", 0)) not in ("0", "None"):
        raise FetchError(terabox_errno(info.get("errno")))
    listing = _get_json("https://www.terabox.com/share/list?app_id=250528&web=1&channel=dubox"
                        f"&clienttype=0&page=1&num=200&dir=%2F&order=time&desc=1&showempty=0"
                        f"&shorturl={shorturl}&root=1", headers)
    if str(listing.get("errno", 0)) not in ("0", "None"):
        raise FetchError(terabox_errno(listing.get("errno")))
    return parse_terabox_list(listing, shorturl, cookie=cookie, page=page)


# --------------------------------------------------------------------------- other drives

def resolve_google_drive(url: str) -> Resolved:
    match = (re.search(r"/file/d/([\w-]{10,})", url) or re.search(r"[?&]id=([\w-]{10,})", url))
    if not match:
        raise FetchError("that Google Drive link carries no file id; open the file and use "
                         "\"Share → copy link\"")
    file_id = match.group(1)
    return Resolved([Source("https://drive.usercontent.google.com/download"
                            f"?id={file_id}&export=download&confirm=t"),
                     Source(f"https://drive.google.com/uc?export=download&id={file_id}")])


def _with_query(url: str, **params: str) -> str:
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    query.update({k: [v] for k, v in params.items()})
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                    urllib.parse.urlencode(query, doseq=True), ""))


def resolve_dropbox(url: str) -> Resolved:
    return Resolved([Source(_with_query(url, dl="1"))])


def resolve_onedrive(url: str) -> Resolved:
    return Resolved([Source(_with_query(url, download="1"))])


def parse_mediafire_page(html: str) -> Resolved:
    match = (re.search(r'id="downloadButton"[^>]*href="([^"]+)"', html)
             or re.search(r'href="(https://download[^"]+mediafire[^"]+)"', html))
    if not match:
        raise FetchError("MediaFire showed no download button; the file may be gone or the page "
                         "wants a browser to answer its captcha")
    return Resolved([Source(match.group(1).replace("&amp;", "&"))],
                    note="MediaFire download button found")


def resolve_mediafire(url: str) -> Resolved:
    return parse_mediafire_page(_get_text(url))


def resolve_pixeldrain(url: str) -> Resolved:
    match = re.search(r"pixeldrain\.com/(?:u|l)/([\w-]+)", url)
    if not match:
        raise FetchError("that pixeldrain link carries no file id")
    return Resolved([Source(f"https://pixeldrain.com/api/file/{match.group(1)}?download")])


def parse_gofile_contents(payload: dict, token: str = "") -> Resolved:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise FetchError("GoFile returned no contents for that link")
    children = data.get("children") or {}
    files = [c for c in (children.values() if isinstance(children, dict) else children)
             if isinstance(c, dict) and str(c.get("name", "")).lower().endswith(MEDIA_SUFFIXES)]
    if not files:
        raise FetchError("that GoFile link holds no video or audio file")
    best = max(files, key=lambda f: int(f.get("size") or 0))
    if not best.get("link"):
        raise FetchError("GoFile listed the file without a download link")
    headers = {"Cookie": f"accountToken={token}"} if token else {}
    return Resolved([Source(best["link"], headers)], name=best.get("name", ""),
                    note=f"picked {best.get('name')} ({describe(int(best.get('size') or 0))})")


def gofile_token() -> str:
    existing = os.environ.get("MEDIARESCUE_GOFILE_TOKEN", "").strip()
    if existing:
        return existing
    request = urllib.request.Request("https://api.gofile.io/accounts", data=b"{}",
                                     headers=_headers({"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError) as exc:
        raise FetchError(f"GoFile would not issue a guest token: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise FetchError("GoFile would not issue a guest token")
    return token


def resolve_gofile(url: str) -> Resolved:
    match = re.search(r"gofile\.io/d/([\w-]+)", url)
    if not match:
        raise FetchError("that GoFile link carries no content id")
    token = gofile_token()
    payload = _get_json(f"https://api.gofile.io/contents/{match.group(1)}?wt={token}")
    return parse_gofile_contents(payload, token)


# --------------------------------------------------------------------------- dispatch

def resolve(url: str) -> Resolved:
    """Turn any supported share link into downloadable candidates."""
    host = urllib.parse.urlsplit(url).netloc.lower()
    if not host:
        raise FetchError("that is not an http(s) link")
    if any(h in host for h in TERABOX_HOSTS):
        return resolve_terabox(url)
    if "mega.nz" in host or "mega.io" in host:
        raise FetchError(
            "Mega links are decrypted in the browser, so they cannot be fetched from here. "
            "Download the file in your browser, then pass the downloaded file instead of the link.")
    if "drive.google.com" in host or "docs.google.com" in host:
        return resolve_google_drive(url)
    if "dropbox.com" in host:
        return resolve_dropbox(url)
    if "1drv.ms" in host or "onedrive.live.com" in host:
        return resolve_onedrive(url)
    if "mediafire.com" in host:
        return resolve_mediafire(url)
    if "pixeldrain.com" in host:
        return resolve_pixeldrain(url)
    if "gofile.io" in host:
        return resolve_gofile(url)
    return Resolved([Source(url)])


# --------------------------------------------------------------------------- download

def _download_one(source: Source, target: Path, report=None) -> int:
    try:
        with _open(source.url, source.headers) as response:
            declared = response.headers.get("Content-Length")
            total = int(declared) if declared and declared.isdigit() else 0
            if total > MAX_BYTES:
                raise FetchError(f"that link is {describe(total)}; a reference should be an "
                                 f"ordinary video")
            done = 0
            with open(target, "wb") as handle:
                while True:
                    block = response.read(CHUNK)
                    if not block:
                        break
                    done += len(block)
                    if done > MAX_BYTES:
                        raise FetchError("download passed 4 GB; that is not a reference file")
                    handle.write(block)
                    if report:
                        report(done, total)
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{urllib.parse.urlsplit(source.url).netloc} answered HTTP {exc.code}"
                         f" ({exc.reason})") from exc
    except urllib.error.URLError as exc:
        raise FetchError(f"could not reach {urllib.parse.urlsplit(source.url).netloc}: "
                         f"{exc.reason}") from exc
    except TimeoutError as exc:
        raise FetchError(f"{urllib.parse.urlsplit(source.url).netloc} timed out "
                         f"after {TIMEOUT}s") from exc
    return done


def download(url: str, dest_dir: Path, name_hint: str = "reference.mp4", report=None) -> Path:
    """Resolve `url`, download the best candidate into `dest_dir`, return the local file."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    resolved = resolve(url)
    remote = Path(resolved.name) if resolved.name else Path(urllib.parse.urlsplit(url).path)
    suffix = remote.suffix.lower()
    if suffix not in MEDIA_SUFFIXES:
        suffix = ".mp4"
    target = dest_dir / (Path(name_hint).stem + suffix)
    problems: list[str] = []
    for source in resolved.sources:
        try:
            size = _download_one(source, target, report)
        except FetchError as exc:
            problems.append(str(exc))
            continue
        if size == 0:
            problems.append(f"{urllib.parse.urlsplit(source.url).netloc} returned an empty file")
            continue
        if not looks_like_media(target):
            problems.append(
                f"{urllib.parse.urlsplit(source.url).netloc} returned a web page, not a video. "
                f"A share link must be public (\"anyone with the link\") or replaced by the "
                f"direct download URL")
            continue
        return target
    target.unlink(missing_ok=True)
    raise FetchError("; ".join(problems) or f"nothing usable came back from {url}")


def cleanup(path: Path) -> None:
    """Remove a downloaded reference and the folder that held it."""
    try:
        shutil.rmtree(Path(path).parent, ignore_errors=True)
    except OSError:
        pass
