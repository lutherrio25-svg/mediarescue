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


def test_a_missing_optional_tool_is_reported_but_not_fatal():
    checks = [doctor_mod.Check("a", True, "fine"),
              doctor_mod.Check("mp3val", False, "not installed", "install it", optional=True)]
    text = doctor_mod.report(checks)
    assert "opt " in text and "FAIL" not in text
    assert "ready: everything" in text


def test_mp3val_is_listed_in_the_checks(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor_mod, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(doctor_mod, "FFPROBE", "ffprobe")
    checks = doctor_mod.run_checks(tmp_path)
    mp3 = [c for c in checks if c.name == "mp3val"]
    assert mp3 and mp3[0].optional
    # A machine without mp3val is still ready: it just repairs fewer formats.
    code, text = doctor_mod.doctor(tmp_path)
    assert code == 0, text


def test_a_read_only_working_folder_is_not_a_failure(monkeypatch, tmp_path):
    """Git Bash started from the Start menu sits in C:\\Program Files\\Git, which is read-only.

    Repairs are written beside each source file, so that must not read as "not ready" -- it told a
    beginner the tool was broken when nothing was wrong.
    """
    monkeypatch.setattr(doctor_mod, "_can_write", lambda p: False)
    monkeypatch.setattr(doctor_mod, "_scratch_folder", lambda: tmp_path)
    check = doctor_mod._write_check(tmp_path, explicit=False)
    assert check.ok
    assert "read-only" in check.detail and "beside your files" in check.detail


def test_a_folder_the_user_asked_for_must_be_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor_mod, "_can_write", lambda p: False)
    monkeypatch.setattr(doctor_mod, "_scratch_folder", lambda: tmp_path)
    check = doctor_mod._write_check(tmp_path, explicit=True)
    assert not check.ok
    assert "read-only" in check.hint


def test_a_missing_folder_says_so_instead_of_blaming_permissions(tmp_path):
    check = doctor_mod._write_check(tmp_path / "typo", explicit=True)
    assert not check.ok
    assert "no such folder" in check.detail


def test_a_read_only_current_directory_still_reports_ready(monkeypatch, tmp_path):
    import pathlib

    monkeypatch.setattr(doctor_mod, "FFMPEG", "ffmpeg")
    monkeypatch.setattr(doctor_mod, "FFPROBE", "ffprobe")
    monkeypatch.setattr(pathlib.Path, "cwd", classmethod(lambda cls: tmp_path / "Program Files"))
    monkeypatch.setattr(doctor_mod, "_can_write",
                        lambda p: str(p) == str(tmp_path))
    monkeypatch.setattr(doctor_mod, "_scratch_folder", lambda: tmp_path)
    monkeypatch.setattr(doctor_mod, "_space_check", lambda w: doctor_mod.Check("disk space", True, "60 GB free"))
    (tmp_path / "Program Files").mkdir()
    code, text = doctor_mod.doctor(None)
    assert code == 0, text
    assert "read-only" in text
    assert "ready: everything" in text
