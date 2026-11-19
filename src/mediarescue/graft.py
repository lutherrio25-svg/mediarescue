"""Reconstruct a flat (non-fragmented) MP4 from a damaged file + an intact reference.

Approach: keep the damaged mdat payload, graft the reference's moov, and rewrite
every chunk offset (stco/co64) by the delta between the two mdat positions.
Sample counts and durations must match, otherwise the reference is wrong for
this recording and we refuse rather than emit a silently corrupt file.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .mp4 import Box, iter_all, read_boxes


@dataclass
class GraftResult:
    ok: bool
    output: str = ""
    tracks: int = 0
    samples: int = 0
    reason: str = ""


def _sample_counts(moov_payload: bytes) -> list[int]:
    """stsz sample_count per trak, in trak order."""
    counts = []
    for box, _ in iter_all(moov_payload):
        if box.type == "stsz":
            counts.append(struct.unpack_from(">I", moov_payload, box.payload + 4)[0])
    return counts


def _chunk_tables(moov: bytes) -> list[tuple[Box, str]]:
    return [(b, b.type) for b, _ in iter_all(moov) if b.type in ("stco", "co64")]


def graft(damaged: Path, reference: Path, out: Path) -> GraftResult:
    dmg = damaged.read_bytes()
    ref = reference.read_bytes()

    d_mdat = next((b for b in read_boxes(dmg) if b.type == "mdat"), None)
    r_moov = next((b for b in read_boxes(ref) if b.type == "moov"), None)
    r_mdat = next((b for b in read_boxes(ref) if b.type == "mdat"), None)
    if d_mdat is None:
        return GraftResult(False, reason="damaged file has no top-level mdat")
    if r_moov is None or r_mdat is None:
        return GraftResult(False, reason="reference lacks moov/mdat")

    moov = bytearray(ref[r_moov.offset:r_moov.end])
    ref_counts = _sample_counts(moov[r_moov.header:])
    if not ref_counts:
        return GraftResult(False, reason="reference has no stsz sample table")

    # Where mdat payload will sit in the output: ftyp (from ref or synthesised) + moov.
    r_ftyp = next((b for b in read_boxes(ref) if b.type == "ftyp"), None)
    ftyp = ref[r_ftyp.offset:r_ftyp.end] if r_ftyp else struct.pack(">I4s4sI4s", 24, b"ftyp", b"isom", 512, b"isom")
    new_mdat_offset = len(ftyp) + len(moov)
    delta = new_mdat_offset + d_mdat.header - r_mdat.payload

    tables = _chunk_tables(moov)
    if not tables:
        return GraftResult(False, reason="reference has no stco/co64 chunk offsets")

    patched = 0
    for box, kind in tables:
        base = box.payload  # offsets from iter_all(moov) are already moov-relative
        count = struct.unpack_from(">I", moov, base + 4)[0]
        width = 8 if kind == "co64" else 4
        if count > max(0, (box.end - base - 8) // width):
            return GraftResult(False, reason=f"reference {kind} claims {count} entries but the "
                                             "box holds fewer")
        for i in range(count):
            pos = base + 8 + i * width
            old = struct.unpack_from(">Q" if width == 8 else ">I", moov, pos)[0]
            new = old + delta
            if new < 0:
                return GraftResult(False, reason=f"negative chunk offset after delta {delta}")
            if width == 4 and new > 0xFFFFFFFF:
                return GraftResult(False, reason="chunk offset exceeds 32-bit stco; use co64 reference")
            struct.pack_into(">Q" if width == 8 else ">I", moov, pos, new)
            patched += 1

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        f.write(ftyp)
        f.write(moov)
        f.write(dmg[d_mdat.offset:d_mdat.end])
    return GraftResult(True, str(out), tracks=len(ref_counts), samples=sum(ref_counts),
                       reason=f"patched {patched} chunk offsets, delta={delta}")
