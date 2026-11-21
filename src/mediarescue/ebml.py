"""Just enough EBML to walk a Matroska file.

Matroska is a tree of elements: a variable-length ID (1-4 bytes), a variable-length size
(1-8 bytes), then the payload. Read at that level, a *headless* file is not opaque — its
clusters are intact and only the header that describes them is gone. That is what makes an
MKV recoverable where `ffmpeg -c copy` refuses before it even opens the input.

Nothing here is a general parser: unknown-size elements are bounded by the next sibling or the
end of the file, and the ID set is limited to the elements a repair actually needs.
"""
from __future__ import annotations

from typing import NamedTuple


class Element(NamedTuple):
    """One EBML element, by byte offsets into the buffer it was read from."""

    id: int
    offset: int        # first byte of the ID
    payload: int       # first byte after the size field
    size: int | None   # None when the element declares "unknown size"
    end: int           # payload + size, clamped to the buffer

    @property
    def header(self) -> int:
        return self.payload - self.offset


def read_id(buf, pos: int) -> tuple[int, int]:
    """(element id, byte length) — the marker bit stays part of the ID, as the spec says."""
    if pos >= len(buf):
        return -1, 0
    first = buf[pos]
    for length, top in ((1, 0x80), (2, 0x40), (3, 0x20), (4, 0x10)):
        if first & top:
            return int.from_bytes(bytes(buf[pos:pos + length]), "big"), length
    return -1, 0


def read_size(buf, pos: int) -> tuple[int | None, int]:
    """(declared size or None for unknown, byte length); (None, 0) when unparseable."""
    if pos >= len(buf):
        return None, 0
    first = buf[pos]
    for length in range(1, 9):
        top = 0x80 >> (length - 1)
        if first & top:
            raw = int.from_bytes(bytes(buf[pos:pos + length]), "big")
            value = raw & ((1 << (7 * length)) - 1)
            unknown = value == (1 << (7 * length)) - 1
            return (None if unknown else value), length
    return None, 0


def element(buf, pos: int, limit: int | None = None) -> Element | None:
    """The element starting at `pos`, or None when the bytes there are not an element."""
    eid, ilen = read_id(buf, pos)
    if eid < 0 or not ilen:
        return None
    size, slen = read_size(buf, pos + ilen)
    if not slen:
        return None
    payload = pos + ilen + slen
    ceiling = len(buf) if limit is None else min(limit, len(buf))
    end = ceiling if size is None else min(payload + size, ceiling)
    if payload > ceiling:
        return None
    return Element(eid, pos, payload, size, end)


def children(buf, start: int, end: int | None = None):
    """Yield the elements directly inside `buf[start:end]`, in order."""
    stop = len(buf) if end is None else min(end, len(buf))
    pos = start
    while pos < stop - 1:
        child = element(buf, pos, stop)
        if child is None:
            return
        yield child
        if child.end <= pos:
            return
        pos = child.end


def uint(buf, offset: int, length: int) -> int:
    """EBML unsigned integer: big-endian, length may be anything from 1 to 8."""
    if length <= 0 or offset + length > len(buf):
        return 0
    return int.from_bytes(bytes(buf[offset:offset + length]), "big")
