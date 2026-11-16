"""Minimal ISO-BMFF (MP4/MOV/M4A) box reader.

Deliberately forgiving: damaged files are the input, so a malformed box is
reported instead of raising. Only the top-level walk plus a few containers
(moov/trak/mdia/minf/stbl/moof/traf) are parsed — enough to locate the sample
tables and per-sample sizes without pulling in a full demuxer.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"moof", b"traf", b"edts", b"dinf"}


@dataclass
class Box:
    type: str
    offset: int
    size: int          # full box size including header
    header: int        # 8 or 16

    @property
    def payload(self) -> int:
        return self.offset + self.header

    @property
    def end(self) -> int:
        return self.offset + self.size


def read_boxes(buf: bytes, start: int = 0, end: int | None = None) -> list[Box]:
    """Walk sibling boxes. Stops at the first structurally impossible header."""
    end = len(buf) if end is None else min(end, len(buf))
    out: list[Box] = []
    off = start
    while off + 8 <= end:
        size = struct.unpack_from(">I", buf, off)[0]
        typ = buf[off + 4:off + 8]
        header = 8
        if size == 1:
            if off + 16 > end:
                break
            size = struct.unpack_from(">Q", buf, off + 8)[0]
            header = 16
        elif size == 0:
            size = end - off
        if size < header or off + size > end:
            break
        if not all(32 <= c < 127 for c in typ):
            break
        box = Box(typ.decode("ascii"), off, size, header)
        out.append(box)
        off += size
    return out


def find(buf: bytes, *path: str, start: int = 0, end: int | None = None) -> Box | None:
    """Descend a container path, e.g. find(buf, 'moov', 'trak', 'mdia', 'minf', 'stbl')."""
    boxes = read_boxes(buf, start, end)
    for depth, name in enumerate(path):
        box = next((b for b in boxes if b.type == name), None)
        if box is None:
            return None
        if depth == len(path) - 1:
            return box
        boxes = read_boxes(buf, box.payload, box.end)
    return None


def iter_all(buf: bytes, start: int = 0, end: int | None = None, depth: int = 0):
    """Yield (Box, depth) for every reachable box, containers included."""
    if depth > 12:
        return
    for box in read_boxes(buf, start, end):
        yield box, depth
        if box.type.encode() in CONTAINERS:
            yield from iter_all(buf, box.payload, box.end, depth + 1)


def zero_ratio(buf: bytes, max_scan: int = 8 << 20) -> float:
    """Fraction of zero bytes, sampling head and tail only (large files)."""
    if not buf:
        return 1.0
    head = buf[:max_scan]
    tail = buf[-max_scan:] if len(buf) > max_scan else b""
    sample = head + tail
    return sample.count(0) / len(sample)


def read_head_tail(path: Path, n: int = 16 << 20) -> tuple[bytes, bytes]:
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(min(n, size))
        tail = head
        if size > n:
            f.seek(max(0, size - n))
            tail = f.read(n)
    return head, tail
