"""Repair an MP4 whose mdat declares more bytes than the file actually holds.

Typical cause: an interrupted copy/download. The moov written at the end is
fine but unreachable, because parsers honour the oversized mdat and skip past
EOF. Shrinking the mdat header to the real payload makes the moov visible
again — no re-encode, and the source file is left untouched.
"""
from __future__ import annotations

import struct
from pathlib import Path

from .mp4 import find, read_boxes


def find_tail_moov(path: Path, tail: bytes) -> int | None:
    """Absolute offset of a well-formed moov inside the tail buffer, else None."""
    size = path.stat().st_size
    base = size - len(tail)
    start = 0
    while True:
        i = tail.find(b"moov", start)
        if i < 4:
            return None
        off = i - 4
        declared = struct.unpack_from(">I", tail, off)[0]
        if 8 <= declared <= len(tail) - off:
            candidate = tail[off:off + declared]
            if (find(candidate, "moov", "mvhd") is not None
                    or find(candidate, "moov", "trak") is not None):
                return base + off
        start = i + 4


def trim_mdat(damaged: Path, out: Path) -> dict:
    size = damaged.stat().st_size
    with damaged.open("rb") as f:
        head = f.read(min(size, 16 << 20))
        mdat = next((b for b in read_boxes(head) if b.type == "mdat"), None)
        if mdat is None:
            # oversized mdat is rejected by the strict walker; look for it raw
            i = head.find(b"mdat")
            if i < 4:
                return {"ok": False, "reason": "no mdat header found"}
            declared = struct.unpack_from(">I", head, i - 4)[0]
            from .mp4 import Box
            mdat = Box("mdat", i - 4, declared, 8)
        f.seek(max(0, size - (16 << 20)))
        tail = f.read()
    moov_off = find_tail_moov(damaged, tail)
    if moov_off is None:
        return {"ok": False, "reason": "no moov in tail; nothing to expose"}
    real_size = moov_off - mdat.offset
    if real_size < mdat.header:
        return {"ok": False, "reason": "computed mdat size is not positive"}
    if real_size == mdat.size:
        return {"ok": False, "reason": "mdat size already matches; not a truncation case"}

    out.parent.mkdir(parents=True, exist_ok=True)
    with damaged.open("rb") as src, out.open("wb") as dst:
        prefix = src.read(mdat.offset)
        dst.write(prefix)
        dst.write(struct.pack(">I4s", real_size, b"mdat"))
        src.seek(mdat.offset + mdat.header)  # skip the original (wrong) header
        remaining = size - mdat.offset - mdat.header
        left = remaining
        while left > 0:
            chunk = src.read(min(8 << 20, left))
            if not chunk:
                break
            dst.write(chunk)
            left -= len(chunk)
    return {"ok": True, "output": str(out), "mdat_declared": mdat.size,
            "mdat_actual": real_size, "moov_offset": moov_off,
            "bytes_reclaimed": mdat.size - real_size}
