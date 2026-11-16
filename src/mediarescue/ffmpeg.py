"""External tool runners. Every repair writes to a NEW path; nothing is in-place."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

FFMPEG = os.environ.get("MEDIARESCUE_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("MEDIARESCUE_FFPROBE", "ffprobe")


def have_ffmpeg() -> bool:
    return shutil.which(FFMPEG) is not None and shutil.which(FFPROBE) is not None


def run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace")


def remux(src: Path, dst: Path, timeout: int = 1800) -> tuple[bool, str]:
    """Container-level rewrite, no re-encode. Survives a missing/truncated moov
    only when the stream is otherwise indexable; used for the cheap wins."""
    if dst.exists():
        dst = dst.with_name(dst.stem + "-1" + dst.suffix)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-hide_banner", "-nostdin", "-y",
        "-err_detect", "ignore_err",
        "-fflags", "+genpts+igndts+discardcorrupt",
        "-i", str(src),
        "-map", "0", "-c", "copy",
        "-movflags", "+faststart",
        str(dst),
    ]
    p = run(cmd, timeout)
    ok = p.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    if not ok:
        dst.unlink(missing_ok=True)
    return ok, (p.stderr or "")[-2000:]


def probe(path: Path, timeout: int = 120) -> dict:
    """Small JSON probe used to validate repaired output."""
    import json

    p = run([
        FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ], timeout)
    if p.returncode != 0:
        return {"ok": False, "error": (p.stderr or "")[-500:]}
    try:
        data = json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "error": "unparseable ffprobe output"}
    streams = data.get("streams", [])
    return {
        "ok": bool(streams),
        "duration": float(data.get("format", {}).get("duration", 0) or 0),
        "streams": [s.get("codec_name") for s in streams],
        "bytes": int(data.get("format", {}).get("size", 0) or 0),
    }
