"""Carve: rebuild video from a bare payload when the index is gone and there is no reference."""
import struct
import subprocess

import pytest

from mediarescue.carve import carve, extract_annexb, find_stream_start
from mediarescue.cli import main
from mediarescue.ffmpeg import have_ffmpeg, probe
from mediarescue.report import Severity
from mediarescue.diagnose import diagnose

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def make_clip(path, seconds=4, fps=25, audio=True):
    """repeat-headers keeps SPS/PPS in-band — without them carve cannot decode anything."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate={fps}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-g", str(fps), "-pix_fmt", "yuv420p",
            "-x264-params", "repeat-headers=1"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "64k"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def strip_index(src, dst):
    """Write out only the mdat payload: boxes gone, pictures still there."""
    data = src.read_bytes()
    i = data.find(b"mdat")
    start = i + 4
    end = i - 4 + struct.unpack_from(">I", data, i - 4)[0]
    dst.write_bytes(data[start:end])


@needs_ffmpeg
def test_bare_payload_is_diagnosed_as_missing_atoms(tmp_path):
    good = tmp_path / "good.mp4"
    make_clip(good)
    bare = tmp_path / "bare.mp4"
    strip_index(good, bare)
    assert diagnose(bare, hash_file=False).severity == Severity.MISSING_ATOMS


@needs_ffmpeg
def test_carve_rebuilds_playable_video_without_a_reference(tmp_path):
    good = tmp_path / "good.mp4"
    make_clip(good, audio=False)          # contiguous samples: the case carve is built for
    bare = tmp_path / "bare.mp4"
    strip_index(good, bare)

    out = tmp_path / "carved.mp4"
    result = carve(bare, out, fps=25)

    assert result["ok"], result
    assert result["verify"]["decode_errors"] == 0
    assert 3.0 < probe(out)["duration"] < 5.0
    assert result["nals"] > 50


@needs_ffmpeg
def test_interleaved_samples_are_refused_not_faked(tmp_path):
    """One video sample per chunk cannot be delimited without the index; say so."""
    good = tmp_path / "good.mp4"
    make_clip(good, seconds=3, audio=True)
    bare = tmp_path / "bare.mp4"
    strip_index(good, bare)

    result = carve(bare, tmp_path / "out.mp4")
    assert result["ok"] is False
    assert "--reference" in result["reason"]


@needs_ffmpeg
def test_carve_refuses_when_sps_pps_lived_in_the_index(tmp_path):
    """Default x264 keeps SPS/PPS in avcC only: without the moov nothing can decode it."""
    plain = tmp_path / "plain.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=320x240:rate=25", "-t", "3",
                    "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p", str(plain)],
                   check=True, capture_output=True)
    bare = tmp_path / "plain_bare.mp4"
    strip_index(plain, bare)

    result = carve(bare, tmp_path / "out.mp4")
    assert result["ok"] is False
    assert "SPS/PPS" in result["reason"] and "--reference" in result["reason"]


@needs_ffmpeg
def test_cli_falls_back_to_carve_when_no_reference_is_given(tmp_path, capsys):
    good = tmp_path / "good.mp4"
    make_clip(good, audio=False)
    bare = tmp_path / "bare.mp4"
    strip_index(good, bare)

    out = tmp_path / "rebuilt.mp4"
    assert main(["fix", str(bare), "-o", str(out)]) == 0
    printed = capsys.readouterr()
    assert '"strategy": "carve"' in printed.out
    assert "no --reference given" in printed.err
    assert probe(out)["ok"]


@needs_ffmpeg
def test_carve_says_so_when_there_is_no_h264(tmp_path):
    noise = tmp_path / "noise.mp4"
    noise.write_bytes(bytes(range(256)) * 400)
    result = carve(noise, tmp_path / "out.mp4")
    assert result["ok"] is False
    assert "no length-prefixed H.264" in result["reason"]
