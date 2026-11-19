import types

from mediarescue.cli import main


def test_scan_json(tmp_path, capsys):
    (tmp_path / "a.mp4").write_bytes(b"")
    (tmp_path / "b.mp4").write_bytes(b"\x00" * 4096)
    assert main(["scan", str(tmp_path), "--json"]) == 0
    assert '"severity"' in capsys.readouterr().out


def test_fix_refuses_empty(tmp_path, capsys):
    p = tmp_path / "empty.mp4"
    p.write_bytes(b"")
    assert main(["fix", str(p)]) == 2
    assert not list(tmp_path.glob("*.repaired.*"))


def test_fix_on_a_directory_runs_a_batch(tmp_path, capsys):
    (tmp_path / "a.mp4").write_bytes(b"\x00" * 64)      # unrepairable, skipped
    assert main(["fix", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 files: 0 repaired, 1 skipped" in out
    assert "Traceback" not in out


def test_a_batch_never_touches_its_own_previous_output(tmp_path, capsys):
    (tmp_path / "a.mp4").write_bytes(b"\x00" * 64)                # unrepairable, skipped
    (tmp_path / "a.repaired.mp4").write_bytes(b"\x00" * 64)       # this tool's own output
    (tmp_path / "a.repaired.stage.mp4").write_bytes(b"\x00" * 64)
    assert main(["fix", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 files: 0 repaired, 1 skipped" in out


def test_a_batch_skips_files_that_would_land_inside_the_output_folder(tmp_path, capsys):
    out_dir = tmp_path / "recovered"
    out_dir.mkdir()
    (tmp_path / "a.mp4").write_bytes(b"\x00" * 64)      # unrepairable, skipped
    (out_dir / "b.mp4").write_bytes(b"\x00" * 64)       # inside --out-dir: invisible to the batch
    assert main(["fix", str(tmp_path), "--out-dir", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "1 files: 0 repaired, 1 skipped" in out


def test_a_batch_separates_needs_reference_from_failed(tmp_path, capsys, monkeypatch):
    """The red FAILED is for real dead ends; a file one intact sibling away from repairable is
    a different, actionable state — the summary must say which and what a reference even is."""
    from mediarescue import cli

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"garbage that reads as nothing")

    class _Diagnosis:
        severity = cli.Severity.MISSING_ATOMS
        needs_reference = True
        atoms = [types.SimpleNamespace(type="mdat")]

    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: _Diagnosis())
    monkeypatch.setattr(cli, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(cli, "_repair",
                        lambda src, out, args, d: {"ok": False, "strategy": "graft",
                                                   "reason": "no SPS/PPS without a --reference file"})

    assert main(["fix", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "need-ref" in out
    assert "need a reference file" in out
    assert "FAILED" not in out
    assert "how to fix the other 1" in out
    assert "--reference" in out


def test_a_file_with_no_readable_payload_is_a_real_failure(tmp_path, capsys, monkeypatch):
    """`missing-atoms` usually means "one reference away", but not when not a single box parses:
    there is nothing for a reference to be grafted onto, so promising one would be a lie."""
    from mediarescue import cli

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"garbage that reads as nothing")

    class _Diagnosis:
        severity = cli.Severity.MISSING_ATOMS
        needs_reference = True
        atoms = []

    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: _Diagnosis())
    monkeypatch.setattr(cli, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(cli, "_repair",
                        lambda src, out, args, d: {"ok": False, "strategy": "graft",
                                                   "reason": "damaged file has no top-level mdat"})

    assert main(["fix", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "no top-level mdat" in out
    assert "need-ref" not in out


def test_a_batch_with_nothing_left_out_is_exit_zero(tmp_path, capsys, monkeypatch):
    """need-ref is no longer silently green: it is a real gap, so the batch keeps exit 1."""
    from mediarescue import cli

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"garbage that reads as nothing")

    class _Diagnosis:
        severity = cli.Severity.MISSING_ATOMS
        needs_reference = True

    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: _Diagnosis())
    monkeypatch.setattr(cli, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(cli, "_repair",
                        lambda src, out, args, d: {"ok": True, "output": str(out),
                                                   "strategy": "graft"})
    assert main(["fix", str(tmp_path)]) == 0


def test_diagnose_on_a_directory_is_unsupported(tmp_path):
    from mediarescue.diagnose import diagnose
    from mediarescue.report import Severity

    d = diagnose(tmp_path, hash_file=False)
    assert d.severity == Severity.UNSUPPORTED
    assert "directory" in d.notes[0]


def test_fix_and_scan_refuse_a_path_that_does_not_exist(tmp_path, capsys):
    """Silently doing nothing and exiting 0 is the worst possible answer."""
    ghost = str(tmp_path / "tidak-ada.mp4")
    assert main(["fix", ghost]) == 2
    assert "not found" in capsys.readouterr().err

    assert main(["scan", ghost]) == 2
    assert "not found" in capsys.readouterr().err


def test_scan_of_an_empty_folder_is_not_an_error(tmp_path, capsys):
    assert main(["scan", str(tmp_path)]) == 0
    assert "0 scanned" in capsys.readouterr().out
