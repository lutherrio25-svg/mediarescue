"""Parsers must survive hostile bytes: they read numbers out of damaged files for a living.

Every parser here is fed random payloads and deliberately broken box structures. None of them may
raise, because the CLI would show a traceback instead of a verdict. `iter_obus` is the one
documented exception: it validates and raises `ValueError`, and every caller catches it.
"""
import random
import struct

import pytest

from mediarescue import av1, avi, mp4, tables


def random_payloads(seed=3, count=150):
    rng = random.Random(seed)
    return [bytes(rng.randrange(256) for _ in range(rng.randrange(0, 400)))
            for _ in range(count)]


def box_payloads(seed=5, count=150):
    rng = random.Random(seed)
    kinds = [b"ftyp", b"moov", b"mdat", b"trak", b"stbl", b"stsz", b"stco", b"trun", b"tfhd",
             b"traf", b"moof", b"mvhd", b"tkhd", b"avcC", b"av01"]

    def box(depth=0):
        payload = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 30)))
        if depth < 2 and rng.random() < 0.5:
            payload = b"".join(box(depth + 1) for _ in range(rng.randrange(0, 3)))
        size = rng.choice([8 + len(payload), 0, 1, 0xFFFFFFFF, 8])
        return struct.pack(">I", size) + rng.choice(kinds) + payload

    return [b"".join(box() for _ in range(rng.randrange(1, 4))) for _ in range(count)]


def hostile_stbl():
    """A stbl whose counts say 'billions' while the file holds a handful of bytes."""
    def box(t, body):
        return struct.pack(">I", 8 + len(body)) + t + body
    n = 0xFFFFFFFF
    stbl = box(b"stbl", box(b"stsz", struct.pack(">II", 0, n) + b"\x00" * 8)
               + box(b"stco", struct.pack(">II", n, 0) + b"\xff" * 8)
               + box(b"stts", struct.pack(">II", n, 0xFFFFFFFF) + b"\x00" * 8))
    minf = box(b"minf", stbl)
    mdia = box(b"mdia", box(b"hdlr", b"\x00" * 8 + b"vide" + b"\x00" * 12) + minf)
    return box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2") + box(b"moov", box(b"trak", mdia))


ALL_PAYLOADS = random_payloads() + box_payloads() + [hostile_stbl(), b"", b"\x00" * 4096]


@pytest.mark.parametrize("data", ALL_PAYLOADS)
def test_read_boxes_never_raises(data):
    mp4.read_boxes(data)


@pytest.mark.parametrize("data", ALL_PAYLOADS)
def test_av1_helpers_never_raise(data):
    av1.is_av1_sample(data)
    av1.av1_is_keyframe(data)
    av1.sniff_video_codec(data)                 # bytes, not a list: must not blow up
    av1.sniff_video_codec([data, b"", data])


@pytest.mark.parametrize("data", ALL_PAYLOADS[:80])
def test_file_parsers_never_raise(tmp_path, data):
    case = tmp_path / "case.bin"
    case.write_bytes(data)
    avi.check(case)
    tables.head_is_junk(case)


def test_iter_obus_raises_only_valueerror():
    """The documented contract: malformed input raises ValueError, never IndexError/struct.error."""
    rng = random.Random(9)
    for _ in range(300):
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
        try:
            list(av1.iter_obus(data))
        except ValueError:
            pass


def test_sniff_accepts_a_single_sample_as_bytes():
    assert av1.sniff_video_codec(b"\x00" * 32) in ("unknown", "av1", "h264")


def _trak_with(stsz_count: int, stco_count: int = 1) -> bytes:
    def box(t, body):
        return struct.pack(">I", 8 + len(body)) + t + body
    # FullBox layout: version/flags first, THEN the fields. Getting this wrong makes the count read
    # as sample_size and the guard is never exercised.
    stbl = box(b"stbl", box(b"stsz", struct.pack(">III", 0, 0, stsz_count) + b"\x00" * 8)
               + box(b"stco", struct.pack(">II", 0, stco_count) + b"\x00" * 8)
               + box(b"stts", struct.pack(">II", 1, 1) + struct.pack(">II", 0xFFFFFFFF, 40))
               + box(b"stsc", struct.pack(">II", 1, 1) + struct.pack(">III", 1, 1, 1)))
    mdhd = box(b"mdhd", b"\x00" * 12 + struct.pack(">I", 1000) + b"\x00" * 8)
    return box(b"trak", box(b"mdia", mdhd + box(b"hdlr", b"\x00" * 8 + b"vide" + b"\x00" * 12)
                            + box(b"minf", stbl)))


def test_a_sample_table_that_claims_billions_is_rejected_not_allocated():
    """stsz with a 4-billion sample count must come back as 'no table', not a 16 GB list."""
    data = _trak_with(0xFFFFFFF0)
    assert tables.sample_table(data, mp4.read_boxes(data)[0]) is None


def test_the_guard_is_what_rejects_it_not_a_missing_box():
    """Same fixture with a sane count must parse, so the None above really comes from the count."""
    data = _trak_with(2)
    assert tables.sample_table(data, mp4.read_boxes(data)[0]) is not None


def test_a_uniform_size_table_cannot_claim_more_bytes_than_the_file_has():
    """sample_size=1000 x count=4 billion would be a 4 TB 'file': refuse before building a list."""
    def box(t, body):
        return struct.pack(">I", 8 + len(body)) + t + body
    data = _trak_with(2)
    stsz_at = data.find(b"stsz") + 4                  # first byte after the type = version/flags
    patched = bytearray(data)
    struct.pack_into(">III", patched, stsz_at, 0, 1000, 0xFFFFFFF0)   # uniform size, huge count
    patched = bytes(patched)
    assert tables.sample_table(patched, mp4.read_boxes(patched)[0]) is None


def test_stts_entries_cannot_inflate_the_duration_list():
    """The fixture's stts says 4 billion samples of 40 ticks; only len(sizes) may be built."""
    data = _trak_with(2)
    table = tables.sample_table(data, mp4.read_boxes(data)[0])
    assert table is not None
    assert all(s["duration"] in (0, 40) for s in table["samples"])


def test_a_trun_claiming_billions_of_samples_is_clamped():
    from mediarescue.fragmented import parse_fragments

    def box(t, body):
        return struct.pack(">I", 8 + len(body)) + t + body
    trun = box(b"trun", b"\x00\x00\x02\x01" + struct.pack(">I", 0xFFFFFFF0)
               + struct.pack(">i", 0) + struct.pack(">I", 10))     # data_offset + ONE size entry
    traf = box(b"traf", box(b"tfhd", b"\x00\x00\x00\x00" + struct.pack(">I", 1)) + trun)
    moof = box(b"moof", traf)
    # Non-zero bytes after the box: without the clamp the parser keeps "finding" samples in them
    # (size 0x01010101 is never <= 0), so a missing clamp cannot hide behind a zero terminator.
    tail = box(b"mdat", b"\x01" * 4096)
    tracks = parse_fragments(box(b"ftyp", b"isom\x00\x00\x02\x00isom") + moof + tail)
    total = sum(len(t.samples) for t in tracks.values())
    assert total == 1, f"clamp failed: {total} samples read from a box that holds one"
