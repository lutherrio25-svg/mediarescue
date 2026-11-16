"""RIFF/AVI structure checks.

AVI needs no central index: libavformat rescans the `movi` chunks, so a file whose `idx1` was cut
off still plays, and a header-only scan sees nothing wrong. The failure that does hurt is an
`idx1` whose entries point at the wrong offsets — ffmpeg then follows the bad index into the
middle of a frame and produces garbage on every seek. That case is cheap to detect, and it is
repairable with `-fflags +ignidx`, which makes the demuxer rescan instead of trusting the index.
"""
from __future__ import annotations

import struct
from pathlib import Path

MAX_ENTRIES = 20000


def check(path: str | Path) -> dict | None:
    """None when the file is not RIFF/AVI, else a verdict on its index."""
    p = Path(path)
    try:
        size = p.stat().st_size
        with p.open("rb") as fh:
            head = fh.read(12)
            if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"AVI ":
                return None

            movi_start = movi_end = None
            idx_at = idx_len = None
            pos = 12
            while pos + 8 <= size:
                fh.seek(pos)
                hdr = fh.read(12)
                if len(hdr) < 8:
                    break
                fourcc, length = hdr[:4], struct.unpack_from("<I", hdr, 4)[0]
                if fourcc == b"LIST" and hdr[8:12] == b"movi":
                    movi_start = pos + 12
                    movi_end = min(pos + 8 + length, size)
                elif fourcc == b"idx1":
                    idx_at, idx_len = pos + 8, length
                pos += 8 + length + (length & 1)

            if movi_start is None or movi_end is None:
                return {"avi": True, "movi": None, "has_index": False, "entries": 0, "bad": 0}
            if idx_at is None or idx_len is None:
                return {"avi": True, "movi": (movi_start, movi_end), "has_index": False,
                        "entries": 0, "bad": 0}

            entries, bad = _validate(fh, idx_at, idx_len, movi_start, movi_end)
            return {"avi": True, "movi": (movi_start, movi_end), "has_index": True,
                    "entries": entries, "bad": bad}
    except OSError:
        return None


def _validate(fh, idx_at: int, idx_len: int, movi_start: int, movi_end: int) -> tuple[int, int]:
    """Count entries whose target does not look like the chunk they claim to point at.

    Index offsets are relative to the `movi` FOURCC, which sits four bytes before the list data;
    files written by other muxers have been seen using the data start instead, so both bases are
    tried and the better one wins. Guessing one base would flag healthy files.
    """
    entries = min(idx_len // 16, MAX_ENTRIES)
    if entries <= 0:
        return 0, 0
    fh.seek(idx_at)
    raw = fh.read(entries * 16)
    parsed = [struct.unpack_from("<4sIII", raw, i * 16) for i in range(len(raw) // 16)]

    best = None
    for base in (movi_start - 4, movi_start):
        bad = sum(1 for cid, _flags, off, csize in parsed
                  if not _plausible(fh, base + off, cid, csize, movi_start, movi_end))
        if best is None or bad < best:
            best = bad
    return len(parsed), int(best or 0)


def _plausible(fh, target: int, chunk_id: bytes, chunk_size: int,
               movi_start: int, movi_end: int) -> bool:
    if target < movi_start or target + 8 > movi_end:
        return False
    fh.seek(target)
    hdr = fh.read(8)
    if len(hdr) < 8:
        return False
    if hdr[:4] != chunk_id:
        return False
    return struct.unpack_from("<I", hdr, 4)[0] == chunk_size
