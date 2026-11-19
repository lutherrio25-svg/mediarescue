"""Pre-flight checks: can this machine actually run a repair?

Every failure this catches is one a user would otherwise meet halfway through a 300 MB repair,
after waiting, with a half-written output file. Each failing check carries the fix, not just the
verdict.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .ffmpeg import FFMPEG, FFPROBE, run

MIN_PYTHON = (3, 10)
MIN_FREE = 512 << 20  # a repair writes a second copy of the file, so keep headroom

HINTS = {
    "windows": "winget install --id Gyan.FFmpeg -e   (then open a new terminal)",
    "darwin": "brew install ffmpeg",
    "linux": "sudo apt install ffmpeg",
}


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    hint: str = ""
    optional: bool = False


def install_hint() -> str:
    key = "windows" if sys.platform.startswith("win") else (
        "darwin" if sys.platform == "darwin" else "linux")
    return HINTS[key]


def _binary(exe: str) -> str | None:
    found = shutil.which(exe)
    if found:
        return found
    p = Path(exe)
    return str(p) if p.is_file() else None


def _version(exe: str) -> str | None:
    p = run([exe, "-version"], timeout=60)
    text = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0 or not text.strip():
        return None
    # "ffmpeg version N-127043-g5a54fcf75e Copyright (c) ..." -> keep the version, not the licence
    return " ".join(text.strip().splitlines()[0].split()[:3])


def _tool_check(name: str, exe: str) -> Check:
    path = _binary(exe)
    if path is None:
        return Check(name, False, f"{exe} not found on PATH", install_hint())
    version = _version(path)
    if version is None:
        return Check(name, False, f"{path} exists but does not run", install_hint())
    return Check(name, True, version)


def _can_write(workdir: Path) -> bool:
    try:
        with tempfile.NamedTemporaryFile(dir=workdir, prefix=".mediarescue-", delete=True) as f:
            f.write(b"ok")
        return True
    except OSError:
        return False


def _scratch_folder() -> Path | None:
    """Somewhere writable for scratch files, when the working folder is read-only."""
    for candidate in (Path(tempfile.gettempdir()), Path.home()):
        if candidate.is_dir() and _can_write(candidate):
            return candidate
    return None


def _write_check(workdir: Path, explicit: bool = False) -> Check:
    """Readiness, not pedantry: a repair writes beside the source file.

    Launching Git Bash from the Start menu puts you in `C:\\Program Files\\Git`, which is
    read-only, and the old check called that "not ready" -- a beginner reading the menu would
    think the tool was broken when nothing was wrong. Only a folder the user explicitly asked for,
    or a machine with nowhere at all to write, is a real failure.
    """
    if not workdir.exists():
        return Check("write access", False, f"no such folder: {workdir}",
                     "create it, or pass --workdir with a folder that exists")
    if _can_write(workdir):
        return Check("write access", True, str(workdir))
    scratch = _scratch_folder()
    if scratch is not None and not explicit:
        # The exact scratch path is a long, often 8.3-shortened TEMP name; what matters to the
        # reader is that nothing is wrong and where their repaired copies will appear.
        return Check("write access", True,
                     f"{workdir} is read-only (normal); repairs are written beside your files")
    return Check("write access", False, f"cannot write to {workdir}",
                 "that folder is read-only -- repairs can write beside each file instead, "
                 "or point --workdir at a writable folder" if explicit else "pick another folder")


def _space_check(workdir: Path) -> Check:
    target = workdir
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        free = shutil.disk_usage(target).free
    except OSError as exc:
        return Check("disk space", False, f"cannot read free space: {exc}", "")
    human = f"{free / (1 << 30):.1f} GB free"
    if free < MIN_FREE:
        return Check("disk space", False, human, "a repair writes a second copy of the file")
    return Check("disk space", True, human)


def _optional_tool(name: str, path: str | None, gain: str, hint: str) -> Check:
    """Reported, never fatal: the tool works without it, just on a smaller set of files."""
    if path is None:
        return Check(name, False, f"not installed — {gain}", hint, optional=True)
    return Check(name, True, f"{path} — {gain}", optional=True)


def run_checks(workdir: Path | None = None) -> list[Check]:
    from .external import mp3val_path

    explicit = workdir is not None
    workdir = Path(workdir) if workdir else Path.cwd()
    py = ".".join(str(n) for n in sys.version_info[:3])
    checks = [
        Check("python", sys.version_info >= MIN_PYTHON, f"{py}" +
              ("" if sys.version_info >= MIN_PYTHON else f" (need {'.'.join(map(str, MIN_PYTHON))}+)"),
              "" if sys.version_info >= MIN_PYTHON else "install a newer Python"),
        _tool_check("ffmpeg", FFMPEG),
        _tool_check("ffprobe", FFPROBE),
        _optional_tool("mp3val", mp3val_path(),
                       "damaged MP3 frame streams can be rebuilt",
                       "download mp3val, or set MEDIARESCUE_MP3VAL to its path"),
        _write_check(workdir, explicit=explicit),
        _space_check(workdir),
    ]
    return checks


def report(checks: list[Check], colour: bool = False) -> str:
    from .ui import paint

    width = max(len(c.name) for c in checks)
    lines = [f"mediarescue {__version__} — doctor", ""]
    for c in checks:
        if c.ok:
            mark = paint("ok  ", "green", colour)
        elif c.optional:
            mark = paint("opt ", "yellow", colour)
        else:
            mark = paint("FAIL", "red", colour)
        lines.append(f"  {mark}  {c.name.ljust(width)}  {c.detail}")
        if not c.ok and c.hint:
            lines.append(f"        {' ' * width}  {paint('-> ' + c.hint, 'dim', colour)}")
    failed = [c for c in checks if not c.ok and not c.optional]
    lines.append("")
    if failed:
        lines.append(paint(f"not ready: {len(failed)} of {len(checks)} checks failed", "red", colour))
    else:
        lines.append(paint("ready: everything this tool needs is present", "green", colour))
    return "\n".join(lines)


def doctor(workdir: Path | None = None, colour: bool = False) -> tuple[int, str]:
    checks = run_checks(workdir)
    return (0 if all(c.ok or c.optional for c in checks) else 1), report(checks, colour)
