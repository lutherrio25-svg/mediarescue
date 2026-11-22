import json
import threading
import time
import types
from typing import ClassVar

import pytest

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
        atoms: ClassVar[list] = [types.SimpleNamespace(type="mdat")]

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
        atoms: ClassVar[list] = []

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


def test_scan_of_a_reference_only_damage_does_not_double_count(tmp_path, capsys, monkeypatch):
    """One file that needs a reference is one damaged file, not three numbers to add up."""
    from mediarescue import cli
    from mediarescue.report import Severity

    class _Diagnosis:
        severity = Severity.MKV_HEADLESS
        recoverable = True
        needs_reference = True
        size = 36
        notes: ClassVar[list] = []
        atoms: ClassVar[list] = []
        gaps: ClassVar[list] = []

    (tmp_path / "a.mkv").write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 32)
    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: _Diagnosis())

    assert main(["scan", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 damaged: 0 repairable now, 1 need a reference file" in out


class _FixArgs:
    """The attributes `_repair` reads, so it can be driven without a real command line."""

    reference = None
    fps = 25
    no_verify = False
    find_source = False
    deep = False


def _repair_with(tmp_path, monkeypatch, probe_info, decode):
    from mediarescue import cli, ffmpeg

    src = tmp_path / "broken.mp4"
    src.write_bytes(b"not really a video")

    def fake_remux(_src, out, **_kw):
        out.write_bytes(b"fake fixed bytes")
        return True, ""

    monkeypatch.setattr(cli, "_resolve_strategy", lambda *a, **k: ("remux", None))
    monkeypatch.setattr(cli, "remux", fake_remux)
    monkeypatch.setattr(cli, "probe", lambda p: probe_info)
    monkeypatch.setattr(ffmpeg, "decode_check", lambda p: decode)
    return cli._repair(src, tmp_path / "broken.repaired.mp4", _FixArgs(),
                       cli.diagnose(src, hash_file=False))


def test_a_written_file_that_does_not_decode_is_not_ok(tmp_path, monkeypatch):
    """`ok` must not survive a failed decode gate: scripts read that field, not the exit code."""
    result = _repair_with(tmp_path, monkeypatch,
                          {"ok": True, "duration": 1.0, "streams": ["h264"]},
                          (False, 7, "Cannot determine format of input 0:0 after EOF"))

    assert result["ok"] is False
    assert result["verify"]["decode_errors"] == 7
    assert "does not decode cleanly" in result["reason"]


def test_a_failed_repair_leaves_no_output_behind(tmp_path, monkeypatch):
    """A `.repaired.` file that does not play must not survive: `scan` skips names the tool owns,
    so it would sit beside the original looking like a repair that worked."""
    result = _repair_with(tmp_path, monkeypatch,
                          {"ok": True, "duration": 1.0, "streams": ["h264"]},
                          (False, 7, "Cannot determine format of input 0:0 after EOF"))

    assert not (tmp_path / "broken.repaired.mp4").exists()
    assert result["discarded"].endswith("broken.repaired.mp4")
    assert result["output"] == ""
    assert (tmp_path / "broken.mp4").exists()


def test_a_good_repair_keeps_its_output(tmp_path, monkeypatch):
    """The cleanup must not touch a file that passed every gate."""
    result = _repair_with(tmp_path, monkeypatch,
                          {"ok": True, "duration": 1.0, "streams": ["h264"]},
                          (True, 0, ""))

    assert (tmp_path / "broken.repaired.mp4").exists()
    assert "discarded" not in result


def test_fix_refuses_to_write_the_repair_over_the_original(tmp_path, capsys, monkeypatch):
    """`-o <the same file>` is the one flag that could break "the original is never touched"."""
    from mediarescue import cli

    src = tmp_path / "broken.mp4"
    src.write_bytes(b"original bytes that must survive")

    class _Diagnosis:
        severity = cli.Severity.CORRUPT_PAYLOAD
        notes: ClassVar[list] = []
        atoms: ClassVar[list] = []
        gaps: ClassVar[list] = []

    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: _Diagnosis())
    monkeypatch.setattr(cli, "have_ffmpeg", lambda: True)

    assert main(["fix", str(src), "-o", str(src), "--no-color"]) == 2
    assert "names the file being repaired" in capsys.readouterr().err
    assert src.read_bytes() == b"original bytes that must survive"


def test_a_written_file_ffmpeg_cannot_open_is_not_ok(tmp_path, monkeypatch):
    """A probe that cannot read the output is the same verdict as a decode that failed."""
    result = _repair_with(tmp_path, monkeypatch,
                          {"ok": False, "duration": 0.0, "streams": []},
                          (True, 0, ""))

    assert result["ok"] is False
    assert "cannot open" in result["reason"]


def test_the_brief_line_names_the_decode_failure(tmp_path, monkeypatch, capsys):
    """The one-liner a script greps must say `failed`, never a bare filename."""
    from mediarescue import cli

    line = cli._brief_line({"ok": False, "strategy": "salvage-mkv",
                            "output": str(tmp_path / "x.webm"),
                            "reason": "the output does not decode cleanly (7 error line(s))"})

    assert line.startswith("salvage-mkv: failed")
    assert "does not decode cleanly" in line


def test_the_json_that_fix_prints_is_json(tmp_path, capsys, monkeypatch):
    """stdout of `fix` is the verdict. A prose line in front of it is a broken contract:
    `mediarescue fix clip.mp4 | jq .` used to die on line 1 of otherwise valid JSON."""
    from mediarescue import cli

    src = tmp_path / "broken.mp4"
    src.write_bytes(b"not really a video")

    class _Diagnosis:
        severity = cli.Severity.CORRUPT_PAYLOAD
        notes: ClassVar[list] = ["the head was overwritten"]
        atoms: ClassVar[list] = []
        gaps: ClassVar[list] = []

    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: _Diagnosis())
    monkeypatch.setattr(cli, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(cli, "_repair", lambda src, out, args, d: {
        "ok": False, "strategy": "salvage", "output": "",
        "reason": "no complete frame to start from"})

    assert main(["fix", str(src), "--no-color"]) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)      # raises if the diagnosis line is still on stdout
    assert payload["ok"] is False
    assert captured.err.startswith("diagnosis:")


def test_a_healthy_file_still_answers_in_the_shape_the_mode_asked_for(tmp_path, capsys,
                                                                     monkeypatch):
    """Nothing to do is an answer, not silence: an empty stdout reads as a crash to a script."""
    from mediarescue import cli

    src = tmp_path / "fine.mp4"
    src.write_bytes(b"whatever")

    class _Diagnosis:
        severity = cli.Severity.HEALTHY
        notes: ClassVar[list] = ["boxes parse, index reachable"]

    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: _Diagnosis())

    assert main(["fix", str(src), "--no-color"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["strategy"] == "none"
    assert payload["reason"].startswith("nothing to repair")

    assert main(["fix", str(src), "--brief", "--no-color"]) == 0
    line = capsys.readouterr().out.strip()
    assert line.startswith("none: nothing to repair")


def _batch_fixture(tmp_path, monkeypatch, names, delay=0.15):
    """Wire `fix` on a folder to a fake repair that takes `delay` seconds and tracks overlap."""
    from mediarescue import cli

    for name in names:
        (tmp_path / name).write_bytes(b"x")

    live: list[str] = []
    peak: list[int] = []
    lock = threading.Lock()

    def fake_repair(src, out, args, d):
        with lock:
            live.append(src.name)
            peak.append(len(live))
        time.sleep(delay)
        with lock:
            live.remove(src.name)
        out.write_bytes(b"ok")
        return {"ok": True, "strategy": "remux", "output": str(out),
                "verify": {"ok": True, "decode_errors": 0}}

    class _Diagnosis:
        severity = cli.Severity.CORRUPT_INDEX
        atoms: ClassVar[list] = []
        gaps: ClassVar[list] = []
        notes: ClassVar[list] = ["index lost"]

    monkeypatch.setattr(cli, "diagnose", lambda *a, **k: _Diagnosis())
    monkeypatch.setattr(cli, "have_ffmpeg", lambda: True)
    monkeypatch.setattr(cli, "iter_media", lambda paths: [tmp_path / n for n in names])
    monkeypatch.setattr(cli, "_repair", fake_repair)
    return peak


def test_jobs_repairs_several_files_at_once_and_keeps_the_table_order(tmp_path, capsys,
                                                                     monkeypatch):
    """The reason to ask for --jobs: wall time. The reason not to fear it: the lines still come
    out in folder order, because the pool hands results back in submission order."""
    names = ["a.mp4", "b.mp4", "c.mp4", "d.mp4"]
    peak = _batch_fixture(tmp_path, monkeypatch, names)

    started = time.monotonic()
    assert main(["fix", str(tmp_path), "--jobs", "4", "--no-color"]) == 0
    elapsed = time.monotonic() - started

    assert max(peak) >= 2, f"nothing ran at the same time: {peak}"
    assert elapsed < 0.55, f"4 x 0.15s took {elapsed:.2f}s, so it did not overlap"
    out = capsys.readouterr().out
    assert [name for name in names if name in out] == names
    assert out.index("a.mp4") < out.index("b.mp4") < out.index("c.mp4") < out.index("d.mp4")


def test_the_default_is_still_one_file_at_a_time(tmp_path, capsys, monkeypatch):
    names = ["a.mp4", "b.mp4", "c.mp4"]
    peak = _batch_fixture(tmp_path, monkeypatch, names, delay=0.05)

    assert main(["fix", str(tmp_path), "--no-color"]) == 0
    assert max(peak) == 1


@pytest.mark.parametrize("bad", ["0", "-2", "two"])
def test_jobs_rejects_what_is_not_a_positive_whole_number(tmp_path, capsys, bad):
    (tmp_path / "a.mp4").write_bytes(b"x")
    with pytest.raises(SystemExit) as caught:
        main(["fix", str(tmp_path), "--jobs", bad])
    assert caught.value.code == 2
    assert "--jobs" in capsys.readouterr().err


def test_jobs_steps_aside_when_the_search_needs_shared_state(capsys):
    from mediarescue import cli

    assert cli._effective_jobs(types.SimpleNamespace(jobs=4, find_source=True)) == 1
    assert "ignored with --find-source" in capsys.readouterr().err
    assert cli._effective_jobs(types.SimpleNamespace(jobs=4, find_source=False)) == 4


def test_a_batch_creates_the_out_dir_it_was_given(tmp_path, capsys, monkeypatch):
    """Pointing a folder at a folder that does not exist yet is a normal thing to do, and one
    file already handled it. A batch used to fail every file with a message about a staging
    file the caller never named."""
    names = ["a.mp4", "b.mp4"]
    _batch_fixture(tmp_path, monkeypatch, names, delay=0.0)
    fresh = tmp_path / "recovered" / "deep"

    assert main(["fix", str(tmp_path), "--out-dir", str(fresh), "--no-color"]) == 0

    assert fresh.is_dir()
    assert sorted(p.name for p in fresh.glob("*.repaired.mp4")) == ["a.repaired.mp4",
                                                                  "b.repaired.mp4"]
    assert "FAILED" not in capsys.readouterr().out
