"""Flatten a fragmented MP4 (moof/mdat only, init segment lost) into a normal MP4.

The codec description (avcC/esds) cannot be recovered from fragments, so an
intact reference from the same encoder/session is required. Everything else —
sample sizes, durations, decode order — is read from the trun boxes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .mp4 import Box, find, iter_all, read_boxes


@dataclass
class Sample:
    offset: int
    size: int
    duration: int
    flags: int


@dataclass
class FragTrack:
    track_id: int
    samples: list[Sample] = field(default_factory=list)

    @property
    def duration(self) -> int:
        return sum(s.duration for s in self.samples)


def _tfhd(buf: bytes, box: Box) -> dict:
    p = box.payload
    flags = int.from_bytes(buf[p + 1:p + 4], "big")
    track_id = struct.unpack_from(">I", buf, p + 4)[0]
    cur = p + 8
    out = {"track_id": track_id, "flags": flags, "base": None, "duration": 0, "size": 0}
    if flags & 0x000001:
        out["base"] = struct.unpack_from(">Q", buf, cur)[0]
        cur += 8
    if flags & 0x000002:
        cur += 4
    if flags & 0x000008:
        out["duration"] = struct.unpack_from(">I", buf, cur)[0]
        cur += 4
    if flags & 0x000010:
        out["size"] = struct.unpack_from(">I", buf, cur)[0]
        cur += 4
    return out


def _trun(buf: bytes, box: Box, moof_offset: int, base: int | None, default_dur: int,
          default_size: int) -> tuple[list[Sample], int | None]:
    p = box.payload
    flags = int.from_bytes(buf[p + 1:p + 4], "big")
    count = struct.unpack_from(">I", buf, p + 4)[0]
    cur = p + 8
    data_offset = None
    if flags & 0x000001:
        data_offset = struct.unpack_from(">i", buf, cur)[0]
        cur += 4
    if flags & 0x000004:
        cur += 4
    origin = base if base is not None else moof_offset
    cursor = origin + data_offset if data_offset is not None else None
    samples = []
    for _ in range(count):
        dur, size, sflags = default_dur, default_size, 0
        if flags & 0x000100:
            dur = struct.unpack_from(">I", buf, cur)[0]
            cur += 4
        if flags & 0x000200:
            size = struct.unpack_from(">I", buf, cur)[0]
            cur += 4
        if flags & 0x000400:
            sflags = struct.unpack_from(">I", buf, cur)[0]
            cur += 4
        if flags & 0x000800:
            cur += 4
        if size <= 0:
            return samples, cursor
        off = cursor if cursor is not None else 0
        samples.append(Sample(off, size, dur, sflags))
        if cursor is not None:
            cursor += size
    return samples, cursor


def parse_fragments(buf: bytes) -> dict[int, FragTrack]:
    tracks: dict[int, FragTrack] = {}
    for box in read_boxes(buf):
        if box.type != "moof":
            continue
        for traf, _ in iter_all(buf, box.payload, box.end):
            if traf.type != "traf":
                continue
            tfhd_box = find(buf, "tfhd", start=traf.payload, end=traf.end)
            if tfhd_box is None:
                continue
            info = _tfhd(buf, tfhd_box)
            track = tracks.setdefault(info["track_id"], FragTrack(info["track_id"]))
            cursor = info["base"]
            for trun, _ in iter_all(buf, traf.payload, traf.end):
                if trun.type != "trun":
                    continue
                samples, cursor = _trun(buf, trun, box.offset, cursor, info["duration"], info["size"])
                track.samples.extend(samples)
    return tracks


# ---------- moov synthesis ----------

def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, typ) + payload


def _full(typ: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(typ, bytes([version]) + flags.to_bytes(3, "big") + payload)


def _runs(durations: list[int]) -> bytes:
    entries = []
    for d in durations:
        if entries and entries[-1][1] == d:
            entries[-1][0] += 1
        else:
            entries.append([1, d])
    return struct.pack(">I", len(entries)) + b"".join(struct.pack(">II", c, d) for c, d in entries)


def _patch_duration(trak: bytes, track_duration: int, movie_timescale: int, track_timescale: int) -> bytes:
    b = bytearray(trak)
    for box, _ in iter_all(bytes(b)):
        if box.type == "mdhd":
            struct.pack_into(">I", b, box.payload + 16, track_duration)
        elif box.type == "tkhd":
            struct.pack_into(">I", b, box.payload + 20,
                             int(track_duration * movie_timescale / max(track_timescale, 1)))
    return bytes(b)


def flatten(damaged: Path, reference: Path, out: Path) -> dict:
    """Returns a result dict; never overwrites the input."""
    buf = damaged.read_bytes()
    ref = reference.read_bytes()
    tracks = parse_fragments(buf)
    if not tracks:
        return {"ok": False, "reason": "no moof/traf samples found"}
    in_bounds, dropped = [], 0
    for t in tracks.values():
        good = [s for s in t.samples if 0 <= s.offset and s.offset + s.size <= len(buf)]
        dropped += len(t.samples) - len(good)
        if good:
            t.samples = good  # truncated final fragment: keep the decodable prefix
            in_bounds.append(t)
    if not in_bounds:
        return {"ok": False, "reason": "every sample points outside the file"}

    ref_moov = find(ref, "moov")
    if ref_moov is None:
        return {"ok": False, "reason": "reference has no moov"}
    ref_traks = [b for b, d in iter_all(ref, ref_moov.payload, ref_moov.end) if b.type == "trak" and d == 0]
    ref_mvhd = find(ref, "mvhd", start=ref_moov.payload, end=ref_moov.end)
    if not ref_traks or ref_mvhd is None:
        return {"ok": False, "reason": "reference lacks trak/mvhd"}
    movie_timescale = struct.unpack_from(">I", ref, ref_mvhd.payload + 12)[0]

    r_ftyp = next((b for b in read_boxes(ref) if b.type == "ftyp"), None)
    ftyp = ref[r_ftyp.offset:r_ftyp.end] if r_ftyp else _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2")

    new_traks, payloads, stco_positions = [], [], []
    for i, frag in enumerate(in_bounds):
        if i >= len(ref_traks):
            break
        rt = ref_traks[i]
        trak = bytearray(ref[rt.offset:rt.end])
        nested = {b.type: b for b, _ in iter_all(bytes(trak))}
        stbl, mdhd, stsd = nested.get("stbl"), nested.get("mdhd"), nested.get("stsd")
        if stbl is None or mdhd is None:
            return {"ok": False, "reason": "reference trak lacks stbl/mdhd"}
        timescale = struct.unpack_from(">I", trak, mdhd.payload + 12)[0]
        if stsd is None:
            return {"ok": False, "reason": "reference has no stsd codec description"}

        samples = frag.samples
        sizes = [s.size for s in samples]
        durs = [s.duration or max(1, timescale // 25) for s in samples]
        payload = b"".join(buf[s.offset:s.offset + s.size] for s in samples)
        payloads.append(payload)

        stsz = _full(b"stsz", 0, 0, struct.pack(">II", 0, len(sizes)) +
                     b"".join(struct.pack(">I", s) for s in sizes))
        stts = _full(b"stts", 0, 0, _runs(durs))
        stsc = _full(b"stsc", 0, 0, struct.pack(">IIII", 1, 1, len(samples), 1))
        stco = _full(b"stco", 0, 0, struct.pack(">II", 1, 0))
        non_sync = [n + 1 for n, s in enumerate(samples) if s.flags & 0x00010000]
        stss = b""
        if non_sync and len(non_sync) < len(samples):
            sync = [n for n in range(1, len(samples) + 1) if n not in set(non_sync)]
            stss = _full(b"stss", 0, 0, struct.pack(">I", len(sync)) +
                         b"".join(struct.pack(">I", n) for n in sync))

        new_stbl = _box(b"stbl", trak[stsd.offset:stsd.end] + stts + stsc + stsz + stco + stss)
        trak = trak[:stbl.offset] + new_stbl + trak[stbl.end:]
        trak = bytearray(_patch_duration(bytes(trak), sum(durs), movie_timescale, timescale))
        # record absolute position of the stco value for the second pass
        for box, _ in iter_all(bytes(trak)):
            if box.type == "stco":
                stco_positions.append((len(new_traks), box.payload + 8))
        new_traks.append(bytes(trak))

    if not new_traks:
        return {"ok": False, "reason": "no tracks could be mapped to the reference"}

    mvhd = bytearray(ref[ref_mvhd.offset:ref_mvhd.end])
    longest = max(int(sum(s.duration for s in t.samples)) for t in in_bounds[:len(new_traks)])
    struct.pack_into(">I", mvhd, ref_mvhd.header + 16, longest)
    moov = bytearray(_box(b"moov", bytes(mvhd) + b"".join(new_traks)))

    mdat_payload = len(ftyp) + len(moov) + 8
    offset = mdat_payload
    trak_start = 8 + len(mvhd)  # moov header + mvhd, then traks in order
    for (trak_index, pos), payload in zip(stco_positions, payloads):
        base = trak_start + sum(len(t) for t in new_traks[:trak_index])
        struct.pack_into(">I", moov, base + pos, offset)
        offset += len(payload)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        f.write(ftyp)
        f.write(moov)
        f.write(struct.pack(">I4s", offset - mdat_payload + 8, b"mdat"))
        for payload in payloads:
            f.write(payload)
    return {"ok": True, "output": str(out), "tracks": len(new_traks),
            "samples": sum(len(t.samples) for t in in_bounds[:len(new_traks)]),
            "dropped_samples": dropped}
