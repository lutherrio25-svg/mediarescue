"""AVI: the index is optional for playback, so only index damage is worth reporting.

Sequential decoding of an AVI never consults `idx1` — libavformat rescans `movi` — which is why a
damaged AVI decodes cleanly and a header scan sees nothing. What can be checked cheaply is whether
each `idx1` entry really points at the chunk it names, and whether an index exists at all.
"""
import struct
import subprocess

import pytest

from mediarescue.avi import check
from mediarescue.cli import main
from mediarescue.ffmpeg import have_ffmpeg

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def make_avi(path, seconds=3):
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=160x120:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", str(seconds),
                    "-c:v", "mpeg4", "-c:a", "libmp3lame", str(path)],
                   check=True, capture_output=True)


def shift_entries(path, delta=64):
    """Keep every chunk header intact, make the index point 64 bytes past each chunk."""
    data = bytearray(path.read_bytes())
    at = data.find(b"idx1")
    length = struct.unpack_from("<I", data, at + 4)[0]
    count = length // 16
    for i in range(count):
        off = struct.unpack_from("<I", data, at + 8 + i * 16 + 8)[0]
        struct.pack_into("<I", data, at + 8 + i * 16 + 8, off + delta)
    path.write_bytes(bytes(data))
    return count


def decode_errors(path):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True)
    return len([ln for ln in p.stderr.splitlines() if ln.strip()])


@needs_ffmpeg
def test_a_clean_avi_has_a_verifiable_index(tmp_path):
    clip = tmp_path / "good.avi"
    make_avi(clip)
    info = check(clip)
    assert info and info["has_index"] and info["entries"] > 0
    assert info["bad"] == 0


@needs_ffmpeg
def test_shifted_index_entries_are_all_caught(tmp_path, capsys):
    clip = tmp_path / "badidx.avi"
    make_avi(clip)
    count = shift_entries(clip)
    info = check(clip)
    assert info is not None and info["bad"] == count

    assert main(["scan", str(clip)]) == 0
    assert "corrupt-index" in capsys.readouterr().out


@needs_ffmpeg
def test_a_lost_index_is_reported_as_repairable(tmp_path):
    clip = tmp_path / "cut.avi"
    make_avi(clip)
    data = clip.read_bytes()
    clip.write_bytes(data[:int(len(data) * 0.85)])          # idx1 lives at the end
    info = check(clip)
    assert info is not None and info["has_index"] is False


@needs_ffmpeg
def test_fix_rewrites_a_correct_index_without_touching_the_pictures(tmp_path):
    clip = tmp_path / "badidx.avi"
    make_avi(clip)
    shift_entries(clip)
    before = decode_errors(clip)
    out = tmp_path / "fixed.avi"

    assert main(["fix", str(clip), "-o", str(out)]) == 0
    info = check(out)
    source = check(clip)
    assert info is not None and source is not None
    assert info["has_index"] and info["bad"] == 0
    assert info["entries"] >= source["entries"] - 1
    assert decode_errors(out) == before == 0
