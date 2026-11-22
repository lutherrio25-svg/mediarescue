"""The search half of `mediarescue source` tested without the network."""
from pathlib import Path

from mediarescue import source

DDG_HTML = """
<html><body>
<div class="result"><a rel="nofollow" class="result__a"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage-one&amp;rut=abc">one</a></div>
<div class="result"><a rel="nofollow" class="result__a"
  href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.terabox.com%2Fs%2F1AbCdEfGhIj">two</a></div>
</body></html>
"""

PAGE_HTML = """
<html><body>
<video src="https://cdn.example.net/clips/holiday-trip.mp4" controls></video>
<a href="/relative/mirror.mkv">mirror</a>
<a href="https://www.terabox.com/s/1AbCdEfGhIj">share</a>
<a href="https://www.mediafire.com/file/abc123/file.mkv/file">mf</a>
<a href="https://example.com/about">about</a>
</body></html>
"""


def test_a_downloaded_name_becomes_a_searchable_title():
    assert source.clean_title("[1080p] Telegram @viralofc - Big Waves At Sunset (HD).mp4") == \
        "Big Waves At Sunset"
    assert source.clean_title("Sunset_Surf_4K.mkv") == "Sunset Surf"
    # Nothing survives the stripping? Then the original stem is still better than nothing.
    assert source.clean_title("1080p.mp4") == "1080p"


def test_the_search_query_keeps_the_words_that_identify_the_file(tmp_path):
    path = tmp_path / "Sunset Surf - EPORNER.mp4"
    path.write_bytes(b"\x00" * 32)
    identity = source.identify(path)
    queries = source.build_queries(identity)
    assert queries and '"Sunset Surf"' in queries[0]
    assert all("eporner" not in q.lower() for q in queries)


def test_duckduckgo_redirect_links_are_decoded():
    urls = source.parse_ddg(DDG_HTML)
    assert "https://example.com/page-one" in urls
    assert "https://www.terabox.com/s/1AbCdEfGhIj" in urls
    assert not any("duckduckgo.com" in u for u in urls)


def test_a_page_yields_its_media_and_its_share_links():
    links = source.extract_media_links(PAGE_HTML, base="https://example.com/post/1")
    assert "https://cdn.example.net/clips/holiday-trip.mp4" in links
    assert "https://example.com/relative/mirror.mkv" in links   # relative made absolute
    assert any("terabox.com" in link for link in links)
    assert any("mediafire.com" in link for link in links)
    assert not any(link.endswith("/about") for link in links)   # an ordinary page is not a source


def test_the_url_that_names_the_file_ranks_first():
    identity = source.Identity(path=Path("x.mp4"), name="x.mp4", title="Sunset Surf")
    ranked = source.rank([
        "https://a.example/whatever.mp4",
        "https://b.example/sunset-surf.mp4?token=1",
        "https://b.example/sunset-surf.mp4?token=2",
        "https://www.terabox.com/s/1AbCdEfGhIj",
    ], identity)
    assert ranked[0] == "https://b.example/sunset-surf.mp4?token=1"
    # The same path with a different query is the same file: one entry, not two.
    assert len([u for u in ranked if "sunset-surf.mp4" in u]) == 1
    assert any("terabox" in u for u in ranked)


def test_a_candidate_that_differs_in_duration_is_rejected(tmp_path):
    identity = source.Identity(path=Path("x.mp4"), name="x.mp4", title="Sunset", duration=60.0,
                               height=1080)
    candidate = tmp_path / "candidate.mp4"
    candidate.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1024)

    def probe(path):
        return {"ok": True, "duration": 12.0, "height": 1080}

    ok, why = source.plausible(candidate, identity, probe=probe)
    assert ok is False and "duration differs" in why


def test_a_candidate_matching_in_shape_is_accepted(tmp_path):
    candidate = tmp_path / "candidate.mp4"
    candidate.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 1024)
    identity = source.Identity(path=Path("x.mp4"), name="x.mp4", title="Sunset", duration=60.0,
                               height=1080, size=candidate.stat().st_size)

    ok, why = source.plausible(candidate, identity, probe=lambda p: {"ok": True, "duration": 60.2,
                                                                    "height": 1080})
    assert ok is True and "matches" in why


def test_a_candidate_of_a_different_length_is_rejected_before_grafting(tmp_path):
    # Grafting a 10 minute copy onto a 30 second recording decodes into noise; the size check is
    # what stops that download from ever being attempted.
    identity = source.Identity(path=Path("x.mp4"), name="x.mp4", title="Sunset", size=9_400_000)
    candidate = tmp_path / "candidate.mp4"
    candidate.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)

    ok, why = source.plausible(candidate, identity, probe=lambda p: {"ok": True})
    assert ok is False and "size differs" in why


def test_a_github_blob_link_becomes_the_raw_file():
    # github.com/<u>/<r>/blob/... answers with a page; the file lives on another host.
    assert source.normalise(
        "https://github.com/chintan9/Big-Buck-Bunny/blob/master/BigBuckBunny720p30s.mp4") == \
        "https://raw.githubusercontent.com/chintan9/Big-Buck-Bunny/master/BigBuckBunny720p30s.mp4"
    assert source.normalise("https://example.com/a.mp4") == "https://example.com/a.mp4"


def test_search_engine_furniture_is_not_a_candidate():
    # A Bing result page carries its own stylesheet and ad redirects; neither is a source file.
    assert source.usable_result("https://r.bing.com/rp/abc.css") is False
    assert source.usable_result("https://www.bing.com/search?q=x") is False
    assert source.usable_result("https://example.com/clip.mp4") is True


def test_the_squashed_title_outranks_a_stranger():
    identity = source.Identity(path=Path("x.mp4"), name="x.mp4", title="Big Buck Bunny")
    ranked = source.rank([
        "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/720/Big_Buck_Bunny_720_10s_1MB.mp4",
        "https://raw.githubusercontent.com/u/r/master/BigBuckBunny720p30s.mp4",
        "https://raw.githubusercontent.com/u/r/master/ElephantsDream.mp4",
    ], identity)
    assert "BigBuckBunny720p30s.mp4" in ranked[0]
    assert ranked[-1].endswith("ElephantsDream.mp4")


def test_an_html_download_is_never_a_candidate(tmp_path):
    identity = source.Identity(path=Path("x.mp4"), name="x.mp4", title="Sunset")
    page = tmp_path / "candidate.mp4"
    page.write_bytes(b"<!DOCTYPE html><html><body>please sign in</body></html>")
    ok, why = source.plausible(page, identity, probe=lambda p: {"ok": True})
    assert ok is False and "not a media file" in why


def test_a_download_that_does_not_decode_gives_way_to_the_next_candidate(tmp_path, monkeypatch):
    """Passing the size sieve is not proof. A candidate that grafts into garbage must cost the
    next candidate, not the whole repair."""
    from types import SimpleNamespace

    from mediarescue import cli
    from mediarescue.report import Severity

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00" * 4096)
    out = tmp_path / "clip.repaired.mp4"
    first = tmp_path / "candidate-1.mp4"
    first.write_bytes(b"\x00" * 10)
    second = tmp_path / "candidate-2.mp4"
    second.write_bytes(b"\x00" * 10)

    tried = []

    def fake_repair(path, ref, out_path, args, strategy):
        tried.append(ref.name)
        if ref == first:
            return {"ok": False, "output": str(out_path), "reason": "31865 decode errors"}
        Path(out_path).write_bytes(b"\x00" * 16)
        return {"ok": True, "output": str(out_path)}

    monkeypatch.setattr(cli, "_repair_with_reference", fake_repair)
    monkeypatch.setattr(cli, "_plays_without_errors", lambda path: len(tried) == 2)
    # The probe is stubbed: this test is about which candidate wins the search, and the fake
    # output above is 16 zero bytes. The probe gate itself is covered by tests/test_cli.py.
    monkeypatch.setattr(cli, "probe",
                        lambda p: {"ok": True, "duration": 1.0, "streams": ["h264"]})
    monkeypatch.setattr(cli, "_online_references", lambda path, args: iter([first, second]))
    monkeypatch.setattr("mediarescue.reference.find_reference", lambda path, **kw: None)

    args = SimpleNamespace(reference=None, find_source=True, strategy="auto", quiet=True,
                           no_color=True, verbose=False, timescale=None, fps=30.0, no_verify=True)
    result = cli._repair(src, out, args, SimpleNamespace(severity=Severity.MISSING_ATOMS))

    assert result["ok"] is True
    assert tried == ["candidate-1.mp4", "candidate-2.mp4"]
    assert any("candidate-2.mp4" in note for note in result.get("notes", []))


def test_a_junk_link_on_a_page_does_not_stop_the_search():
    """Real pages carry broken hrefs like //[host; urlsplit refuses them outright."""
    html = ('<a href="//[www.terabox.com/s/1abc">x</a> '
            '<a href="https://www.terabox.com/s/1real">real</a>')
    found = source.extract_media_links(html)
    assert "https://www.terabox.com/s/1real" in found
