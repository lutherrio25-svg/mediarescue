"""Batch mode: one command repairs a folder, skips what is fine, and reports what failed."""
import struct
import subprocess

import pytest

from mediarescue.cli import main
from mediarescue.ffmpeg import have_ffmpeg

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def make_clip(path, seconds=3, repeat_headers=False):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x96:rate=10",
           "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", str(seconds),
           "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k"]
    if repeat_headers:
        cmd[-1:-1] = ["-x264-params", "repeat-headers=1"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True)


def corrupt_head(path, fraction=0.35, seed=3):
    data = bytearray(path.read_bytes())
    i = data.find(b"mdat") + 4
    span = int((len(data) - i) * fraction)
    rng = __import__("random").Random(seed)
    data[i:i + span] = bytes(rng.randrange(256) for _ in range(span))
    path.write_bytes(bytes(data))


def strip_index(src, dst):
    data = src.read_bytes()
    i = data.find(b"mdat")
    dst.write_bytes(data[i + 4:i - 4 + struct.unpack_from(">I", data, i - 4)[0]])


def build_folder(tmp_path):
    good = tmp_path / "good.mp4"
    make_clip(good)
    damaged = tmp_path / "damaged.mp4"
    make_clip(damaged)
    corrupt_head(damaged)
    hopeless = tmp_path / "hopeless.mp4"          # index gone, no SPS/PPS: carve must refuse
    make_clip(tmp_path / "src.mp4")
    strip_index(tmp_path / "src.mp4", hopeless)
    (tmp_path / "empty.mp4").write_bytes(b"")
    return good, damaged, hopeless


@needs_ffmpeg
def test_batch_repairs_damaged_and_leaves_healthy_alone(tmp_path, capsys):
    good, damaged, hopeless = build_folder(tmp_path)
    hopeless.unlink()                       # nothing must fail for this test to mean anything
    before = (good.stat().st_size, good.stat().st_mtime_ns)

    assert main(["fix", str(tmp_path)]) == 0
    out = capsys.readouterr().out

    assert (tmp_path / "damaged.repaired.mp4").exists()
    assert (good.stat().st_size, good.stat().st_mtime_ns) == before   # untouched
    assert not (tmp_path / "good.repaired.mp4").exists()
    assert "healthy, left untouched" in out
    assert "1 repaired" in out and "failed" not in out


@needs_ffmpeg
def test_batch_reports_needs_reference_and_still_exits_nonzero(tmp_path, capsys):
    """The index-stripped file is not "failed" — it is one intact sibling away from repairable."""
    _, damaged, _ = build_folder(tmp_path)

    assert main(["fix", str(tmp_path)]) == 1
    out = capsys.readouterr().out

    assert "need-ref" in out and "hopeless.mp4" in out
    assert "FAILED" not in out
    assert "how to fix the other 1" in out
    assert (tmp_path / "damaged.repaired.mp4").exists()   # the good repair still happened


@needs_ffmpeg
def test_batch_out_dir_keeps_the_source_folder_clean(tmp_path, capsys):
    _, damaged, _ = build_folder(tmp_path)
    target = tmp_path / "fixed"

    assert main(["fix", str(tmp_path), "--out-dir", str(target)]) in (0, 1)
    assert (target / "damaged.repaired.mp4").exists()
    assert not (tmp_path / "damaged.repaired.mp4").exists()
    assert "output folder" in capsys.readouterr().out


@needs_ffmpeg
def test_out_flag_is_refused_for_a_batch(tmp_path, capsys):
    build_folder(tmp_path)
    assert main(["fix", str(tmp_path), "-o", str(tmp_path / "one.mp4")]) == 2
    assert "use --out-dir" in capsys.readouterr().err


@needs_ffmpeg
def test_force_rewrites_healthy_files_too(tmp_path, capsys):
    good, _, _ = build_folder(tmp_path)
    assert main(["fix", str(tmp_path), "--force", "--out-dir", str(tmp_path / "all")]) in (0, 1)
    assert (tmp_path / "all" / "good.repaired.mp4").exists()
