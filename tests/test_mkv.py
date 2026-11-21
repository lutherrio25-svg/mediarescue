"""Matroska whose head was cut away: the clusters survive, the track table does not.

`ffmpeg -c copy` will not even open such a file, so this is the one damage class where the
repair has to write a header before ffmpeg is allowed to look at it. These tests build real
MKV files with ffmpeg, cut the head off, and check that what comes back decodes frame for frame.
"""
import subprocess

import pytest

from mediarescue import mkv
from mediarescue.cli import main
from mediarescue.diagnose import diagnose
from mediarescue.ebml import read_id, read_size
from mediarescue.ffmpeg import have_ffmpeg
from mediarescue.report import Severity

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def make_mkv(path, seconds=6, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=15"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-g", "15", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd += [str(path)]
    subprocess.run(cmd, check=True, capture_output=True)


def cut_head(source, target, fraction=0.35):
    """Throw away the first `fraction` of the bytes: header, Info and Tracks go with it."""
    data = source.read_bytes()
    target.write_bytes(data[int(len(data) * fraction):])
    return len(data) - len(target.read_bytes())


def decode_errors(path):
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True, check=False)
    return [ln for ln in p.stderr.splitlines() if ln.strip()]


def duration(path):
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=False)
    return float(p.stdout.strip() or 0)


# --- the EBML reader --------------------------------------------------------------------------

def test_sizes_and_ids_round_trip():
    assert read_id(b"\x1a\x45\xdf\xa3", 0) == (0x1A45DFA3, 4)
    assert read_size(b"\x8f", 0) == (15, 1)
    assert read_size(b"\x40\x00", 0) == (0, 2)
    # all-ones payload means "unknown size", which live writers really do emit
    assert read_size(b"\xff", 0) == (None, 1)
    assert read_id(b"\x00\x00", 0) == (-1, 0)


def test_a_plain_file_is_not_mistaken_for_matroska(tmp_path):
    junk = tmp_path / "a.mkv"
    junk.write_bytes(b"not a matroska file at all" * 40)
    verdict = mkv.check(junk)
    assert verdict["matroska"] is False and verdict["recoverable"] is False


# --- diagnosis --------------------------------------------------------------------------------

@needs_ffmpeg
def test_an_intact_mkv_reads_as_healthy(tmp_path):
    clip = tmp_path / "good.mkv"
    make_mkv(clip)
    d = diagnose(clip, hash_file=False)
    assert d.severity == Severity.HEALTHY
    assert any("Info and Tracks parse" in note for note in d.notes)


@needs_ffmpeg
def test_a_cut_head_is_recoverable_not_headerless(tmp_path):
    clip = tmp_path / "good.mkv"
    make_mkv(clip)
    broken = tmp_path / "cut.mkv"
    cut_head(clip, broken)

    d = diagnose(broken, hash_file=False)
    assert d.severity == Severity.MKV_HEADLESS
    assert d.recoverable and d.needs_reference
    assert any("Cluster starts at byte" in note for note in d.notes)


@needs_ffmpeg
def test_a_truncated_mkv_keeps_its_header_and_is_only_caught_by_decoding(tmp_path):
    clip = tmp_path / "good.mkv"
    make_mkv(clip)
    short = tmp_path / "short.mkv"
    short.write_bytes(clip.read_bytes()[: int(clip.stat().st_size * 0.6)])

    assert diagnose(short, hash_file=False).severity == Severity.HEALTHY
    assert diagnose(short, hash_file=False, deep=True).severity == Severity.CORRUPT_PAYLOAD


# --- repair -----------------------------------------------------------------------------------

@needs_ffmpeg
def test_transplanting_the_track_table_restores_the_remaining_footage(tmp_path):
    clip = tmp_path / "good.mkv"
    make_mkv(clip)
    broken = tmp_path / "cut.mkv"
    cut_head(clip, broken)
    before = broken.read_bytes()

    result = mkv.salvage(broken, clip, tmp_path / "fixed.mkv")

    assert result["ok"], result.get("reason")
    out = tmp_path / "fixed.mkv"
    assert decode_errors(out) == []
    assert 0 < duration(out) < duration(clip)
    assert result["dropped_seconds"] > 1
    assert broken.read_bytes() == before  # the damaged file is never written to


@needs_ffmpeg
def test_the_rebuilt_file_carries_both_streams(tmp_path):
    clip = tmp_path / "good.mkv"
    make_mkv(clip, audio=True)
    broken = tmp_path / "cut.mkv"
    cut_head(clip, broken)

    result = mkv.salvage(broken, clip, tmp_path / "fixed.mkv")

    assert result["ok"] and result["tracks"] == [1, 2]
    codecs = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name",
                             "-of", "csv=p=0", str(tmp_path / "fixed.mkv")],
                            capture_output=True, text=True, check=False).stdout.split()
    assert codecs == ["h264", "aac"]


@needs_ffmpeg
def test_a_reference_without_the_missing_track_is_refused(tmp_path):
    """A video-only reference cannot describe a file whose second track is audio."""
    with_audio = tmp_path / "with-audio.mkv"
    make_mkv(with_audio, audio=True)
    video_only = tmp_path / "video-only.mkv"
    make_mkv(video_only, audio=False)
    broken = tmp_path / "cut.mkv"
    cut_head(with_audio, broken)

    result = mkv.salvage(broken, video_only, tmp_path / "fixed.mkv")

    assert not result["ok"]
    assert "track" in result["reason"] or "tracks" in result["reason"]


@needs_ffmpeg
def test_a_file_with_no_cluster_at_all_is_refused_with_a_reason(tmp_path):
    junk = tmp_path / "nothing.mkv"
    junk.write_bytes(b"\x00" * 5000)
    reference = tmp_path / "good.mkv"
    make_mkv(reference)

    result = mkv.salvage(junk, reference, tmp_path / "fixed.mkv")

    assert not result["ok"]
    assert "cluster" in result["reason"].lower()
    assert not (tmp_path / "fixed.mkv").exists()


@needs_ffmpeg
def test_a_reference_that_is_not_matroska_is_refused(tmp_path):
    clip = tmp_path / "good.mkv"
    make_mkv(clip)
    broken = tmp_path / "cut.mkv"
    cut_head(clip, broken)
    fake = tmp_path / "ref.mkv"
    fake.write_bytes(b"plain text, not EBML" * 100)

    result = mkv.salvage(broken, fake, tmp_path / "fixed.mkv")

    assert not result["ok"]
    assert "Info and Tracks" in result["reason"]


# --- candidate picking ------------------------------------------------------------------------

@needs_ffmpeg
def test_candidates_are_ranked_by_name_then_size_and_skip_our_own_output(tmp_path):
    wanted = tmp_path / "holiday-camera-01.mkv"
    make_mkv(wanted)
    near = tmp_path / "holiday-camera-01.repaired.mkv"
    make_mkv(near)
    other = tmp_path / "something-else.mkv"
    make_mkv(other)
    nested = tmp_path / "sub" / "holiday-camera-02.mkv"
    nested.parent.mkdir()
    make_mkv(nested)
    broken = tmp_path / "holiday-camera-01-broken.mkv"
    cut_head(wanted, broken)

    names = [p.name for p in mkv.reference_candidates(broken)]

    assert "holiday-camera-01.repaired.mkv" not in names  # our own output is never a reference
    assert names[0].startswith("holiday-camera")


@needs_ffmpeg
def test_a_folder_with_no_other_mkv_offers_nothing(tmp_path):
    """Candidates come from the file's folder and one level up — not from the whole tree."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    make_mkv(elsewhere / "good.mkv")
    lonely = tmp_path / "alone"
    lonely.mkdir()
    cut_head(elsewhere / "good.mkv", lonely / "cut.mkv")

    assert mkv.reference_candidates(lonely / "cut.mkv") == []


# --- through the command line ----------------------------------------------------------------

@needs_ffmpeg
def test_fix_picks_a_sibling_reference_and_verifies_the_result(tmp_path, capsys):
    reference = tmp_path / "camera-01.mkv"
    make_mkv(reference)
    broken = tmp_path / "camera-01-broken.mkv"
    cut_head(reference, broken)
    out = tmp_path / "out"

    assert main(["fix", str(broken), "--out-dir", str(out), "--no-color", "--brief"]) == 0
    printed = capsys.readouterr().out
    assert "salvage-mkv" in printed and "decodes cleanly" in printed
    assert decode_errors(out / "camera-01-broken.repaired.mkv") == []


@needs_ffmpeg
def test_fix_without_any_reference_says_what_to_pass(tmp_path, capsys):
    clip = tmp_path / "only.mkv"
    make_mkv(clip)
    broken = tmp_path / "only-cut.mkv"
    cut_head(clip, broken)
    clip.unlink()  # nothing left in the folder to borrow a track table from
    out = tmp_path / "out"

    assert main(["fix", str(broken), "--out-dir", str(out), "--no-color", "--brief"]) == 1
    printed = capsys.readouterr().out
    assert "--reference" in printed
    assert not list(out.glob("*.mkv")) if out.exists() else True


@needs_ffmpeg
def test_scan_keeps_deep_in_the_command_it_suggests(tmp_path, capsys):
    clip = tmp_path / "good.mkv"
    make_mkv(clip)
    (tmp_path / "short.mkv").write_bytes(clip.read_bytes()[: int(clip.stat().st_size * 0.6)])

    assert main(["scan", str(tmp_path), "--deep", "--no-color"]) == 0
    printed = capsys.readouterr().out
    assert "next: mediarescue fix" in printed and "--deep" in printed.split("next:")[1]


# --- verify: the standalone decode gate -------------------------------------------------------

@needs_ffmpeg
def test_verify_passes_a_clean_file(tmp_path, capsys):
    clip = tmp_path / "good.mkv"
    make_mkv(clip, seconds=2)

    assert main(["verify", str(clip), "--no-color"]) == 0
    assert "every frame decoded" in capsys.readouterr().out


@needs_ffmpeg
def test_verify_fails_a_file_it_cannot_open(tmp_path, capsys):
    broken = tmp_path / "cut.mkv"
    make_mkv(tmp_path / "good.mkv")
    cut_head(tmp_path / "good.mkv", broken)

    assert main(["verify", str(broken), "--no-color"]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_verify_reports_a_missing_file_without_crashing(tmp_path, capsys):
    assert main(["verify", str(tmp_path / "ghost.mp4")]) == 2
    assert "not found" in capsys.readouterr().err


# --- webm is matroska too ---------------------------------------------------------------------

def test_webm_files_are_scanned_at_all(tmp_path, capsys):
    """Before 0.9.0 a .webm was invisible to scan: it is Matroska, and was never in the suffix set."""
    (tmp_path / "clip.webm").write_bytes(b"\x00" * 64)
    assert main(["scan", str(tmp_path), "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "1 scanned" in out and "clip.webm" in out


@needs_ffmpeg
def test_a_cut_webm_is_reported_as_recoverable(tmp_path):
    """WebM is Matroska with a Doctype; the same transplant applies."""
    clip = tmp_path / "good.webm"
    # -g 15 matters: the webm muxer opens a new cluster on each keyframe, and a clip with a
    # single cluster has nothing left once the head is cut.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=160x120:rate=15", "-t", "6", "-g", "15",
                    "-c:v", "libvpx-vp9", "-b:v", "200k", str(clip)],
                   check=True, capture_output=True)
    broken = tmp_path / "cut.webm"
    cut_head(clip, broken)

    d = diagnose(broken, hash_file=False)
    assert d.severity == Severity.MKV_HEADLESS


@needs_ffmpeg
def test_a_cut_webm_is_rebuilt_too(tmp_path):
    """VP9/WebM blocks are the same EBML blocks: the transplant does not care about the codec."""
    clip = tmp_path / "good.webm"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc2=size=160x120:rate=15", "-t", "6", "-g", "15",
                    "-c:v", "libvpx-vp9", "-b:v", "200k", str(clip)],
                   check=True, capture_output=True)
    broken = tmp_path / "cut.webm"
    cut_head(clip, broken)

    result = mkv.salvage(broken, clip, tmp_path / "fixed.webm")

    assert result["ok"], result.get("reason")
    assert decode_errors(tmp_path / "fixed.webm") == []
    assert duration(tmp_path / "fixed.webm") > 1


@pytest.mark.parametrize("suffix", [".mkv", ".mka", ".webm"])
def test_every_matroska_suffix_takes_the_matroska_branch(tmp_path, suffix):
    blast = tmp_path / f"zero{suffix}"
    blast.write_bytes(b"\x00" * 2048)
    d = diagnose(blast, hash_file=False)
    assert d.severity == Severity.HEADERLESS
    assert any("EBML" in note for note in d.notes)
