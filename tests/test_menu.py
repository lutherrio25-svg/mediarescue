"""The all-in-one menu: scripted sessions, no terminal required."""
import pytest

from mediarescue.cli import main
from mediarescue.menu import CHOICES, run_menu


class Session:
    """Feeds a scripted list of answers and records everything the menu printed."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts, self.output = [], []

    def read(self, prompt=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def write(self, text=""):
        self.output.append(str(text))

    @property
    def text(self):
        return "\n".join(self.output)


def test_the_menu_lists_every_choice_with_a_number():
    s = Session(["0"])
    assert run_menu(s.read, s.write) == 0
    for key, label in CHOICES:
        assert f"{key}  {label}" in s.text


def test_quit_returns_without_running_anything():
    calls = []
    s = Session(["0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert calls == []
    assert "bye" in s.text


def test_scan_choice_asks_for_a_path_and_runs_scan(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    calls = []
    s = Session(["2", str(clip), "n", "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert calls == [["scan", str(clip)]]


def test_deep_scan_choice_adds_the_flag(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    calls = []
    s = Session(["2", str(clip), "y", "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert calls == [["scan", str(clip), "--deep"]]


def test_repair_choice_passes_output_and_reference(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"\x00" * 64)
    out = tmp_path / "fixed.mp4"
    calls = []
    s = Session(["3", str(clip), str(out), str(ref), "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert calls == [["fix", str(clip), "-o", str(out), "--reference", str(ref)]]


def test_repair_without_extra_answers_uses_defaults(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    calls = []
    s = Session(["3", str(clip), "", "", "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert calls == [["fix", str(clip)]]


def test_batch_choice_uses_out_dir(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    calls = []
    s = Session(["4", str(folder), str(tmp_path / "fixed"), "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert calls == [["fix", str(folder), "--out-dir", str(tmp_path / "fixed")]]


def test_batch_choice_passes_a_reference_when_given(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    ref = tmp_path / "intact.mp4"
    ref.write_bytes(b"\x00" * 64)
    calls = []
    s = Session(["4", str(folder), "", str(ref), "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert calls == [["fix", str(folder), "--out-dir", str(folder / "recovered"),
                      "--reference", str(ref)]]


def test_a_missing_path_is_refused_without_running_anything(tmp_path):
    calls = []
    s = Session(["2", str(tmp_path / "nope.mp4"), "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert calls == []
    assert "not found" in s.text


def test_an_unknown_choice_is_reported_and_the_menu_continues():
    calls = []
    s = Session(["9", "5", "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0)
    assert "'9' is not one of the numbers above" in s.text
    assert "corrupt-index" in s.text            # choice 5 printed the damage table
    assert calls == []


def test_a_bare_enter_redraws_the_menu_without_scolding():
    """Enter alone is not a mistake; it must not print an empty-quote complaint."""
    s = Session(["", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: 0)
    assert "is not one of the numbers above" not in s.text
    assert s.text.count("1  Check this machine") == 2


def test_the_damage_help_names_every_class():
    s = Session(["5", "", "0"])
    run_menu(s.read, s.write, dispatch=lambda argv: 0)
    for severity in ("healthy", "corrupt-payload", "corrupt-index", "missing-trailer",
                     "fragmented", "missing-atoms", "headerless", "empty"):
        assert severity in s.text


def test_eof_at_the_prompt_exits_cleanly():
    s = Session([])
    assert run_menu(s.read, s.write, dispatch=lambda argv: 0) == 0


def test_a_failing_action_does_not_kill_the_menu(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    seen = []

    def dispatch(argv):
        seen.append(argv)
        return 1 if len(seen) == 1 else 0

    s = Session(["2", str(clip), "n", "", "2", str(clip), "n", "", "0"])
    assert run_menu(s.read, s.write, dispatch=dispatch) == 0
    assert len(seen) == 2


def test_bare_invocation_without_a_terminal_prints_help_and_exits_2(capsys, monkeypatch):
    """A script that runs `mediarescue` with no arguments must never block on input."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out


def test_a_real_command_is_never_hijacked_by_the_no_argument_shortcut(tmp_path, monkeypatch, capsys):
    """`main()` used to ignore sys.argv, so `mediarescue scan x` opened the menu instead."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.argv", ["mediarescue", "scan", str(tmp_path)])
    assert main() == 0
    out = capsys.readouterr().out
    assert "all-in-one menu" not in out
    assert "scanned" in out


def test_the_menu_command_exists():
    import mediarescue.cli as cli

    with pytest.raises(SystemExit):
        cli.main(["menu", "--help"])
