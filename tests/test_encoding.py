"""Console-encoding robustness.

A real run over a damaged drive aborted in the middle of the scan table with
`OSError: [Errno 22] Invalid argument`: one filename held an unpaired UTF-16 surrogate (NTFS
stores raw UTF-16 units, so a corrupted directory entry can contain one), and writing that to a
Windows console is rejected. One unprintable name must never cost the user the whole report.
"""
from __future__ import annotations

import io

import pytest

from mediarescue import ui
from mediarescue.cli import main

SURROGATE_NAME = "bad\ud800name.mp4"


class ExplodingStream(io.StringIO):
    """A console stand-in that refuses text the way a Windows console does."""

    def write(self, text: str) -> int:
        if any("\ud800" <= ch <= "\udfff" for ch in text):
            raise OSError(22, "Invalid argument")
        return super().write(text)


def test_printable_swaps_a_lone_surrogate_for_a_placeholder():
    assert ui.printable(SURROGATE_NAME) == "bad\ufffdname.mp4"


def test_printable_leaves_ordinary_text_alone():
    text = "C:/media/holiday vídeo 日本語.mp4"
    assert ui.printable(text) == text


def test_the_forgiving_stream_retries_instead_of_raising():
    inner = ExplodingStream()
    stream = ui.ForgivingStream(inner)
    assert stream.write("plain text") == len("plain text")
    stream.write(SURROGATE_NAME)  # must not raise
    assert inner.getvalue() == "plain textbad\ufffdname.mp4"


def test_the_forgiving_stream_falls_back_to_escapes_when_the_console_is_ascii():
    class AsciiOnly(io.TextIOBase):
        encoding = "ascii"

        def __init__(self):
            self.seen = ""

        def write(self, text: str) -> int:
            if not text.isascii():
                raise UnicodeEncodeError("ascii", text, 0, 1, "ordinal not in range(128)")
            self.seen += text
            return len(text)

    inner = AsciiOnly()
    ui.ForgivingStream(inner).write("café.mp4")  # must not raise
    assert inner.seen == "caf\\xe9.mp4"


def test_a_reader_that_walks_away_is_not_an_error():
    """`mediarescue scan big-folder | head` closes the pipe; Windows says OSError EINVAL."""

    class ClosedPipe(io.TextIOBase):
        encoding = "utf-8"

        def __init__(self):
            self.attempts = 0

        def write(self, text: str) -> int:
            self.attempts += 1
            raise OSError(22, "Invalid argument")

        def flush(self) -> None:
            raise OSError(22, "Invalid argument")

        def close(self) -> None:  # keep the interpreter's final flush quiet
            pass

    inner = ClosedPipe()
    stream = ui.ForgivingStream(inner)
    assert stream.write("severity bytes fix file") == len("severity bytes fix file")
    stream.write("another line")  # stays quiet instead of raising again
    stream.flush()
    assert inner.attempts == 1


def test_the_wrapper_behaves_like_the_stream_it_wraps():
    inner = io.StringIO()
    stream = ui.ForgivingStream(inner)
    assert stream.encoding == inner.encoding
    assert stream.isatty() is False
    stream.flush()
    stream.write("x")
    assert inner.getvalue() == "x"


def test_forgiving_output_is_installed_only_once(monkeypatch):
    original = io.StringIO()
    monkeypatch.setattr("sys.stdout", original)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    ui.forgiving_output()
    once = ui.sys.stdout
    ui.forgiving_output()
    assert ui.sys.stdout is once
    assert isinstance(once, ui.ForgivingStream)


@pytest.fixture()
def unprintable_media(tmp_path):
    target = tmp_path / SURROGATE_NAME
    try:
        target.write_bytes(b"\x00" * 4096)
        if target.name != SURROGATE_NAME:  # pragma: no cover - filesystem mangled the name
            pytest.skip("this filesystem cannot store a surrogate name")
    except (OSError, UnicodeError):  # pragma: no cover - Linux refuses surrogates outright
        pytest.skip("this filesystem cannot store a surrogate name")
    return target


def test_a_scan_survives_a_filename_the_console_cannot_print(unprintable_media, capsys):
    code = main(["scan", str(unprintable_media.parent), "--no-color"])
    out = capsys.readouterr().out
    assert code == 0
    assert "severity" in out
    assert "\ufffd" in out  # the name was shown, just not byte-for-byte


def test_json_output_stays_valid_with_an_unprintable_name(unprintable_media, capsys):
    import json

    code = main(["scan", str(unprintable_media.parent), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    payload = json.loads(out)  # a lone surrogate would make this raise
    assert len(payload) == 1
