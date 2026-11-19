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
    good = tmp_path / "good.mp4"
    make_clip(good)
    # Two levels down: outside the search cone (same folder + sub-folders + one level up).
    deep = tmp_path / "x" / "y"
    deep.mkdir(parents=True)
    frag = deep / "frag.mp4"
    make_fragmented(good, frag)

    assert find_reference(frag) is None


@needs_ffmpeg
def test_reference_is_found_in_a_sibling_folder(tmp_path):
    """Segments in dated folders: the intact file lives beside the damaged one, not in it."""
    seg = tmp_path / "2026-11-16"
    seg.mkdir()
    good_dir = tmp_path / "intact"
    good_dir.mkdir()
    good = good_dir / "good.mp4"
    make_clip(good)
    frag = seg / "frag.mp4"
    make_fragmented(good, frag)

    assert find_reference(frag) == good


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


def _healthy(monkeypatch, reference):
    """Pretend every candidate near the damaged file is intact, without needing ffmpeg."""
    from types import SimpleNamespace

    from mediarescue.report import Severity
    monkeypatch.setattr(reference, "diagnose",
                        lambda p, hash_file=False: SimpleNamespace(severity=Severity.HEALTHY))


def test_with_no_readable_codec_a_same_size_twin_is_still_used(tmp_path, monkeypatch):
    """Index gone and payload unrecognisable: size is the only evidence left, so use it."""
    from mediarescue import reference

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00" * 1000)
    twin = tmp_path / "unrelated name.mp4"
    twin.write_bytes(b"\x00" * 1004)

    monkeypatch.setattr(reference, "source_video_codec", lambda p: None)
    _healthy(monkeypatch, reference)

    assert reference.find_reference(src) == twin


def test_the_program_never_borrows_its_own_repair_as_a_reference(tmp_path, monkeypatch):
    """A `.repaired.mp4` beside the original is our output; grafting it back is a loop."""
    from mediarescue import reference

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00" * 1000)
    ours = tmp_path / "clip.repaired.mp4"
    ours.write_bytes(b"\x00" * 1000)
    real = tmp_path / "clip twin.mp4"
    real.write_bytes(b"\x00" * 1000)

    monkeypatch.setattr(reference, "source_video_codec", lambda p: None)
    _healthy(monkeypatch, reference)

    assert reference.find_reference(src) == real


def test_a_shared_name_beats_a_bare_size_match(tmp_path, monkeypatch):
    from mediarescue import reference

    src = tmp_path / "holiday clip 2019.mp4"
    src.write_bytes(b"\x00" * 1000)
    sized = tmp_path / "aaa.mp4"            # closer in size, nothing else in common
    sized.write_bytes(b"\x00" * 1000)
    named = tmp_path / "zzz holiday clip.mp4"   # further in size, but the same name
    named.write_bytes(b"\x00" * 1080)

    monkeypatch.setattr(reference, "source_video_codec", lambda p: None)
    _healthy(monkeypatch, reference)

    assert reference.find_reference(src) == named

