import shutil
import struct
import subprocess

import pytest

from mediarescue.diagnose import diagnose
from mediarescue.ffmpeg import have_ffmpeg, probe, remux
from mediarescue.fragmented import flatten, parse_fragments
from mediarescue.graft import graft
from mediarescue.mp4 import read_boxes
from mediarescue.report import Severity

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg not installed")


def make_mp4(path, extra=(), duration="1"):
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=800",
        "-t", duration, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        *extra, str(path),
    ], check=True, capture_output=True)
    return path


@pytest.fixture
def healthy(tmp_path):
    return make_mp4(tmp_path / "healthy.mp4", ["-movflags", "+faststart"])


def test_box_roundtrip():
    buf = struct.pack(">I4s", 16, b"ftyp") + b"isom" * 2 + struct.pack(">I4s", 12, b"free") + b"\0" * 4
    boxes = read_boxes(buf)
    assert [b.type for b in boxes] == ["ftyp", "free"]
    assert boxes[0].size == 16


def test_bad_header_stops_walk():
    assert read_boxes(b"\x00" * 64) == []
    assert read_boxes(b"\xff\xff\xff\xffftyp") == []


def test_empty_file(tmp_path):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    d = diagnose(p)
    assert d.severity == Severity.EMPTY
    assert not d.recoverable


def test_zero_head_is_headerless(tmp_path):
    p = tmp_path / "zeroed.mp4"
    p.write_bytes(b"\x00" * (2 << 20))
    assert diagnose(p).severity in (Severity.HEADERLESS, Severity.MISSING_ATOMS)


@needs_ffmpeg
def test_healthy_diagnosis(healthy):
    d = diagnose(healthy)
    assert d.severity == Severity.HEALTHY
    assert d.recoverable
    assert any(a.type == "moov" for a in d.atoms)


@needs_ffmpeg
def test_remux_writes_new_file(healthy, tmp_path):
    out = tmp_path / "out.mp4"
    before = healthy.read_bytes()
    ok, log = remux(healthy, out)
    assert ok, log
    assert probe(out)["ok"]
    assert healthy.read_bytes() == before


@needs_ffmpeg
def test_graft_restores_missing_moov(healthy, tmp_path):
    raw = healthy.read_bytes()
    boxes = read_boxes(raw)
    moov = next(b for b in boxes if b.type == "moov")
    damaged = tmp_path / "damaged.mp4"
    damaged.write_bytes(raw[:moov.offset] + raw[moov.end:])
    assert diagnose(damaged).severity == Severity.MISSING_ATOMS

    out = tmp_path / "fixed.mp4"
    res = graft(damaged, healthy, out)
    assert res.ok, res.reason
    info = probe(out)
    assert info["ok"], info
    assert info["duration"] == pytest.approx(1.0, abs=0.3)


@needs_ffmpeg
def test_flatten_fragmented(healthy, tmp_path):
    frag = make_mp4(tmp_path / "frag.mp4",
                    ["-movflags", "+frag_keyframe+empty_moov+default_base_moof"])
    raw = frag.read_bytes()
    boxes = read_boxes(raw)
    first_moof = next(b for b in boxes if b.type == "moof")
    damaged = tmp_path / "init-lost.mp4"
    damaged.write_bytes(raw[first_moof.offset:])
    assert diagnose(damaged).severity == Severity.FRAGMENTED

    tracks = parse_fragments(damaged.read_bytes())
    assert tracks, "no fragments parsed"
    assert all(t.samples for t in tracks.values())

    out = tmp_path / "flat.mp4"
    res = flatten(damaged, healthy, out)
    assert res["ok"], res
    info = probe(out)
    assert info["ok"], info
    assert "h264" in info["streams"]
    assert "aac" in info["streams"], info  # both tracks must survive, not just video
    assert info["duration"] == pytest.approx(1.0, abs=0.3), info
