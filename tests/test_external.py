"""mp3val integration: an optional backend, used when present and reported when absent."""
import random
import subprocess

import pytest

from mediarescue import external
from mediarescue.cli import main
from mediarescue.ffmpeg import have_ffmpeg

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def make_mp3(path, seconds=3):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=44100", "-t", str(seconds),
                    "-c:a", "libmp3lame", str(path)], check=True, capture_output=True)


def damage(path, fraction=0.4, seed=4):
    data = bytearray(path.read_bytes())
    lo = int(len(data) * 0.3)
    span = int(len(data) * fraction)
    rng = random.Random(seed)
    data[lo:lo + span] = bytes(rng.randrange(256) for _ in range(span))
    path.write_bytes(bytes(data))


def test_find_tool_honours_the_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "mp3val.exe"
    fake.write_bytes(b"")
    monkeypatch.setenv("MEDIARESCUE_MP3VAL", str(fake))
    assert external.mp3val_path() == str(fake)

    monkeypatch.setenv("MEDIARESCUE_MP3VAL", str(tmp_path / "nope.exe"))
    assert external.mp3val_path() is None


@needs_ffmpeg
def test_damaged_mp3_uses_mp3val_when_it_is_available(tmp_path, monkeypatch, capsys):
    clip = tmp_path / "bad.mp3"
    make_mp3(clip)
    damage(clip)

    monkeypatch.setattr(external, "mp3val_path", lambda: "mp3val")
    monkeypatch.setattr(external, "run_mp3val", lambda tool, path, timeout=900: (True, "FIXED"))
    out = tmp_path / "fixed.mp3"

    # The stub "repairs" nothing, so verification must still fail the run: the tool cannot claim
    # success just because the external binary exited 0.
    assert main(["fix", str(clip), "-o", str(out), "--deep"]) == 1
    printed = capsys.readouterr().out
    assert '"strategy": "mp3val"' in printed
    assert '"decode_errors"' in printed


@needs_ffmpeg
def test_missing_mp3val_is_reported_in_the_notes(tmp_path, monkeypatch, capsys):
    clip = tmp_path / "bad.mp3"
    make_mp3(clip)
    damage(clip)

    monkeypatch.setattr(external, "mp3val_path", lambda: None)
    out = tmp_path / "fixed.mp3"
    assert main(["fix", str(clip), "-o", str(out), "--deep"]) == 1
    printed = capsys.readouterr().out
    assert "mp3val is not installed" in printed
