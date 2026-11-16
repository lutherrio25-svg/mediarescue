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
    assert "1 files: 0 repaired, 0 failed, 1 skipped" in out
    assert "Traceback" not in out


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
