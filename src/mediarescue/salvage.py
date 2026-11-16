"""Recover the clean remainder of a file whose head was overwritten by foreign data.

The sample table survives such damage, so the damage boundary is knowable: walk the video
samples until the first one that parses as length-prefixed NALs containing an IDR, then
stream-copy from that timestamp. Everything after the boundary is bit-identical to the
source; nothing is re-encoded.
"""
from __future__ import annotations

import mmap
from pathlib import Path

from .ffmpeg import cut_copy, probe, remux
from .tables import first_clean_keyframe
from .trim import trim_mdat

MIN_CUT = 0.5  # below this, cutting costs more (a lost fragment) than it buys


def _keyframe_time(path: Path) -> tuple[dict | None, str]:
    try:
        with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            return first_clean_keyframe(mm), ""
    except (ValueError, OSError) as exc:  # empty file, mmap refuses
        return None, f"could not map {path.name}: {exc}"


def salvage(src: Path, out: Path) -> dict:
    notes: list[str] = []
    stage = out.with_name(out.stem + ".stage" + out.suffix)
    staged = src

    t = trim_mdat(src, stage)
    if t.get("ok"):
        staged = stage
        notes.append(f"mdat header trimmed: {t['bytes_reclaimed']:,} declared bytes were "
                     f"never written to disk")
    elif stage.exists():
        stage.unlink(missing_ok=True)

    key, warn = _keyframe_time(staged)
    if warn:
        notes.append(warn)

    result: dict = {"ok": False, "output": str(out), "notes": notes}
    if key is not None and key["time"] > MIN_CUT:
        ok, log = cut_copy(staged, out, key["time"])
        if not ok:
            result["reason"] = f"stream copy from {key['time']:.3f}s failed: {log[-300:]}"
            return result
        notes.append(f"dropped the first {key['time']:.3f}s: {key['dropped']} unreadable "
                     f"samples before the first clean "
                     f"{'keyframe' if key.get('codec') == 'av1' else 'IDR'} at sample "
                     f"#{key['index']}")
        result["dropped_seconds"] = round(key["time"], 3)
    else:
        notes.append("no clean IDR found; keeping the file whole" if key is None
                     else "file starts on a clean keyframe; no head to drop")
        ok, log = remux(staged, out)
        if not ok:
            result["reason"] = f"remux failed: {log[-300:]}"
            return result
    result["ok"] = True

    if stage.exists():
        stage.unlink(missing_ok=True)
    result["probe"] = probe(out)
    return result
