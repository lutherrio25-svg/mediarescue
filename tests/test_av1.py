"""Codec sniffing + av1C construction, checked against a real libaom/dav1d round trip."""
import shutil
import subprocess

import pytest

from mediarescue.av1 import (build_av01_stsd, find_sequence_header, is_av1_sample,
                             is_avc_sample, sniff_video_codec)
from mediarescue.ffmpeg import have_ffmpeg

have_av1 = have_ffmpeg() and "libaom-av1" in subprocess.run(
    ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
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
