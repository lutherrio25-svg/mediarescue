"""What the polish changed: a framed menu, arrow keys, honest answers, and no wasted rewrites."""
import subprocess
import sys

import pytest

from mediarescue import menu as menu_mod
from mediarescue import ui
from mediarescue.cli import main
from mediarescue.ffmpeg import have_ffmpeg
from mediarescue.menu import CHOICES, run_menu

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


class Session:
    def __init__(self, answers=()):
        self.answers = list(answers)
        self.output = []
        self.prompts = []

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


def scripted_keys(seq):
    """Feed arrow/enter tokens to the menu without a terminal."""
    queue = list(seq)

    def read():
        if not queue:
            return "quit"
        return queue.pop(0)
    return read


def make_clip(path, seconds=1):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=64x64:rate=10", "-f", "lavfi",
                    "-i", "sine=frequency=440", "-t", str(seconds),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
                   check=True, capture_output=True)


def make_avi_without_index(path, seconds=2):
    """An AVI whose idx1 was cut off: scan calls it corrupt-index and repairable."""
    clip = path.with_suffix(".tmp.avi")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=64x64:rate=10", "-t", str(seconds),
                    "-c:v", "mpeg4", str(clip)], check=True, capture_output=True)
    data = clip.read_bytes()
    at = data.find(b"idx1")
    path.write_bytes(data[:at] if at > 0 else data)
    clip.unlink(missing_ok=True)


# --- the frame --------------------------------------------------------------------------------

def test_the_menu_is_framed_and_names_the_version():
    s = Session(["0"])
    run_menu(s.read, s.write, dispatch=lambda argv: 0)
    text = s.text
    assert menu_mod._glyphs()["tl"] in text            # a real corner, not a bare list
    assert "mediarescue" in text and "originals are never touched" in text


def test_every_choice_carries_a_plain_language_hint():
    s = Session(["0"])
    run_menu(s.read, s.write, dispatch=lambda argv: 0)
    for key, _ in CHOICES:
        assert menu_mod.HINTS[key] in s.text


def test_the_menu_fits_an_80_column_terminal():
    """The frame is as wide as its longest row: one long hint and the menu wraps on a stock
    terminal, which is the shape the tool ships to."""
    paint = ui.colour_for(False)
    rows = menu_mod._header(paint) + menu_mod._plain_choices(1) + [menu_mod._footer(paint, True)]
    widest = max(rows, key=len)
    assert len(widest) <= 80, widest
    help_lines = menu_mod.DAMAGE_HELP.split("\n")
    assert max(len(line) for line in help_lines) <= 80, max(help_lines, key=len)


def test_the_hint_column_does_not_shift_on_the_highlighted_row():
    """The highlight is a background, not an indent: it used to push its own row two columns right,
    so the whole menu looked ragged on the row the eye is drawn to."""
    import re
    paint = ui.colour_for(True)
    rows = [re.sub(r"\033\[[0-9;]*m", "", row) for row in menu_mod._choice_lines(paint, 3)]
    starts = {row.index(menu_mod.HINTS[key]) for row, (key, _) in zip(rows, CHOICES, strict=True)}
    assert len(starts) == 1, starts


def test_the_frame_degrades_to_ascii_on_a_legacy_code_page(monkeypatch):
    """A cp437 console would raise UnicodeEncodeError on box drawing and kill the menu."""
    class Legacy:
        encoding = "cp437"

    monkeypatch.setattr("sys.stdout", Legacy, raising=False)
    s = Session(["0"])
    run_menu(s.read, s.write, dispatch=lambda argv: 0)
    assert "+---" in s.text and "│" not in s.text


# --- arrow keys -------------------------------------------------------------------------------

def test_arrows_move_the_highlight_and_enter_runs_the_choice(tmp_path):
    folder = tmp_path / "broken"
    folder.mkdir()
    calls = []
    s = Session([str(folder), "", ""])            # path, out-dir default, press Enter
    keys = scripted_keys(["down", "down", "enter", "quit"])   # 1 -> 2 -> 3? see below
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0, keys=keys)
    # two downs from the first row lands on "Repair one file"
    assert calls and calls[0][0] == "fix"


def test_a_digit_jumps_straight_to_its_row_in_arrow_mode(tmp_path):
    calls = []
    s = Session([])
    run_menu(s.read, s.write, dispatch=lambda argv: calls.append(argv) or 0,
             keys=scripted_keys(["1", "enter", "quit"]))
    assert calls == [["doctor"]]


def test_arrow_mode_prints_the_key_hints(tmp_path):
    s = Session([])
    run_menu(s.read, s.write, dispatch=lambda argv: 0, keys=scripted_keys(["quit"]))
    assert "move" in s.text and "Enter choose" in s.text
    assert "type a number" not in s.text


def test_typed_mode_still_works_without_keys():
    s = Session(["0"])
    run_menu(s.read, s.write, dispatch=lambda argv: 0, keys=None)
    assert "type a number" in s.text


def test_the_highlighted_row_is_one_solid_bar():
    """A reverse span wrapped around coloured spans loses its highlight at the first reset."""
    lines = menu_mod._choice_lines(ui.colour_for(True), 2)
    row = lines[2]
    assert row.count("\033[") == 2               # one span: open and close
    assert row.startswith("\033[7m") and row.endswith("\033[0m")
    for index, line in enumerate(lines):
        if index != 2:
            assert "\033[7m" not in line         # only one row may be highlighted
            assert "\033[33m" in line            # every other row still numbers itself


def test_the_highlight_lines_up_with_the_box_above_it():
    paint = ui.colour_for(True)
    box = _frame_header_width(paint)
    row = menu_mod._choice_lines(paint, 2)[2]
    import re
    visible = re.sub(r"\033\[[0-9;]*m", "", row)
    assert len(visible) == box


def _frame_header_width(paint):
    import re
    top = menu_mod._header(paint)[0]
    return len(re.sub(r"\033\[[0-9;]*m", "", top))


# --- scan output ------------------------------------------------------------------------------

@needs_ffmpeg
def test_scan_prints_a_column_header_and_a_next_step(tmp_path, capsys):
    make_avi_without_index(tmp_path / "a.avi")
    assert main(["scan", str(tmp_path), "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "severity" in out and "bytes" in out and "file" in out
    assert "next: mediarescue fix" in out


def test_scan_of_unrepairable_junk_says_nothing_can_be_done(tmp_path, capsys):
    (tmp_path / "a.mp4").write_bytes(b"\x00" * 4096)
    assert main(["scan", str(tmp_path), "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "nothing here can be rebuilt" in out
    assert "next:" not in out


def test_scan_of_a_clean_folder_says_so(tmp_path, capsys):
    (tmp_path / "readme.txt").write_text("nothing to see")
    assert main(["scan", str(tmp_path), "--no-color"]) == 0
    out = capsys.readouterr().out
    assert "no video or audio files" in out
    assert "next:" not in out


# --- fix on a healthy file --------------------------------------------------------------------

@needs_ffmpeg
def test_a_healthy_file_is_not_rewritten(tmp_path, capsys):
    src = tmp_path / "good.mp4"
    make_clip(src)
    out = tmp_path / "out.mp4"
    assert main(["fix", str(src), "-o", str(out), "--no-color"]) == 0
    assert "nothing to repair" in capsys.readouterr().out
    assert not out.exists()


@needs_ffmpeg
def test_force_still_rewrites_a_healthy_file(tmp_path, capsys):
    src = tmp_path / "good.mp4"
    make_clip(src)
    out = tmp_path / "out.mp4"
    assert main(["fix", str(src), "-o", str(out), "--force", "--no-color"]) == 0
    assert out.exists()


# --- the menu the user actually gets -----------------------------------------------------------

def test_the_menu_gets_a_live_key_reader(monkeypatch):
    """`run_menu()` with no reader can only be driven by typing a number; the arrow keys live
    behind make_key_reader(), so cmd_menu has to pass it."""
    seen = {}
    monkeypatch.setattr(menu_mod, "make_key_reader", lambda: "READER")
    monkeypatch.setattr(menu_mod, "run_menu", lambda **kw: seen.update(kw) or 0)

    from mediarescue import cli

    assert cli.cmd_menu(None) == 0
    assert seen["keys"] == "READER"


def test_no_key_reader_off_a_terminal():
    """Under pytest stdin is not a tty, so the reader must bow out instead of blocking."""
    from mediarescue.menu import make_key_reader

    assert make_key_reader() is None


def test_the_bare_command_line_opens_the_menu(monkeypatch):
    """`mediarescue` with nothing after it is a terminal user who has not read the README."""
    class Tty:
        def isatty(self):
            return True

        def write(self, text):
            return len(text)

        def flush(self):
            pass

    seen = {}
    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(sys, "stdout", Tty())
    monkeypatch.setattr(menu_mod, "make_key_reader", lambda: "READER")
    monkeypatch.setattr(menu_mod, "run_menu", lambda **kw: seen.update(kw) or 0)

    assert main([]) == 0
    assert seen["keys"] == "READER"


def test_a_script_gets_help_instead_of_a_menu(capsys):
    """Nothing may block on input when there is no terminal on either end."""
    assert main([]) == 2
    assert "usage: mediarescue" in capsys.readouterr().out


# --- small things people type ----------------------------------------------------------------

def test_python_dash_m_reaches_the_same_tool():
    out = subprocess.run([sys.executable, "-m", "mediarescue", "--version"],
                         capture_output=True, text=True, check=False)
    assert out.returncode == 0
    assert out.stdout.startswith("mediarescue ")


def test_version_says_what_it_is(capsys):
    with pytest.raises(SystemExit) as stop:
        main(["--version"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.startswith("mediarescue ")


# --- --out-dir means the same thing for one file ----------------------------------------------

@needs_ffmpeg
def test_out_dir_is_honoured_for_a_single_file(tmp_path, capsys):
    """`fix file.avi --out-dir out` used to write beside the source and leave `out` empty."""
    from tests.test_avi import make_avi, shift_entries

    src = tmp_path / "clip.avi"
    make_avi(src)
    shift_entries(src)          # a real repair, so the healthy short-circuit cannot mask it
    out = tmp_path / "collected"
    assert main(["fix", str(src), "--out-dir", str(out), "--no-color"]) == 0
    assert (out / "clip.repaired.avi").exists()
    assert not (tmp_path / "clip.repaired.avi").exists()
