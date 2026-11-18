"""No reference + AV1 index lost: say what is actually missing, not an H.264 complaint.

Found by running the published package on a real file. `carve` only understands H.264, but was
being handed AV1 bytes; it "found" a bogus stream and blamed SPS/PPS, which an AV1 file never has.
"""
import subprocess

import pytest

from mediarescue.cli import main
from mediarescue.ffmpeg import have_ffmpeg

needs_ffmpeg = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")


def _av1_fragment(dst):
    p = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc=size=160x96:rate=10", "-t", "3", "-c:v", "libaom-av1",
                        "-cpu-used", "8", "-crf", "45", "-g", "10",
                        "-movflags", "frag_keyframe+empty_moov+default_base_moof", str(dst)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        pytest.skip(f"libaom-av1 unavailable: {p.stderr.strip()[:80]}")
    data = dst.read_bytes()
    dst.write_bytes(data[data.find(b"moof") - 4:])        # lose ftyp+moov


@needs_ffmpeg
def test_av1_without_reference_is_explained_as_av1(tmp_path, capsys):
    frag = tmp_path / "lonely" / "clip.mp4"
    frag.parent.mkdir()
    _av1_fragment(frag)

    assert main(["fix", str(frag), "-o", str(tmp_path / "out.mp4"), "--brief"]) == 1
    text = capsys.readouterr()
    shown = text.out + text.err
    assert "SPS/PPS" not in shown, "AV1 has no SPS/PPS; naming them points the user nowhere"
    assert "AV1" in shown and "--reference" in shown
    assert "AV1 stream" in shown, "the AV1 verdict must survive the one-line --brief view"


@needs_ffmpeg
def test_the_explanation_says_the_reference_need_not_be_av1(tmp_path, capsys):
    frag = tmp_path / "lonely" / "clip.mp4"
    frag.parent.mkdir()
    _av1_fragment(frag)
    main(["fix", str(frag), "-o", str(tmp_path / "out.mp4")])
    shown = capsys.readouterr().out
    assert "does not have to be AV1" in shown
