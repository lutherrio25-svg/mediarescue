"""The window is a shell around the command line, and these tests hold it to that.

The behaviour is tested through `gui.repair_one`/`repair_target`, which contain no tkinter, so the
suite runs on a machine without a display and without ffmpeg.
"""

import importlib
import subprocess
import sys
import types
from pathlib import Path

import pytest

from mediarescue import cli, gui


def _diagnosis(severity, notes=None):
    return types.SimpleNamespace(severity=severity, notes=notes or [], size=1,
                                 recoverable=True, needs_reference=False)


def _damaged(monkeypatch, severity=None):
    # `mediarescue.diagnose` is both a module and the function the package re-exports; the window
    # imports the function from the module, so patch the module's attribute, not the package's.
    diagnose_module = importlib.import_module("mediarescue.diagnose")

    monkeypatch.setattr(diagnose_module, "diagnose",
                        lambda path, **kwargs: _diagnosis(severity or cli.Severity.CORRUPT_PAYLOAD))


def _ffmpeg_present(monkeypatch, present=True):
    from mediarescue import ffmpeg

    monkeypatch.setattr(ffmpeg, "have_ffmpeg", lambda: present)


def test_the_window_repairs_with_the_command_lines_own_defaults(tmp_path, monkeypatch):
    """A flag added to `fix` must reach the window, so the arguments come from build_parser()."""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    ref = tmp_path / "other.mp4"
    ref.write_bytes(b"x")
    seen = {}

    def fake_repair(path, out, args, diagnosis):
        seen.update(strategy=args.strategy, fps=args.fps, max_tries=args.max_tries,
                    reference=args.reference, deep=args.deep)
        out.write_bytes(b"repaired")
        return {"ok": True, "strategy": "remux", "output": str(out), "verify": {"ok": True},
                "probe": {"ok": True}}

    _damaged(monkeypatch)
    _ffmpeg_present(monkeypatch)
    monkeypatch.setattr(cli, "_repair", fake_repair)

    result = gui.repair_one(src, reference=ref, deep=True)

    assert result["ok"] is True
    assert result["output"] == str(tmp_path / "clip.repaired.mp4")
    assert seen == {"strategy": "auto", "fps": 25.0, "max_tries": 8, "reference": str(ref),
                    "deep": True}


def test_a_healthy_file_is_skipped_instead_of_rewritten(tmp_path, monkeypatch):
    src = tmp_path / "fine.mp4"
    src.write_bytes(b"x")
    _damaged(monkeypatch, cli.Severity.HEALTHY)
    monkeypatch.setattr(cli, "_repair",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("repaired anyway")))

    result = gui.repair_one(src)

    assert result["ok"] is True
    assert result["output"] == ""
    assert "nothing to repair" in result["reason"]
    assert not (tmp_path / "fine.repaired.mp4").exists()


def test_a_repair_that_fails_its_own_check_is_deleted(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    def fake_repair(path, out, args, diagnosis):
        out.write_bytes(b"unusable")
        return {"ok": False, "strategy": "remux", "output": str(out), "verify": {"ok": False,
                "first_error": "Invalid NAL unit size"}, "probe": {"ok": True}}

    _damaged(monkeypatch)
    _ffmpeg_present(monkeypatch)
    monkeypatch.setattr(cli, "_repair", fake_repair)

    result = gui.repair_one(src)

    assert result["ok"] is False
    assert result["output"] == ""
    assert "NAL" in result["reason"]
    assert not (tmp_path / "clip.repaired.mp4").exists(), "a broken repair must not be left behind"
    assert src.read_bytes() == b"x", "the source is never touched"


def test_a_failed_repair_says_what_the_strategy_said(tmp_path, monkeypatch):
    """The gate's guess ("cannot open the file that was written") must not replace the real cause.

    A cut Matroska head can only be rebuilt from another file; the command line says so, and a
    window that says something vaguer is worse than useless to the person reading it.
    """
    src = tmp_path / "clip.mkv"
    src.write_bytes(b"x")
    truth = "a cut Matroska head can only be rebuilt from another healthy file of the same source"

    _damaged(monkeypatch)
    _ffmpeg_present(monkeypatch)
    monkeypatch.setattr(cli, "_repair",
                        lambda *a, **k: {"ok": False, "strategy": "salvage-mkv", "reason": truth})

    result = gui.repair_one(src)

    assert result["ok"] is False
    assert result["reason"] == truth


def test_an_unrepairable_file_says_why_and_runs_nothing(tmp_path, monkeypatch):
    src = tmp_path / "empty.mp4"
    src.write_bytes(b"")
    _damaged(monkeypatch, cli.Severity.EMPTY)
    monkeypatch.setattr(cli, "_repair",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran a strategy")))

    result = gui.repair_one(src)

    assert result["ok"] is False
    assert "no stream payload" in result["reason"]


def test_a_second_repair_does_not_overwrite_the_first(tmp_path):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    (tmp_path / "clip.repaired.mp4").write_bytes(b"first")

    assert gui._output_path(src).name == "clip.repaired-2.mp4"


def test_the_folder_run_reports_each_file(tmp_path, monkeypatch):
    for name in ("a.mp4", "b.mp4"):
        (tmp_path / name).write_bytes(b"x")
    _damaged(monkeypatch, cli.Severity.HEALTHY)

    lines = []
    stats = gui.repair_target(tmp_path, log=lines.append)

    assert stats["files"] == 2
    assert stats["skipped"] == 2 and stats["repaired"] == 0 and stats["failed"] == 0
    assert [line for line in lines if line.startswith("[")] == ["[1/2] a.mp4", "[2/2] b.mp4"]


def test_the_empty_folder_is_not_a_crash(tmp_path):
    lines = []
    stats = gui.repair_target(tmp_path, log=lines.append)

    assert stats["files"] == 0
    assert "no video or audio files" in lines[0]


def test_the_window_can_actually_be_built():
    """The layout is code too: a duplicate keyword here once meant the window never opened at all.

    Skipped on a machine without a display, which is what CI is -- but the local run is the one that
    catches a mistyped grid option before it becomes an exe someone double-clicks.
    """
    tkinter = pytest.importorskip("tkinter")
    from tkinter import filedialog, ttk

    try:
        probe = tkinter.Tk()
    except tkinter.TclError:
        pytest.skip("no display")
    probe.destroy()

    app = gui.App(tkinter, ttk, filedialog)
    try:
        app.root.update()
        assert app.root.title().startswith("mediarescue")
        assert app.status.get().startswith("Ready")
        # Repair is the primary action and stays dark until there is something to repair.
        assert str(app.go.cget("state")) == "disabled"
        app.target_var.set("broken.mp4")
        assert str(app.go.cget("state")) == "normal"
        assert str(app.stop_button.cget("state")) == "disabled"
        # The tabs are the four the window promises: facts, why, options, log.
        assert [app.tabs.tab(i, "text") for i in range(app.tabs.index("end"))] == [
            "Summary", "Diagnosis", "Options", "Activity Log"]
        assert app.log is not None and app.fact_vars["file"] is not None
        # No native menu bar: Windows draws that one in its own colours and it comes out white.
        assert not app.root.cget("menu")
        assert app.menubar.cget("bg") == gui.MENU
    finally:
        app.root.destroy()


def test_a_dropped_file_opens_even_with_the_gui_word_in_front(tmp_path, monkeypatch):
    """The windowed exe is called as `mediarescue-window gui <file>`, so argv[0] is not the file."""
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    seen = {}

    class FakeApp:
        def __init__(self, *args, **kwargs):
            seen["prefill"] = kwargs.get("prefill")
            self.root = types.SimpleNamespace(mainloop=lambda: None)

    monkeypatch.setattr(gui, "App", FakeApp)
    gui.main(["gui", str(src)])
    assert seen["prefill"] == str(src)


def test_importing_the_window_needs_no_display():
    """The command line imports this module on a headless box only when asked for `gui`."""
    code = "import mediarescue.gui, sys; print('tkinter' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], check=False, capture_output=True,
                         text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_the_options_on_screen_are_the_switches_the_command_line_takes(tmp_path, monkeypatch):
    """Every box in the window has to be a flag that already exists, or the window lies."""
    acceptable = set(cli.build_parser().parse_args(["fix", "x.mp4"]).__dict__)
    assert {"strategy", "deep", "fps", "max_tries"} <= acceptable
    choices = cli.build_parser().parse_args(["fix", "x.mp4"]).strategy
    assert choices == "auto"
    for _label, value in gui.STRATEGIES:
        # `fix --strategy` has choices; anything the box offers must be one of them.
        parser = cli.build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["fix", "x.mp4", "--strategy", "definitely-not-a-strategy"])
        assert value in ("auto", "remux", "graft", "flatten", "trim", "salvage", "carve",
                         "salvage-mkv")


def test_the_boxes_reach_the_repair(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    out_dir = tmp_path / "fixed"
    seen = {}

    def fake_repair(path, out, args, diagnosis):
        seen.update(strategy=args.strategy, fps=args.fps, max_tries=args.max_tries,
                    out=Path(out))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_bytes(b"repaired")
        return {"ok": True, "strategy": args.strategy, "output": str(out),
                "verify": {"ok": True}, "probe": {"ok": True}}

    _damaged(monkeypatch)
    _ffmpeg_present(monkeypatch)
    monkeypatch.setattr(cli, "_repair", fake_repair)

    result = gui.repair_one(src, strategy="remux", fps=23.976, max_tries=3, output_dir=out_dir)

    assert result["ok"] is True
    assert seen["strategy"] == "remux" and seen["fps"] == 23.976 and seen["max_tries"] == 3
    assert seen["out"] == out_dir / "clip.repaired.mp4", "Save in must decide where it lands"
    assert result["output"] == str(out_dir / "clip.repaired.mp4")


def test_the_save_in_folder_is_created_by_the_caller_and_keeps_its_own_numbering(tmp_path):
    out_dir = tmp_path / "fixed"
    out_dir.mkdir()
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")
    (out_dir / "clip.repaired.mp4").write_bytes(b"first")

    assert gui._output_path(src, out_dir) == out_dir / "clip.repaired-2.mp4"


def test_inspect_says_what_is_wrong_and_what_it_would_write(tmp_path, monkeypatch):
    src = tmp_path / "clip.mkv"
    src.write_bytes(b"x" * 2048)
    diagnose_module = importlib.import_module("mediarescue.diagnose")
    monkeypatch.setattr(
        diagnose_module, "diagnose",
        lambda path, **kwargs: types.SimpleNamespace(
            severity=cli.Severity.MKV_HEADLESS, notes=["the EBML header is gone"],
            size=2048, recoverable=True, needs_reference=True))

    facts = gui.inspect(src)

    assert facts["name"] == "clip.mkv"
    assert facts["size_text"] == "2.0 kB"
    assert facts["kind"] == "MKV"
    assert facts["severity"] == "mkv-headless"
    assert facts["needs_reference"] is True
    assert facts["notes"] == ["the EBML header is gone"]
    assert facts["would_write"] == "clip.repaired.mkv"
    assert facts["tone"] == gui.WARN


def test_inspect_survives_a_file_it_cannot_read(tmp_path):
    facts = gui.inspect(tmp_path / "gone.mp4")

    assert facts["error"] == ""            # missing file is a fact, not an exception
    assert facts["size_text"] == "—"


def test_the_progress_callback_counts_every_file(tmp_path, monkeypatch):
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).write_bytes(b"x")
    _damaged(monkeypatch, cli.Severity.HEALTHY)
    seen = []

    gui.repair_target(tmp_path, progress=lambda done, total: seen.append((done, total)))

    assert seen == [(0, 3), (1, 3), (1, 3), (2, 3), (2, 3), (3, 3)]


def test_a_finished_run_remembers_which_files_it_wrote(tmp_path, monkeypatch):
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"x")

    def fake_repair(path, out, args, diagnosis):
        Path(out).write_bytes(b"repaired")
        return {"ok": True, "strategy": "remux", "output": str(out),
                "verify": {"ok": True}, "probe": {"ok": True}}

    _damaged(monkeypatch)
    _ffmpeg_present(monkeypatch)
    monkeypatch.setattr(cli, "_repair", fake_repair)

    stats = gui.repair_target(src)

    assert stats["repaired"] == 1
    assert stats["written"] == [str(tmp_path / "clip.repaired.mp4")]
