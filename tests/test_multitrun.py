"""Regression: several truns inside ONE traf (real encoders do this; ffmpeg's fixtures do not).

trun.data_offset is relative to the traf base (moof start with default-base-is-moof),
NOT to where the previous trun's data ended. The old parser chained them and shifted
every trun after the first, which made a 36-minute AV1 file decode as noise.
"""
import struct

from mediarescue.fragmented import parse_fragments


def box(typ, payload):
    return struct.pack(">I4s", len(payload) + 8, typ) + payload


def full(typ, flags, payload, version=0):
    return box(typ, bytes([version]) + flags.to_bytes(3, "big") + payload)


def trun(data_offset, sizes):
    # flags: data-offset + sample-duration + sample-size
    body = struct.pack(">Ii", len(sizes), data_offset)
    body += b"".join(struct.pack(">II", 1000, s) for s in sizes)
    return full(b"trun", 0x000301, body[:4] + body[4:])


def build(layout):
    """layout: list of runs [(gap_before, [sizes])]; returns (file bytes, expected offsets)."""
    tfhd = full(b"tfhd", 0x020000, struct.pack(">I", 1))  # default-base-is-moof
    # first pass with zero offsets to learn the moof size
    def moof_with(offsets):
        truns = b"".join(trun(o, sizes) for o, (_, sizes) in zip(offsets, layout))
        traf = box(b"traf", tfhd + truns)
        return box(b"moof", box(b"mfhd", b"\0" * 8) + traf)

    size = len(moof_with([0] * len(layout)))
    offsets, expected, cursor = [], [], size + 8  # +8: mdat header
    payload = b""
    for gap, sizes in layout:
        cursor += gap
        payload += b"\xee" * gap
        offsets.append(cursor)
        for s in sizes:
            expected.append((cursor, s))
            payload += bytes([len(expected) % 251]) * s
            cursor += s
    return moof_with(offsets) + box(b"mdat", payload), expected


def test_multiple_truns_use_traf_base_not_previous_end():
    # the 2nd trun starts far after the 1st one's data (gap) -> chaining would be wrong
    data, expected = build([(0, [10, 20, 30]), (500, [7, 8]), (40, [9])])
    got = parse_fragments(data)[1].samples
    assert [(s.offset, s.size) for s in got] == expected
    # payload at those offsets must be the bytes we wrote for that sample
    for i, s in enumerate(got, start=1):
        assert set(data[s.offset:s.offset + s.size]) == {i % 251}


def test_back_to_back_trun_without_gap():
    data, expected = build([(0, [5, 5]), (0, [6, 6])])
    got = parse_fragments(data)[1].samples
    assert [(s.offset, s.size) for s in got] == expected
