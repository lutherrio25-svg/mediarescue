"""Flatten a fragmented MP4 (moof/mdat only, init segment lost) into a normal MP4.

The codec description (avcC/esds) cannot be recovered from fragments, so an
intact reference from the same encoder/session is required. Everything else —
sample sizes, durations, decode order — is read from the trun boxes.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .av1 import build_av01_stsd, find_sequence_header, sniff_video_codec
from .mp4 import Box, find, iter_all, read_boxes


@dataclass
class Sample:
    offset: int
    size: int
    duration: int
    flags: int
    cto: int = 0  # composition time offset (B-frame reordering), from trun flag 0x800


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


def _trun(buf: bytes, box: Box, origin: int, cursor: int | None, default_dur: int,
          default_size: int) -> tuple[list[Sample], int | None]:
    """origin = base data offset of the traf (tfhd base, else moof start).

    trun.data_offset is relative to ORIGIN, never to the end of the previous trun;
    cursor is only used for a trun without data_offset (data continues from the
    previous trun). Mixing the two shifted every trun after the first one.
    """
    p = box.payload
    version = buf[p]
    flags = int.from_bytes(buf[p + 1:p + 4], "big")
    count = struct.unpack_from(">I", buf, p + 4)[0]
    cur = p + 8
    data_offset = None
    if flags & 0x000001:
        data_offset = struct.unpack_from(">i", buf, cur)[0]
        cur += 4
    if flags & 0x000004:
        cur += 4
    if data_offset is not None:
        cursor = origin + data_offset
    elif cursor is None:
        cursor = origin
    samples = []
    # A trun written by a crashed muxer can claim billions of entries. Only as many as the box
    # still holds can be read, so clamp instead of trusting the count.
    per_sample = 4 * sum(1 for bit in (0x000100, 0x000200, 0x000400, 0x000800) if flags & bit)
    if per_sample:
        count = min(count, max(0, (box.end - cur) // per_sample))
    elif count > 1_000_000:      # no per-sample fields: sizes come from tfhd, count is all we have
        count = 1_000_000
    for _ in range(count):
        dur, size, sflags, cto = default_dur, default_size, 0, 0
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
            # v0 is unsigned, v1 signed (negative offsets allowed)
            cto = struct.unpack_from(">i" if version else ">I", buf, cur)[0]
            cur += 4
        if size <= 0:
            return samples, cursor
        off = cursor if cursor is not None else 0
        samples.append(Sample(off, size, dur, sflags, cto))
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
            origin = info["base"] if info["base"] is not None else box.offset
            cursor = None
            for trun, _ in iter_all(buf, traf.payload, traf.end):
                if trun.type != "trun":
                    continue
                samples, cursor = _trun(buf, trun, origin, cursor, info["duration"], info["size"])
                track.samples.extend(samples)
    return tracks


# ---------- moov synthesis ----------

def _box(typ: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", len(payload) + 8, typ) + payload


def _full(typ: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _box(typ, bytes([version]) + flags.to_bytes(3, "big") + payload)


COMMON_TIMESCALES = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000,
                     88200, 90000, 96000)
AUDIO_FRAME_SIZES = (1024, 960, 1152, 2048)  # AAC-LC, AAC 960, MP3, HE-AAC


def infer_timescale(samples: list, handler: bytes, ref_timescale: int) -> int:
    """Audio timescale actually used by a fragmented source.

    The init segment is gone, so the reference's mdhd timescale is only a guess.
    For constant-frame-size audio the median sample duration says more: AAC packs
    1024 samples per frame, so duration/1024 * sample_rate is the real clock.
    Only a snap to a known timescale (within 1%) is trusted; video is never touched.
    """
    if handler != b"soun" or len(samples) < 8:
        return ref_timescale
    durations = sorted(s.duration for s in samples if s.duration > 0)
    if not durations:
        return ref_timescale
    median = durations[len(durations) // 2]
    for frame in AUDIO_FRAME_SIZES:
        guess = median / frame * ref_timescale
        for ts in COMMON_TIMESCALES:
            if abs(guess - ts) / ts < 0.01:
                return ts
    return ref_timescale


def looks_like_encoder_tag(d: bytes) -> bool:
    """True for the 'Lavc<version>' blob some muxers park in front of the first audio sample.

    It is text, not a frame, so handing it to a decoder produces one bad frame and a click.
    """
    if len(d) < 16 or b"Lavc" not in d:
        return False
    printable = sum(1 for c in d if 32 <= c < 127)
    return printable * 2 >= len(d)


def _runs(durations: list[int]) -> bytes:
    entries = []
    for d in durations:
        if entries and entries[-1][1] == d:
            entries[-1][0] += 1
        else:
            entries.append([1, d])
    return struct.pack(">I", len(entries)) + b"".join(struct.pack(">II", c, d) for c, d in entries)


def _ctts_runs(offsets: list[int], signed: bool) -> bytes:
    entries = []
    for o in offsets:
        if entries and entries[-1][1] == o:
            entries[-1][0] += 1
        else:
            entries.append([1, o])
    fmt = ">Ii" if signed else ">II"
    return struct.pack(">I", len(entries)) + b"".join(struct.pack(fmt, c, o) for c, o in entries)


def _patch_duration(trak: bytes, track_duration: int, movie_timescale: int, track_timescale: int,
                    set_timescale: int | None = None) -> bytes:
    b = bytearray(trak)
    for box, _ in iter_all(bytes(b)):
        if box.type == "mdhd":
            if set_timescale:
                struct.pack_into(">I", b, box.payload + 12, set_timescale)
            struct.pack_into(">I", b, box.payload + 16, track_duration)
        elif box.type == "tkhd":
            value = min(0xFFFFFFFF, int(track_duration * movie_timescale / max(track_timescale, 1)))
            struct.pack_into(">I", b, box.payload + 20, value)
    return bytes(b)


def flatten(damaged: Path, reference: Path, out: Path, force_timescale: int | None = None) -> dict:
    """Returns a result dict; never overwrites the input.

    force_timescale: force one timescale for every track. Fragmented encoders
    often use a single clock (e.g. 90000) for audio and video alike, while the
    reference file may use 44100 for audio — that mismatch doubles the audio
    duration, so an explicit override is sometimes required.
    """
    buf = damaged.read_bytes()
    ref = reference.read_bytes()
    tracks = parse_fragments(buf)
    if not tracks:
        return {"ok": False, "reason": "no moof/traf samples found"}
    in_bounds, dropped = [], 0
    for t in tracks.values():
        good = [s for s in t.samples if s.offset >= 0 and s.offset + s.size <= len(buf)]
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
    movie_timescale = 1000  # normalised: inheriting odd reference scales can overflow tkhd

    r_ftyp = next((b for b in read_boxes(ref) if b.type == "ftyp"), None)
    ftyp = ref[r_ftyp.offset:r_ftyp.end] if r_ftyp else _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2")

    new_traks, payloads, stco_positions = [], [], []
    movie_durations = []
    notes: list[str] = []
    used_ref: set[int] = set()
    video_codecs: set[str] = set()

    def ref_handler(rt: Box) -> bytes:
        h = next((b for b, _ in iter_all(ref, rt.offset + 8, rt.end) if b.type == "hdlr"), None)
        return bytes(ref[h.payload + 8:h.payload + 12]) if h else b""

    for frag in in_bounds:
        # What is this track really? Decide from the bytes, not from track order.
        probe = [buf[s.offset:s.offset + s.size] for s in frag.samples[:200:4]]
        codec = sniff_video_codec(probe)
        want = b"vide" if codec != "unknown" else b"soun"
        if codec != "unknown":
            video_codecs.add(codec)
        idx = next((k for k, rt in enumerate(ref_traks)
                    if k not in used_ref and ref_handler(rt) == want), None)
        if idx is None:
            return {"ok": False, "reason": f"reference has no unused {want.decode()} track "
                                           f"for fragment track {frag.track_id} (codec: {codec})"}
        used_ref.add(idx)
        rt = ref_traks[idx]
        trak = bytearray(ref[rt.offset:rt.end])
        nested = {b.type: b for b, _ in iter_all(bytes(trak))}
        stbl, mdhd, stsd = nested.get("stbl"), nested.get("mdhd"), nested.get("stsd")
        if stbl is None or mdhd is None:
            return {"ok": False, "reason": "reference trak lacks stbl/mdhd"}
        ref_timescale = struct.unpack_from(">I", trak, mdhd.payload + 12)[0]
        handler = want
        if stsd is None:
            return {"ok": False, "reason": "reference has no stsd codec description"}

        samples = list(frag.samples)
        if want == b"soun" and samples:
            head = buf[samples[0].offset:samples[0].offset + min(samples[0].size, 64)]
            if looks_like_encoder_tag(head):
                tag = head[3:19].decode("latin1", "replace").rstrip("\x00")
                notes.append(f"track {frag.track_id}: dropped leading {samples[0].size}-byte encoder "
                             f"tag {tag!r} sitting in front of the first audio frame")
                samples = samples[1:]
                if not samples:
                    return {"ok": False, "reason": "audio track held only an encoder tag"}
        stsd_bytes = bytes(trak[stsd.offset:stsd.end])
        if want == b"vide":
            ref_entry = bytes(trak[stsd.payload + 12:stsd.payload + 16])
            if codec == "av1":
                # The source carries its own sequence header: build av1C from it and do
                # not borrow a (possibly different) codec description from the reference.
                found = next((r for r in (find_sequence_header(buf[s.offset:s.offset + s.size])
                                          for s in samples[:120]) if r), None)
                if found is None:
                    return {"ok": False, "reason": "AV1 stream but no sequence header in the "
                                                   "first 120 samples; cannot build av1C"}
                info, seq_obu = found
                stsd_bytes = build_av01_stsd(info, seq_obu)
                notes.append(f"track {frag.track_id}: AV1 {info['width']}x{info['height']} "
                             f"(profile {info['profile']}, level idx {info['level']}) — "
                             f"av1C built from the stream, reference codec ignored")
                width, height = info["width"], info["height"]
            elif codec == "h264" and ref_entry != b"avc1":
                return {"ok": False, "reason": f"source video is H.264 but the reference video is "
                                               f"{ref_entry.decode('latin1')!r}; use a reference "
                                               "from the same encoder"}
            else:
                width = height = None
        else:
            width = height = None
        timescale = force_timescale or infer_timescale(samples, handler, ref_timescale)
        if timescale != ref_timescale:
            notes.append(f"track {frag.track_id}: timescale {ref_timescale} -> {timescale} "
                         f"({'forced' if force_timescale else 'inferred from sample durations'})")
        sizes = [s.size for s in samples]
        durs = [s.duration or max(1, timescale // 25) for s in samples]
        payload = b"".join(buf[s.offset:s.offset + s.size] for s in samples)
        payloads.append(payload)

        stsz = _full(b"stsz", 0, 0, struct.pack(">II", 0, len(sizes)) +
                     b"".join(struct.pack(">I", s) for s in sizes))
        stts = _full(b"stts", 0, 0, _runs(durs))
        ctts = b""
        if any(s.cto for s in samples):
            # version 1 when any offset is negative so players keep correct ordering
            neg = any(s.cto < 0 for s in samples)
            ctts = _full(b"ctts", 1 if neg else 0, 0, _ctts_runs([s.cto for s in samples], neg))
        stsc = _full(b"stsc", 0, 0, struct.pack(">IIII", 1, 1, len(samples), 1))
        stco = _full(b"stco", 0, 0, struct.pack(">II", 1, 0))
        non_sync = [n + 1 for n, s in enumerate(samples) if s.flags & 0x00010000]
        stss = b""
        if non_sync and len(non_sync) < len(samples):
            sync = [n for n in range(1, len(samples) + 1) if n not in set(non_sync)]
            stss = _full(b"stss", 0, 0, struct.pack(">I", len(sync)) +
                         b"".join(struct.pack(">I", n) for n in sync))

        new_stbl = _box(b"stbl", stsd_bytes + stts + ctts + stsc + stsz + stco + stss)
        delta = len(new_stbl) - stbl.size
        # Locate ancestors BEFORE splicing: afterwards their declared sizes no longer
        # match the content, so the strict walker cannot find them (shrinking case).
        ancestors = [b for b, _ in iter_all(bytes(trak)) if b.type in ("minf", "mdia")]
        trak = trak[:stbl.offset] + new_stbl + trak[stbl.end:]
        for abox in ancestors:  # offsets precede stbl, so they are unchanged
            struct.pack_into(">I", trak, abox.offset, abox.size + delta)
        struct.pack_into(">I", trak, 0, len(trak))
        # Edit lists from the reference use its movie timescale and no longer apply.
        edts = next((b for b, depth in iter_all(bytes(trak)) if b.type == "edts" and depth == 1), None)
        if edts is not None:
            trak = trak[:edts.offset] + trak[edts.end:]
            struct.pack_into(">I", trak, 0, len(trak))
        trak = bytearray(_patch_duration(bytes(trak), sum(durs), movie_timescale, timescale,
                                         set_timescale=timescale))
        if width and height:  # tkhd width/height are 16.16 fixed point (v0 layout)
            tk = next((b for b, _ in iter_all(bytes(trak)) if b.type == "tkhd"), None)
            if tk is not None:
                struct.pack_into(">II", trak, tk.payload + 76, width << 16, height << 16)
        movie_durations.append(min(0xFFFFFFFF, int(sum(durs) * movie_timescale / max(timescale, 1))))
        # record absolute position of the stco value for the second pass
        for box, _ in iter_all(bytes(trak)):
            if box.type == "stco":
                stco_positions.append((len(new_traks), box.payload + 8))
        new_traks.append(bytes(trak))

    if not new_traks:
        return {"ok": False, "reason": "no tracks could be mapped to the reference"}

    if "av1" in video_codecs:
        # Never advertise the reference's brand: a player that trusts `avc1` here will pick
        # the H.264 decoder and render the AV1 bitstream as noise.
        ftyp = _box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2av01mp41")
        notes.append("ftyp brands rewritten to isom/iso2/av01/mp41 (source is AV1)")

    mvhd = bytearray(ref[ref_mvhd.offset:ref_mvhd.end])
    struct.pack_into(">I", mvhd, ref_mvhd.header + 12, movie_timescale)
    struct.pack_into(">I", mvhd, ref_mvhd.header + 16, max(movie_durations))
    moov = bytearray(_box(b"moov", bytes(mvhd) + b"".join(new_traks)))

    mdat_payload = len(ftyp) + len(moov) + 8
    offset = mdat_payload
    trak_start = 8 + len(mvhd)  # moov header + mvhd, then traks in order
    for (trak_index, pos), payload in zip(stco_positions, payloads, strict=False):
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
    result = {"ok": True, "output": str(out), "tracks": len(new_traks),
              "samples": sum(len(t.samples) for t in in_bounds[:len(new_traks)]),
              "dropped_samples": dropped,
              "track_seconds": [round(d / movie_timescale, 2) for d in movie_durations]}
    if notes:
        result["notes"] = notes
    if len(movie_durations) > 1 and min(movie_durations) > 0:
        gap = max(movie_durations) / min(movie_durations) - 1
        if gap > 0.05:
            result["warning"] = (f"track durations differ by {gap:.0%}. Can be genuine (the source "
                                 "itself has a gap or priming) or a wrong timescale; if the source "
                                 "plays at the wrong length, set --timescale explicitly")
    return result
