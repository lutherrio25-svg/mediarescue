import struct

import pytest

from mediarescue.diagnose import diagnose
from mediarescue.ffmpeg import have_ffmpeg, probe, remux
from mediarescue.mp4 import read_boxes
from mediarescue.report import Severity
from mediarescue.trim import trim_mdat
from test_core import make_mp4

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg not installed")


@needs_ffmpeg
def test_truncated_mdat_hides_moov(tmp_path):
    src = make_mp4(tmp_path / "plain.mp4")  # moov at end
    raw = bytearray(src.read_bytes())
    boxes = read_boxes(bytes(raw))
    mdat = next(b for b in boxes if b.type == "mdat")
    moov = next(b for b in boxes if b.type == "moov")
    assert moov.offset > mdat.offset, "fixture must have moov after mdat"

    struct.pack_into(">I", raw, mdat.offset, mdat.size + (5 << 20))
    damaged = tmp_path / "broken.mp4"
    damaged.write_bytes(bytes(raw))

    d = diagnose(damaged)
    assert d.severity == Severity.MISSING_TRAILER, d.notes

    trimmed = tmp_path / "trimmed.mp4"
    res = trim_mdat(damaged, trimmed)
    assert res["ok"], res
    assert probe(trimmed)["ok"]

    out = tmp_path / "final.mp4"
    ok, log = remux(trimmed, out)
    assert ok, log
    assert probe(out)["duration"] == pytest.approx(1.0, abs=0.3)


def test_png_truncation(tmp_path):
    good = tmp_path / "ok.png"
    good.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32 + b"IEND\xaeB`\x82")
    assert diagnose(good).severity == Severity.HEALTHY
    bad = tmp_path / "cut.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    assert diagnose(bad).severity == Severity.MISSING_TRAILER
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"\x00" * 64)
    assert diagnose(junk).severity == Severity.HEADERLESS
