"""Reference-by-link: the share hosts, and what must happen when one lies to us."""
import http.server
import threading
from pathlib import Path

import pytest

from mediarescue import cli, fetch

MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
HTML_BYTES = b"<!DOCTYPE html><html><body>please sign in</body></html>"


@pytest.fixture
def server():
    """A local HTTP server, so the download path is exercised without touching the internet."""
    routes: dict[str, tuple[str, bytes]] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            content_type, payload = routes.get(self.path.split("?")[0], ("text/html", HTML_BYTES))
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):                        # keep pytest output readable
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, routes
    httpd.shutdown()


def test_a_media_file_is_recognised_and_a_web_page_is_not(tmp_path):
    clip = tmp_path / "a.mp4"
    clip.write_bytes(MP4_BYTES)
    page = tmp_path / "b.mp4"
    page.write_bytes(HTML_BYTES)
    riff = tmp_path / "c.avi"
    riff.write_bytes(b"RIFF\x10\x00\x00\x00AVI LIST")

    assert fetch.looks_like_media(clip)
    assert fetch.looks_like_media(riff)
    assert not fetch.looks_like_media(page)


def test_terabox_share_ids_are_understood():
    assert fetch.terabox_shorturl("https://terabox.com/s/1AbCdEfGhIj") == "1AbCdEfGhIj"
    assert fetch.terabox_shorturl("https://www.1024terabox.com/sharing/1AbCdEf") == "1AbCdEf"
    assert fetch.terabox_shorturl("https://teraboxapp.com/s/1XyZ123?pwd=abcd") == "1XyZ123"
    with pytest.raises(fetch.FetchError, match="share link"):
        fetch.terabox_shorturl("https://terabox.com/")


def test_terabox_picks_the_largest_video_and_carries_the_referer():
    payload = {"errno": 0, "list": [
        {"server_filename": "readme.txt", "size": 10, "isdir": 0},
        {"server_filename": "small.mp4", "size": 100, "isdir": 0,
         "dlink": "https://cdn/small", "fs_id": 5},
        {"server_filename": "big.mp4", "size": 900, "isdir": 0,
         "dlink": "https://terabox.com/dl?fid=7", "fs_id": 7},
    ]}
    resolved = fetch.parse_terabox_list(payload, "1AbC", page="https://terabox.com/s/1AbC")
    assert resolved.name == "big.mp4"
    assert resolved.sources[0].url == "https://terabox.com/dl?fid=7"
    assert resolved.sources[0].headers["Referer"] == "https://terabox.com/s/1AbC"
    assert "big.mp4" in resolved.note


def test_a_terabox_folder_without_media_is_refused():
    with pytest.raises(fetch.FetchError, match="no video or audio"):
        fetch.parse_terabox_list({"errno": 0, "list": [
            {"server_filename": "notes.txt", "size": 1, "isdir": 0, "dlink": "x"}]}, "1AbC")


def test_terabox_login_walls_say_what_to_do():
    assert "MEDIARESCUE_TERABOX_COOKIE" in fetch.terabox_errno(4001301)
    assert "removed" in fetch.terabox_errno(2)


def test_a_terabox_link_resolves_through_the_share_api(monkeypatch):
    answers = [
        {"errno": 0, "shareid": 1},
        {"errno": 0, "list": [{"server_filename": "clip.mp4", "size": 2048, "isdir": 0,
                               "dlink": "https://terabox.com/dl/1", "fs_id": 11}]},
    ]
    monkeypatch.setattr(fetch, "_get_json", lambda *a, **k: answers.pop(0))
    monkeypatch.setenv("MEDIARESCUE_TERABOX_COOKIE", "ndus=abc")
    resolved = fetch.resolve("https://terabox.com/s/1AbCdEfGhIj")
    assert resolved.name == "clip.mp4"
    assert resolved.sources[0].headers["Cookie"] == "ndus=abc"


def test_google_drive_dropbox_onedrive_and_pixeldrain_become_direct_links():
    drive = fetch.resolve("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing")
    assert "drive.usercontent.google.com" in drive.sources[0].url
    assert "1AbCdEfGhIjKlMnOp" in drive.sources[0].url

    drop = fetch.resolve("https://www.dropbox.com/s/abc/clip.mp4?dl=0")
    assert "dl=1" in drop.sources[0].url

    one = fetch.resolve("https://1drv.ms/u/s!AbCdEf")
    assert "download=1" in one.sources[0].url

    pix = fetch.resolve("https://pixeldrain.com/u/AbC123")
    assert pix.sources[0].url == "https://pixeldrain.com/api/file/AbC123?download"


def test_mediafire_download_button_is_scraped():
    html = ('<a class="input popsok" id="downloadButton" '
            'href="https://download2392.mediafire.com/abc/clip.mp4">Download</a>')
    resolved = fetch.parse_mediafire_page(html)
    assert resolved.sources[0].url.startswith("https://download2392.mediafire.com/")


def test_gofile_contents_pick_the_largest_file():
    payload = {"data": {"children": {
        "a": {"type": "file", "name": "notes.txt", "size": 3, "link": "x"},
        "b": {"type": "file", "name": "small.mp4", "size": 10, "link": "https://store/small"},
        "c": {"type": "file", "name": "big.mkv", "size": 99, "link": "https://store/big"},
    }}}
    resolved = fetch.parse_gofile_contents(payload, token="tok")
    assert resolved.name == "big.mkv"
    assert resolved.sources[0].url == "https://store/big"
    assert resolved.sources[0].headers["Cookie"] == "accountToken=tok"


def test_mega_is_refused_with_an_explanation():
    with pytest.raises(fetch.FetchError, match="browser"):
        fetch.resolve("https://mega.nz/file/AbC123#key")


def test_a_downloaded_file_lands_on_disk(server, tmp_path):
    base, routes = server
    routes["/clip.mp4"] = ("video/mp4", MP4_BYTES)
    path = fetch.download(f"{base}/clip.mp4", tmp_path / "ref")
    assert path.read_bytes() == MP4_BYTES
    assert path.name == "reference.mp4"


def test_a_share_page_that_returns_html_is_reported_not_saved(server, tmp_path):
    base, routes = server
    routes["/page.mp4"] = ("text/html", HTML_BYTES)
    with pytest.raises(fetch.FetchError, match="web page"):
        fetch.download(f"{base}/page.mp4", tmp_path / "ref")
    assert not list((tmp_path / "ref").glob("*"))


def test_the_cli_fetches_a_link_reference_uses_it_and_deletes_it(monkeypatch, tmp_path):
    clip = tmp_path / "broken.mp4"
    clip.write_bytes(b"not really a video")
    seen = {}

    def fake_download(url, dest_dir, name_hint="reference.mp4", report=None):
        target = Path(dest_dir) / "reference.mp4"
        target.write_bytes(MP4_BYTES)
        seen["url"] = url
        return target

    def fake_repair(src, out, args, d):
        seen["reference"] = args.reference
        seen["exists_during_repair"] = Path(args.reference).exists()
        raise SystemExit(0)

    monkeypatch.setattr(fetch, "download", fake_download)
    monkeypatch.setattr(cli, "_repair", fake_repair)
    monkeypatch.setattr(cli, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: type(
        "D", (), {"severity": cli.Severity.MISSING_ATOMS, "needs_reference": True,
                  "notes": ["index gone"]})())

    with pytest.raises(SystemExit):
        cli.main(["fix", str(clip), "--reference", "https://terabox.com/s/1AbCdEfGhIj"])

    assert seen["url"] == "https://terabox.com/s/1AbCdEfGhIj"
    assert Path(seen["reference"]).exists() is False       # deleted after the run
    assert seen["exists_during_repair"] is True


def test_an_unusable_link_stops_before_any_repair(monkeypatch, tmp_path, capsys):
    clip = tmp_path / "broken.mp4"
    clip.write_bytes(b"not really a video")

    def fail(url, dest_dir, name_hint="reference.mp4", report=None):
        raise fetch.FetchError("that TeraBox share holds no video or audio file")

    monkeypatch.setattr(fetch, "download", fail)
    code = cli.main(["fix", str(clip), "--reference", "https://terabox.com/s/1AbCdEfGhIj"])
    assert code == 2
    assert "cannot use that link" in capsys.readouterr().err
