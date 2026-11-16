"""Read the sample table of a normal (non-fragmented) MP4 and judge each sample's payload.

Used by salvage: a file whose head was overwritten still has a valid moov, so the table
tells us exactly where the damage stops.
"""
from __future__ import annotations

import mmap
import struct
from typing import cast

from .av1 import av1_is_keyframe, is_av1_sample
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


def head_is_junk(path) -> bool:
    """True when the first video sample does not parse, i.e. the head was overwritten.

    AV1 samples are recognised by their OBU framing and cleared; a sample that parses as
    neither AV1 nor H.264 is junk. Returns False when there is no video track or the index
    cannot be read — an unknown is not an accusation.
    """
    try:
        with open(path, "rb") as f:
            with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                moov = locate_moov(mm)
                if moov is None:
                    return False
                for trak in (b for b in read_boxes(cast(bytes, mm), moov.payload, moov.end)
                             if b.type == "trak"):
                    table = sample_table(mm, trak)
                    if not table or table["handler"] != b"vide" or not table["samples"]:
                        continue
                    first = table["samples"][0]
                    data = mm[first["offset"]:first["offset"] + first["size"]]
                    if is_av1_sample(data):
                        return False
                    return nal_types(data) is None
    except (ValueError, OSError):
        return False
    return False


def first_clean_keyframe(buf) -> dict | None:
    """Where the decodable video really starts, and how many samples were junk before it.

    Handles both codecs this tool understands: H.264 (an IDR inside length-prefixed NALs) and
    AV1 (a KEY_FRAME in the first frame OBU).
    """
    moov = locate_moov(buf)
    if moov is None:
        return None
    for trak in (b for b in read_boxes(buf, moov.payload, moov.end) if b.type == "trak"):
        table = sample_table(buf, trak)
        if not table or table["handler"] != b"vide" or not table["timescale"]:
            continue
        samples = table["samples"]

        # Decide the codec by majority, sampling across the whole track. Deciding per sample
        # lets a junk head masquerade as the other codec: random bytes can parse as an AV1
        # KEY_FRAME by luck, and then the real H.264 stream is never cut.
        step = max(1, len(samples) // 200)
        votes = {"h264": 0, "av1": 0}
        for i in range(0, len(samples), step):
            data = buf[samples[i]["offset"]:samples[i]["offset"] + samples[i]["size"]]
            if nal_types(data) is not None:
                votes["h264"] += 1
            elif is_av1_sample(data):
                votes["av1"] += 1
        codec = "av1" if votes["av1"] > votes["h264"] else "h264"

        t, junk = 0.0, 0
        for index, sample in enumerate(samples):
            data = buf[sample["offset"]:sample["offset"] + sample["size"]]
            if codec == "av1":
                parsed = is_av1_sample(data)
                clean = parsed and av1_is_keyframe(data)
            else:
                types = nal_types(data)
                parsed = types is not None
                clean = bool(parsed and 5 in (types or []))
            if not parsed:
                junk += 1
            if clean:
                return {"time": t, "index": index, "dropped": junk,
                        "timescale": table["timescale"], "codec": codec}
            t += sample["duration"] / table["timescale"]
    return None
