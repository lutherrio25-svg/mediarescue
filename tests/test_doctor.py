"""Doctor must say 'ready' only when a repair could actually run."""
from pathlib import Path

import pytest

from mediarescue import doctor as doctor_mod
from mediarescue.ffmpeg import have_ffmpeg

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


@needs_ffmpeg
def test_ready_when_everything_is_present(tmp_path):
    code, text = doctor_mod.doctor(tmp_path)
    assert code == 0, text
    assert "ready: everything this tool needs is present" in text
    assert "FAIL" not in text


def test_missing_ffmpeg_fails_with_an_install_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor_mod, "FFMPEG", "definitely-not-ffmpeg")
    code, text = doctor_mod.doctor(tmp_path)
    assert code == 1
    assert "FAIL  ffmpeg" in text
    assert "->" in text  # the hint, not just the verdict
    assert "not ready: 1 of" in text


def test_unwritable_workdir_is_caught(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor_mod, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(doctor_mod, "FFPROBE", "ffprobe")
    code, text = doctor_mod.doctor(Path(tmp_path) / "does-not-exist")
    assert code == 1
    assert "FAIL  write access" in text


def test_report_never_claims_ready_with_a_failure():
    checks = [doctor_mod.Check("a", True, "fine"), doctor_mod.Check("b", False, "broken", "fix it")]
    text = doctor_mod.report(checks)
    assert "ready: everything" not in text
    assert "not ready: 1 of 2 checks failed" in text
