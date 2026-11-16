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


def _write_check(workdir: Path) -> Check:
    try:
        with tempfile.NamedTemporaryFile(dir=workdir, prefix=".mediarescue-", delete=True) as f:
            f.write(b"ok")
        return Check("write access", True, str(workdir))
    except OSError as exc:
        return Check("write access", False, f"cannot write to {workdir}: {exc.strerror or exc}",
                     "pick another output folder with --workdir")


def _space_check(workdir: Path) -> Check:
    try:
        free = shutil.disk_usage(workdir).free
    except OSError as exc:
        return Check("disk space", False, f"cannot read free space: {exc}", "")
    human = f"{free / (1 << 30):.1f} GB free"
    if free < MIN_FREE:
        return Check("disk space", False, human, "a repair writes a second copy of the file")
    return Check("disk space", True, human)


def run_checks(workdir: Path | None = None) -> list[Check]:
    workdir = Path(workdir) if workdir else Path.cwd()
    py = ".".join(str(n) for n in sys.version_info[:3])
    checks = [
        Check("python", sys.version_info >= MIN_PYTHON, f"{py}" +
              ("" if sys.version_info >= MIN_PYTHON else f" (need {'.'.join(map(str, MIN_PYTHON))}+)"),
              "" if sys.version_info >= MIN_PYTHON else "install a newer Python"),
        _tool_check("ffmpeg", FFMPEG),
        _tool_check("ffprobe", FFPROBE),
        _write_check(workdir),
        _space_check(workdir),
    ]
    return checks


def report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [f"mediarescue {__version__} — doctor", ""]
    for c in checks:
        mark = "ok  " if c.ok else "FAIL"
        lines.append(f"  {mark}  {c.name.ljust(width)}  {c.detail}")
        if not c.ok and c.hint:
            lines.append(f"        {' ' * width}  -> {c.hint}")
    failed = [c for c in checks if not c.ok]
    lines.append("")
    if failed:
        lines.append(f"not ready: {len(failed)} of {len(checks)} checks failed")
    else:
        lines.append("ready: everything this tool needs is present")
    return "\n".join(lines)


def doctor(workdir: Path | None = None) -> tuple[int, str]:
    checks = run_checks(workdir)
    return (0 if all(c.ok for c in checks) else 1), report(checks)
