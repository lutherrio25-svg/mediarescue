"""Codec sniffing + av1C construction, checked against a real libaom/dav1d round trip."""
import subprocess

import pytest

from mediarescue.av1 import (
    build_av01_stsd,
    find_sequence_header,
    is_av1_sample,
    is_avc_sample,
    sniff_video_codec,
)
from mediarescue.ffmpeg import have_ffmpeg

have_av1 = have_ffmpeg() and "libaom-av1" in subprocess.run(
    ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False).stdout
needs_av1 = pytest.mark.skipif(not have_av1, reason="ffmpeg without libaom-av1")


def test_h264_and_garbage_are_not_av1():
    nal = (5).to_bytes(4, "big") + b"\x65" + b"\x01\x02\x03\x04"      # one NAL, 5 bytes
    assert is_avc_sample(nal) and not is_av1_sample(nal)
    noise = bytes(range(200, 255)) * 3
    assert sniff_video_codec([noise] * 10) == "unknown"
    assert sniff_video_codec([]) == "unknown"


@needs_av1
def test_av1_sequence_header_roundtrip(tmp_path):
    """Encode real AV1, strip it to raw OBUs, and make sure we read the right size back."""
    src = tmp_path / "a.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x96:rate=10",
                    "-t", "1", "-c:v", "libaom-av1", "-cpu-used", "8", "-b:v", "200k", str(src)],
                   check=True, capture_output=True)
    obu = tmp_path / "a.obu"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "copy",
                    "-bsf:v", "av1_metadata=td=remove", "-f", "obu", str(obu)],
                   check=True, capture_output=True)
    data = obu.read_bytes()
    found = find_sequence_header(data)
    assert found, "no sequence header located"
    info, seq_obu = found
    assert (info["width"], info["height"]) == (160, 96)
    stsd = build_av01_stsd(info, seq_obu)
    assert stsd[4:8] == b"stsd" and b"av01" in stsd and b"av1C" in stsd


def test_encoder_tag_detected_but_real_aac_frames_are_not():
    from mediarescue.fragmented import looks_like_encoder_tag
    blob = bytes.fromhex("de0200") + b"Lavc61.33.102\x00B \x08\xc1\x188"
    assert looks_like_encoder_tag(blob)
    assert not looks_like_encoder_tag(b"\x21\x10\x04\x60\x8c\x1c" * 4)   # AAC frames
    assert not looks_like_encoder_tag(b"Lavc")                            # too short


@needs_av1
def test_av1_head_is_not_flagged_as_junk(tmp_path):
    """AV1 samples have no NAL length prefixes; the junk check must not convict them."""
    from mediarescue.diagnose import diagnose
    from mediarescue.report import Severity
    from mediarescue.tables import head_is_junk

    clip = tmp_path / "av1.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=160x96:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "2",
                    "-c:v", "libaom-av1", "-cpu-used", "8", "-b:v", "200k",
                    "-c:a", "aac", "-b:a", "64k", str(clip)], check=True, capture_output=True)
    assert head_is_junk(clip) is False
    assert diagnose(clip, hash_file=False).severity == Severity.HEALTHY


@needs_av1
def test_av1_keyframes_are_found_and_salvage_cuts_to_one(tmp_path):
    """AV1 has no IDR: the cut point is the first KEY_FRAME, and a corrupt head must not hide it."""
    from mediarescue.av1 import av1_is_keyframe
    from mediarescue.mp4 import read_boxes
    from mediarescue.salvage import salvage
    from mediarescue.tables import first_clean_keyframe, locate_moov, sample_table

    def decode_errors(path):
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                           capture_output=True, text=True, check=False)
        return len([ln for ln in p.stderr.splitlines() if not ln.startswith("[null @")])

    clip = tmp_path / "g.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25",
                    "-t", "4", "-c:v", "libaom-av1", "-cpu-used", "8", "-b:v", "300k",
                    "-g", "25", str(clip)], check=True, capture_output=True)

    buf = clip.read_bytes()
    moov = locate_moov(buf)
    trak = next(b for b in read_boxes(buf, moov.payload, moov.end) if b.type == "trak")
    table = sample_table(buf, trak)
    flags = [av1_is_keyframe(buf[s["offset"]:s["offset"] + s["size"]]) for s in table["samples"]]
    assert flags[0] is True and flags.count(True) >= 3

    data = bytearray(buf)
    start = data.find(b"mdat") + 4
    span = int((len(data) - start) * 0.3)
    data[start:start + span] = bytes((i * 37) % 256 for i in range(span))
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(bytes(data))

    key = first_clean_keyframe(bad.read_bytes())
    assert key and key["codec"] == "av1" and key["time"] > 0.5, key

    out = tmp_path / "fixed.mp4"
    result = salvage(bad, out)
    assert result["ok"], result
    assert result["dropped_seconds"] > 0.5
    assert decode_errors(out) == 0
