"""Optional external repair tools.

Every one of them is an upgrade, never a requirement: if the binary is missing the tool keeps
working with what it has, and says so. `MEDIARESCUE_MP3VAL` overrides the lookup for people who
keep it outside PATH.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def find_tool(name: str, env_var: str) -> str | None:
    override = os.environ.get(env_var)
    if override:
        candidate = Path(override)
        return str(candidate) if candidate.exists() else None
    return shutil.which(name)


def mp3val_path() -> str | None:
    return find_tool("mp3val", "MEDIARESCUE_MP3VAL")


def run_mp3val(tool: str, path: Path, timeout: int = 900) -> tuple[bool, str]:
    """`mp3val -f -nb` rewrites the file in place, so callers must pass a copy."""
    try:
        p = subprocess.run([tool, "-f", "-nb", str(path)], capture_output=True, text=True,
                           timeout=timeout, errors="replace", check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    log = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, log[-600:]
