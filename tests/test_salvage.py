"""Salvage: a file whose head was overwritten must come out clean, starting at a real IDR."""
import random
import struct
import subprocess

import pytest

from mediarescue.ffmpeg import have_ffmpeg, probe
from mediarescue.mp4 import find, read_boxes
from mediarescue.salvage import salvage
from mediarescue.tables import first_clean_keyframe, nal_types, sample_table

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def make_clip(path, seconds=4):
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
        "-t", str(seconds), "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k", str(path),
    ], check=True, capture_output=True)


def corrupt_head(path, fraction=0.3, seed=7):
    """Overwrite the first `fraction` of mdat payload with noise, leaving the moov valid."""
    data = bytearray(path.read_bytes())
    mdat = next(b for b in read_boxes(bytes(data)) if b.type == "mdat")
    span = int((mdat.end - mdat.payload) * fraction)
    rng = random.Random(seed)
    data[mdat.payload:mdat.payload + span] = bytes(rng.randrange(256) for _ in range(span))
    path.write_bytes(bytes(data))
    return mdat.payload, span


def decode_errors(path) -> str:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True)
    return p.stderr.strip()


@needs_ffmpeg
def test_sample_table_matches_probe(tmp_path):
    clip = tmp_path / "clean.mp4"
    make_clip(clip)
    buf = clip.read_bytes()
    moov = find(buf, "moov")
    assert moov is not None
    video = [b for b in read_boxes(buf, moov.payload, moov.end) if b.type == "trak"][0]
    table = sample_table(buf, video)
    assert table["handler"] == b"vide"
    assert len(table["samples"]) == 100  # 4s at 25fps
    first = table["samples"][0]
    assert 5 in (nal_types(buf[first["offset"]:first["offset"] + first["size"]]) or [])


@needs_ffmpeg
def test_clean_file_needs_no_cut(tmp_path):
    clip = tmp_path / "clean.mp4"
    make_clip(clip)
    key = first_clean_keyframe(clip.read_bytes())
    assert key and key["time"] < 0.5 and key["dropped"] == 0


@needs_ffmpeg
def test_overwritten_head_is_not_reported_healthy(tmp_path):
    """A perfect index over junk pictures must not pass as healthy: a probe would."""
    from mediarescue.diagnose import diagnose
    from mediarescue.report import Severity

    clip = tmp_path / "bad.mp4"
    make_clip(clip)
    corrupt_head(clip, fraction=0.3)

    assert diagnose(clip, hash_file=False).severity == Severity.CORRUPT_PAYLOAD
    assert probe(clip)["ok"]  # the lie the old classifier believed


@needs_ffmpeg
def test_salvage_drops_only_the_corrupt_head(tmp_path):
    clip = tmp_path / "bad.mp4"
    make_clip(clip)
    corrupt_head(clip, fraction=0.3)

    out = tmp_path / "salvaged.mp4"
    result = salvage(clip, out)

    assert result["ok"], result
    assert result["dropped_seconds"] > 0.5
    assert decode_errors(out) == ""
    duration = probe(out)["duration"]
    assert 0.5 < duration < 4.1
    assert not list(tmp_path.glob("*.stage.*"))      # working copies never outlive the run


@needs_ffmpeg
def test_salvage_failure_leaves_no_stage_file_behind(tmp_path, monkeypatch):
    """A real batch left a 93 MB .stage file in the output folder after a failed remux; the next
    scan then classified the tool's own working copy as a damaged file."""
    import mediarescue.salvage as salvage_mod

    clip = tmp_path / "bad.mp4"
    make_clip(clip)
    # Oversize the mdat box so trim_mdat writes its staging copy — the exact situation that
    # leaked: trim succeeds, remux then fails, and the stage file must not survive.
    data = bytearray(clip.read_bytes())
    mdat = next(b for b in read_boxes(bytes(data)) if b.type == "mdat")
    struct.pack_into(">I", data, mdat.offset, len(data) * 2)
    clip.write_bytes(bytes(data))

    monkeypatch.setattr(salvage_mod, "remux", lambda *a, **k: (False, ["boom"]))
    result = salvage(clip, tmp_path / "salvaged.mp4")

    assert not result["ok"]
    assert not list(tmp_path.glob("*.stage.*"))
