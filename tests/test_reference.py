"""Auto-reference: a batch should find its own reference file, by codec, not by name."""
import subprocess

import pytest

from mediarescue.cli import main
from mediarescue.ffmpeg import have_ffmpeg, probe
from mediarescue.reference import find_reference, source_video_codec

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def make_clip(path, seconds=3):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=160x96:rate=10", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=44100", "-t", str(seconds),
                    "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "64k", str(path)],
                   check=True, capture_output=True)


def make_fragmented(src, dst):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c", "copy",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof", str(dst)],
                   check=True, capture_output=True)
    data = dst.read_bytes()
    dst.write_bytes(data[data.find(b"moof") - 4:])       # lose ftyp+moov


@needs_ffmpeg
def test_codec_is_read_from_a_damaged_file_without_an_index(tmp_path):
    good = tmp_path / "good.mp4"
    make_clip(good)
    frag = tmp_path / "frag.mp4"
    make_fragmented(good, frag)
    assert source_video_codec(frag) == "h264"


@needs_ffmpeg
def test_reference_is_picked_by_codec_not_by_name(tmp_path):
    good = tmp_path / "good.mp4"
    make_clip(good)
    frag = tmp_path / "zzz_fragment.mp4"
    make_fragmented(good, frag)
    (tmp_path / "aaa_notes.mp3").write_bytes(b"\x00" * 512)   # tempting, wrong

    assert find_reference(frag) == good


@needs_ffmpeg
def test_no_reference_when_the_folder_has_no_match(tmp_path):
    frag_dir = tmp_path / "frag"
    frag_dir.mkdir()
    good = tmp_path / "good.mp4"
    make_clip(good)
    frag = frag_dir / "frag.mp4"
    make_fragmented(good, frag)

    assert find_reference(frag) is None


@needs_ffmpeg
def test_batch_uses_the_sibling_and_reports_it(tmp_path, capsys):
    good = tmp_path / "good.mp4"
    make_clip(good)
    frag = tmp_path / "frag.mp4"
    make_fragmented(good, frag)
    out_dir = tmp_path / "fixed"

    assert main(["fix", str(frag), "-o", str(out_dir / "frag.mp4")]) == 0
    printed = capsys.readouterr().out
    assert '"reference"' in printed and "good.mp4" in printed
    assert '"strategy": "flatten"' in printed
    assert probe(out_dir / "frag.mp4")["ok"]
