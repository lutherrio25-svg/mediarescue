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
