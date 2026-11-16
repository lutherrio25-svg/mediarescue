"""Codec sniffing for fragmented sources whose init segment (and so codec info) is lost.

Only what flatten() needs: tell AV1 / H.264 apart from raw sample bytes, and build an
`av01` sample entry (with av1C) from the sequence header found inside the first sample.
Parsing follows the AV1 spec, section 5.5 (sequence header OBU).
"""
from __future__ import annotations

import struct


def leb128(b: bytes, o: int) -> tuple[int, int]:
    v = 0
    for i in range(8):
        if o >= len(b):
            raise ValueError("truncated leb128")
        c = b[o]
        o += 1
        v |= (c & 0x7F) << (7 * i)
        if not c & 0x80:
            return v, o
    raise ValueError("leb128 too long")


def iter_obus(d: bytes):
    """Yield (type, header_start, payload_start, payload_end); raise ValueError if malformed."""
    o = 0
    while o < len(d):
        start = o
        h = d[o]
        if h & 0x80 or not (h >> 1) & 1:  # forbidden bit / missing obu_has_size_field
            raise ValueError("bad obu header")
        o += 1 + ((h >> 2) & 1)
        size, o = leb128(d, o)
        if o + size > len(d):
            raise ValueError("obu overruns sample")
        yield (h >> 3) & 15, start, o, o + size
        o += size


def is_av1_sample(d: bytes) -> bool:
    try:
        return any(True for _ in iter_obus(d))
    except ValueError:
        return False


def is_avc_sample(d: bytes, length_size: int = 4) -> bool:
    """Length-prefixed NAL units that tile the sample exactly, with forbidden_zero_bit clear."""
    o = 0
    n = 0
    while o + length_size <= len(d):
        L = int.from_bytes(d[o:o + length_size], "big")
        o += length_size
        if L == 0 or o + L > len(d) or d[o] & 0x80:
            return False
        o += L
        n += 1
    return o == len(d) and n > 0


def sniff_video_codec(samples_bytes: list[bytes]) -> str:
    """'av1', 'h264' or 'unknown' — decided by majority over the given samples."""
    probe = [d for d in samples_bytes if len(d) > 8]
    if not probe:
        return "unknown"
    av1 = sum(is_av1_sample(d) for d in probe)
    avc = sum(is_avc_sample(d) for d in probe)
    need = max(1, int(len(probe) * 0.8))
    if av1 >= need and av1 > avc:
        return "av1"
    if avc >= need and avc > av1:
        return "h264"
    return "unknown"


def av1_is_keyframe(sample: bytes) -> bool:
    """True when the first frame OBU in this sample is a KEY_FRAME.

    OBU_FRAME (6) and OBU_FRAME_HEADER (3) both start with the frame header, whose first bits
    are show_existing_frame, then a 2-bit frame_type where 0 means KEY_FRAME. That is all the
    information needed to find where decodable video starts, without parsing the rest.
    """
    try:
        for typ, _start, p0, p1 in iter_obus(sample):
            if typ in (3, 6):
                payload = sample[p0:p1]
                if not payload:
                    return False
                if (payload[0] >> 7) & 1:      # show_existing_frame: not a new picture
                    return False
                return ((payload[0] >> 5) & 0b11) == 0
    except (ValueError, IndexError):
        return False
    return False


class _Bits:
    def __init__(self, b: bytes):
        self.b, self.p = b, 0

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.b[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v


def parse_sequence_header(payload: bytes) -> dict:
    r = _Bits(payload)
    profile = r.u(3)
    r.u(1)  # still_picture
    reduced = r.u(1)
    level, tier = 0, 0
    decoder_model = 0
    delay_len = 0
    if reduced:
        level = r.u(5)
    else:
        if r.u(1):  # timing_info_present_flag
            r.u(32)
            r.u(32)
            if r.u(1):  # equal_picture_interval -> uvlc
                zeros = 0
                while r.u(1) == 0:
                    zeros += 1
                    if zeros > 32:
                        raise ValueError("bad uvlc")
                r.u(zeros)
            decoder_model = r.u(1)
            delay_len = 0
            if decoder_model:
                delay_len = r.u(5) + 1
                r.u(32)
                r.u(5)
                r.u(5)
        init_delay = r.u(1)
        for i in range(r.u(5) + 1):
            r.u(12)
            lv = r.u(5)
            t = r.u(1) if lv > 7 else 0
            if i == 0:
                level, tier = lv, t
            if decoder_model and r.u(1):
                r.u(delay_len)
                r.u(delay_len)
                r.u(1)
            if init_delay and r.u(1):
                r.u(4)
    wb, hb = r.u(4) + 1, r.u(4) + 1
    width, height = r.u(wb) + 1, r.u(hb) + 1
    if not reduced:
        if r.u(1):  # frame_id_numbers_present_flag
            r.u(4)
            r.u(3)
    r.u(3)  # use_128x128, enable_filter_intra, enable_intra_edge
    order_hint = 0
    if not reduced:
        r.u(4)  # inter_intra, masked, warped, dual_filter
        order_hint = r.u(1)
        if order_hint:
            r.u(2)  # jnt_comp, ref_frame_mvs
        force_sct = 2 if r.u(1) else r.u(1)
        if force_sct > 0:
            if not r.u(1):  # seq_choose_integer_mv
                r.u(1)
        if order_hint:
            r.u(3)
    r.u(3)  # superres, cdef, restoration
    high = r.u(1)
    twelve = r.u(1) if (profile == 2 and high) else 0
    mono = r.u(1) if profile != 1 else 0
    cp = tc = mc = 2
    if r.u(1):  # color_description_present_flag
        cp, tc, mc = r.u(8), r.u(8), r.u(8)
    csp = 0
    if mono:
        r.u(1)
        subx = suby = 1
    elif (cp, tc, mc) == (1, 13, 0):
        subx = suby = 0
    else:
        r.u(1)  # color_range
        if profile == 0:
            subx = suby = 1
        elif profile == 1:
            subx = suby = 0
        elif twelve:
            subx = r.u(1)
            suby = r.u(1) if subx else 0
        else:
            subx, suby = 1, 0
        if subx and suby:
            csp = r.u(2)
    return dict(profile=profile, level=level, tier=tier, width=width, height=height,
                high_bitdepth=high, twelve_bit=twelve, mono=mono, subx=subx, suby=suby, csp=csp)


def find_sequence_header(sample: bytes) -> tuple[dict, bytes] | None:
    """(parsed fields, the complete sequence-header OBU incl. header) from one sample."""
    try:
        for typ, start, p0, p1 in iter_obus(sample):
            if typ == 1:
                return parse_sequence_header(sample[p0:p1]), sample[start:p1]
    except (ValueError, IndexError):
        return None
    return None


def build_av01_stsd(info: dict, seq_obu: bytes) -> bytes:
    """A complete `stsd` box holding one `av01` entry with an `av1C` child."""
    av1c_payload = bytes([
        0x81,
        (info["profile"] << 5) | info["level"],
        (info["tier"] << 7) | (info["high_bitdepth"] << 6) | (info["twelve_bit"] << 5)
        | (info["mono"] << 4) | (info["subx"] << 3) | (info["suby"] << 2) | info["csp"],
        0x00,
    ]) + seq_obu
    av1c = struct.pack(">I4s", 8 + len(av1c_payload), b"av1C") + av1c_payload
    entry = (b"\x00" * 6 + struct.pack(">H", 1)            # reserved + data_reference_index
             + b"\x00" * 16                                 # pre_defined/reserved
             + struct.pack(">HH", info["width"], info["height"])
             + struct.pack(">II", 0x00480000, 0x00480000)  # 72 dpi
             + b"\x00" * 4 + struct.pack(">H", 1)           # reserved, frame_count
             + b"\x00" * 32                                 # compressorname
             + struct.pack(">Hh", 0x0018, -1)               # depth, pre_defined
             + av1c)
    entry_box = struct.pack(">I4s", 8 + len(entry), b"av01") + entry
    body = b"\x00\x00\x00\x00" + struct.pack(">I", 1) + entry_box
    return struct.pack(">I4s", 8 + len(body), b"stsd") + body
