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


def test_fix_on_a_directory_explains_instead_of_crashing(tmp_path, capsys):
    (tmp_path / "a.mp4").write_bytes(b"\x00" * 64)
    assert main(["fix", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "is a directory" in err and "scan" in err
    assert "Traceback" not in err


def test_diagnose_on_a_directory_is_unsupported(tmp_path):
    from mediarescue.diagnose import diagnose
    from mediarescue.report import Severity

    d = diagnose(tmp_path, hash_file=False)
    assert d.severity == Severity.UNSUPPORTED
    assert "directory" in d.notes[0]
