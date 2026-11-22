"""scan --deep must catch damage that headers cannot show (MP3/MKV/AVI, junk MP4 payloads)."""
import random
import subprocess

import pytest

from mediarescue.cli import main
from mediarescue.diagnose import diagnose
from mediarescue.ffmpeg import have_ffmpeg
from mediarescue.report import Severity

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def make_mp3(path, seconds=3):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=44100", "-t", str(seconds),
                    "-c:a", "libmp3lame", str(path)], check=True, capture_output=True)


def corrupt_middle(path, fraction=0.4, seed=9):
    data = bytearray(path.read_bytes())
    lo = int(len(data) * 0.3)
    hi = lo + int(len(data) * fraction)
    rng = random.Random(seed)
    data[lo:hi] = bytes(rng.randrange(256) for _ in range(hi - lo))
    path.write_bytes(bytes(data))


@needs_ffmpeg
def test_header_scan_misses_mp3_damage_but_deep_finds_it(tmp_path):
    clip = tmp_path / "a.mp3"
    make_mp3(clip)
    corrupt_middle(clip)

    assert diagnose(clip, hash_file=False).severity == Severity.HEALTHY  # the blind spot
    deep = diagnose(clip, hash_file=False, deep=True)
    assert deep.severity == Severity.CORRUPT_PAYLOAD
    assert any("decode check:" in note and "error line" in note for note in deep.notes)


@needs_ffmpeg
def test_deep_scan_leaves_a_clean_file_healthy(tmp_path):
    clip = tmp_path / "ok.mp3"
    make_mp3(clip)
    deep = diagnose(clip, hash_file=False, deep=True)
    assert deep.severity == Severity.HEALTHY
    assert "decode check: clean" in deep.notes


@needs_ffmpeg
def test_cli_deep_scan_counts_the_damage(tmp_path, capsys):
    make_mp3(tmp_path / "good.mp3")
    bad = tmp_path / "bad.mp3"
    make_mp3(bad)
    corrupt_middle(bad)

    assert main(["scan", str(tmp_path), "--deep"]) == 0
    out = capsys.readouterr().out
    assert "1 damaged" in out


@needs_ffmpeg
def test_deep_seconds_still_checks_the_opening(tmp_path):
    bad = tmp_path / "bad.mp3"
    make_mp3(bad)
    corrupt_middle(bad)
    # A capped decode is cheap; it must not crash and must still report something.
    deep = diagnose(bad, hash_file=False, deep=True, deep_seconds=1)
    assert any("decode check:" in note for note in deep.notes)


@needs_ffmpeg
def test_fix_fails_honestly_when_the_payload_is_gone(tmp_path, capsys):
    """A remux cannot invent lost audio: the tool must say so, not exit 0."""
    bad = tmp_path / "bad.mp3"
    make_mp3(bad)
    corrupt_middle(bad)
    out = tmp_path / "fixed.mp3"

    assert main(["fix", str(bad), "-o", str(out), "--deep"]) == 1
    printed = capsys.readouterr().out
    # The verdict carries the decode count, and the file it was measured on is gone: a `-o` file
    # that does not play is not something to leave lying around under a name the user chose.
    assert '"decode_errors"' in printed
    assert '"ok": false' in printed
    assert not out.exists()
    assert '"discarded"' in printed


@needs_ffmpeg
def test_fix_passes_verification_on_a_repairable_mp4(tmp_path, capsys):
    import json

    src = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=160x96:rate=10", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=44100", "-t", "4",
                    "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "64k", str(src)],
                   check=True, capture_output=True)
    data = bytearray(src.read_bytes())
    i = data.find(b"mdat") + 4
    span = int((len(data) - i) * 0.3)
    rng = random.Random(4)
    data[i:i + span] = bytes(rng.randrange(256) for _ in range(span))
    src.write_bytes(bytes(data))

    out = tmp_path / "fixed.mp4"
    assert main(["fix", str(src), "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    payload = json.loads(printed[printed.find("{"):])
    assert payload["verify"]["ok"] is True and payload["verify"]["decode_errors"] == 0
