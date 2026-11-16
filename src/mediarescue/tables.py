"""Read the sample table of a normal (non-fragmented) MP4 and judge each sample's payload.

Used by salvage: a file whose head was overwritten still has a valid moov, so the table
tells us exactly where the damage stops.
"""
from __future__ import annotations

import struct

from .mp4 import Box, find, iter_all, read_boxes


def locate_moov(buf) -> Box | None:
    """Find moov, tolerating an mdat that declares more bytes than the file holds."""
    box = find(buf, "moov")
    if box is not None:
        return box
    idx = buf.rfind(b"moov")
    while idx > 4:
        size = int.from_bytes(buf[idx - 4:idx], "big")
        if 8 <= size <= len(buf) - (idx - 4):
            return Box("moov", idx - 4, size, 8)
        idx = buf.rfind(b"moov", 0, idx)
    return None


def _child(boxes: list, name: str) -> Box | None:
    return next((b for b, _ in boxes if b.type == name), None)


def sample_table(buf, trak: Box) -> dict | None:
    """{'handler', 'timescale', 'samples': [{'offset','size','duration'}], 'chunks'}"""
    boxes = list(iter_all(buf, trak.payload, trak.end))
    hdlr, mdhd = _child(boxes, "hdlr"), _child(boxes, "mdhd")
    stsz, stsc, stco = _child(boxes, "stsz"), _child(boxes, "stsc"), _child(boxes, "stco")
    stts = _child(boxes, "stts")
    if (hdlr is None or mdhd is None or stsz is None or stsc is None
            or stco is None or stts is None):
        return None

    handler = bytes(buf[hdlr.payload + 8:hdlr.payload + 12])
    timescale = struct.unpack_from(">I", buf, mdhd.payload + 12)[0]

    uniform = struct.unpack_from(">I", buf, stsz.payload + 4)[0]
    count = struct.unpack_from(">I", buf, stsz.payload + 8)[0]
    if uniform:
        sizes = [uniform] * count
    else:
        sizes = list(struct.unpack_from(f">{count}I", buf, stsz.payload + 12))

    n = struct.unpack_from(">I", buf, stsc.payload + 4)[0]
    runs = [struct.unpack_from(">III", buf, stsc.payload + 8 + 12 * i) for i in range(n)]
    n = struct.unpack_from(">I", buf, stco.payload + 4)[0]
    chunks = list(struct.unpack_from(f">{n}I", buf, stco.payload + 8))

    offsets, si = [], 0
    for ci, chunk in enumerate(chunks, start=1):
        per_chunk = next((r[1] for r in reversed(runs) if r[0] <= ci), 1)
        pos = chunk
        for _ in range(per_chunk):
            if si >= len(sizes):
                break
            offsets.append((pos, sizes[si]))
            pos += sizes[si]
            si += 1

    durations: list[int] = []
    n = struct.unpack_from(">I", buf, stts.payload + 4)[0]
    for i in range(n):
        cnt, delta = struct.unpack_from(">II", buf, stts.payload + 8 + 8 * i)
        durations.extend([delta] * cnt)

    samples = [{"offset": o, "size": s,
                "duration": durations[i] if i < len(durations) else 0}
               for i, (o, s) in enumerate(offsets)]
    return {"handler": handler, "timescale": timescale, "samples": samples,
            "chunks": len(chunks)}


def nal_types(d: bytes) -> list[int] | None:
    """NAL unit types of a length-prefixed sample, or None when it does not tile exactly."""
    p, types = 0, []
    while p + 4 <= len(d):
        length = struct.unpack_from(">I", d, p)[0]
        if length == 0 or p + 4 + length > len(d):
            return None
        types.append(d[p + 4] & 0x1F)
        p += 4 + length
    return types if p == len(d) and types else None


def first_clean_keyframe(buf) -> dict | None:
    """Where the decodable video really starts, and how many samples were junk before it."""
    moov = locate_moov(buf)
    if moov is None:
        return None
    for trak in (b for b in read_boxes(buf, moov.payload, moov.end) if b.type == "trak"):
        table = sample_table(buf, trak)
        if not table or table["handler"] != b"vide" or not table["timescale"]:
            continue
        t, junk = 0.0, 0
        for index, sample in enumerate(table["samples"]):
            data = buf[sample["offset"]:sample["offset"] + sample["size"]]
            types = nal_types(data)
            if types is None:
                junk += 1
            elif 5 in types:  # IDR
                return {"time": t, "index": index, "dropped": junk,
                        "timescale": table["timescale"]}
            t += sample["duration"] / table["timescale"]
    return None
