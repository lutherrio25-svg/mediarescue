"""Colour must help a human and never leak into a log file."""
import subprocess

import pytest

from mediarescue import ui
from mediarescue.cli import main
from mediarescue.ffmpeg import have_ffmpeg

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
ESC = "\033["


def test_paint_wraps_only_when_enabled():
    assert ui.paint("x", "green", True) == "\033[32mx\033[0m"
    assert ui.paint("x", "green", False) == "x"
    assert ui.paint("x", "chartreuse", True) == "x"      # unknown colour is not an error


def test_enabled_follows_isatty_and_env(monkeypatch):
    class FakeTTY:
        def isatty(self):
            return True

    class FakePipe:
        def isatty(self):
            return False

    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    assert ui.enabled(FakeTTY()) is True
    assert ui.enabled(FakePipe()) is False
    monkeypatch.setenv("NO_COLOR", "1")
    assert ui.enabled(FakeTTY()) is False
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert ui.enabled(FakePipe()) is True
    assert ui.enabled(FakePipe(), override=False) is False


def test_piped_output_has_no_escape_codes(tmp_path, capsys, monkeypatch):
    """capsys is not a tty, so this is exactly the 'redirected to a file' case."""
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    (tmp_path / "a.mp4").write_bytes(b"")
    assert main(["scan", str(tmp_path)]) == 0
    assert ESC not in capsys.readouterr().out


def test_forced_colour_and_no_color_flag(tmp_path, capsys, monkeypatch):
    (tmp_path / "a.mp4").write_bytes(b"")
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert main(["scan", str(tmp_path)]) == 0
    assert ESC in capsys.readouterr().out

    assert main(["scan", str(tmp_path), "--no-color"]) == 0
    assert ESC not in capsys.readouterr().out


def test_doctor_respects_no_color(monkeypatch, capsys):
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert main(["doctor", "--no-color"]) in (0, 1)
    assert ESC not in capsys.readouterr().out


@needs_ffmpeg
def test_batch_quiet_prints_only_the_summary(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("FORCE_COLOR", "1")
    (tmp_path / "empty.mp4").write_bytes(b"")
    assert main(["fix", str(tmp_path), "--quiet"]) == 0
    out = capsys.readouterr().out
    assert "files:" in out
    assert "nothing to rebuild" not in out and "healthy, left untouched" not in out
    assert ESC in out                       # colour still on, only the noise is gone


@needs_ffmpeg
def test_batch_verbose_prints_notes(tmp_path, capsys):
    good = tmp_path / "good.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=160x96:rate=10", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=44100", "-t", "3",
                    "-c:v", "libx264", "-g", "10", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "64k", str(good)], check=True, capture_output=True)
    bad = tmp_path / "bad.mp4"
    data = bytearray(good.read_bytes())
    i = data.find(b"mdat") + 4
    span = int((len(data) - i) * 0.35)
    data[i:i + span] = bytes(range(256)) * (span // 256) + bytes(span % 256)
    bad.write_bytes(bytes(data))

    assert main(["fix", str(tmp_path), "--verbose", "--out-dir", str(tmp_path / "o")]) == 0
    out = capsys.readouterr().out
    assert "dropped the first" in out or "notes" in out.lower() or "NAL" in out
