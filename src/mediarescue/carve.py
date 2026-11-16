"""Rebuild a playable MP4 from a bare H.264 payload, without a reference file.

When the index (`moov`) is gone the sample table is unrecoverable, but the pictures are still
there: an MP4 `mdat` holds length-prefixed NAL units back to back. Find where that chain really
starts, walk it, and hand ffmpeg an Annex-B stream — ffmpeg then reads SPS/PPS for the geometry
and writes a fresh index.

Honest limits: this recovers VIDEO only (raw AAC frames have no delimiters, so audio needs the
index), and the frame rate cannot be known without VUI timing, so it is assumed.
"""
from __future__ import annotations

import re
import struct
import tempfile
from pathlib import Path

from .ffmpeg import decode_check, probe, run

NAL_TYPES = set(range(1, 13))  # 1-5 slices, 6 SEI, 7 SPS, 8 PPS, 9 AUD, 10-12 end of seq/stream
# A NAL header is 1 byte: forbidden_zero_bit clear, nal_ref_idc any, type 1..12.
_HEADER_BYTES = b"".join(
    bytes([(ref << 5) | t]) for ref in range(8) for t in NAL_TYPES)
CANDIDATE = re.compile(b"(?s).{4}[" + re.escape(_HEADER_BYTES) + b"]")
MIN_CHAIN = 2   # NALs that must tile before we believe a stream starts (plus an IDR)
MIN_RESYNC = 3  # NALs that must tile before we accept a gap and carry on


def _nal(buf, off: int, end: int) -> int | None:
    """Length of the length-prefixed NAL at `off`, or None when it does not fit."""
    if off + 5 > end:
        return None
    length = struct.unpack_from(">I", buf, off)[0]
    if length == 0 or off + 4 + length > end:
        return None
    header = buf[off + 4]
    if header & 0x80 or (header & 0x1F) not in NAL_TYPES:
        return None
    return length


def _chain(buf, off: int, end: int, want: int) -> tuple[int, bool, bool]:
    """(NALs that tile from `off`, whether an IDR is among them, whether slices are)."""
    pos, count, idr, slices = off, 0, False, False
    while count < 64:
        length = _nal(buf, pos, end)
        if length is None:
            break
        kind = buf[pos + 4] & 0x1F
        idr = idr or kind == 5
        slices = slices or kind in (1, 5)
        pos += 4 + length
        count += 1
    return count, idr, slices


def _next_candidate(buf, start: int, end: int, want: int, need_idr: bool) -> int | None:
    """First offset that tiles into real video NALs.

    Requiring slice NALs is what keeps the walker out of the audio samples: AAC payloads happily
    produce a few length-prefixed bytes that look like headers, and following those poisons the
    stream. A chain (not a lone NAL) is required for the same reason — accepting a single
    plausible-looking NAL injected SEI garbage into the output during development.
    """
    for match in CANDIDATE.finditer(buf, start, end):
        count, idr, slices = _chain(buf, match.start(), end, want)
        if count >= want and slices and (idr or not need_idr):
            return match.start()
    return None


def find_stream_start(buf) -> int | None:
    """Offset of the first believable length-prefixed NAL chain that contains an IDR."""
    return _next_candidate(buf, 0, len(buf), MIN_CHAIN, need_idr=True)


def extract_annexb(buf, start: int) -> tuple[bytes, int, int, set[int]]:
    """(Annex-B stream, NAL count, bytes skipped, NAL types seen) walking from `start`.

    An MP4 mdat interleaves audio samples with video samples, so the NAL chain breaks every few
    hundred milliseconds. Stopping there would recover a fraction of a second.
    """
    out = bytearray()
    pos, end, count, skipped = start, len(buf), 0, 0
    types: set[int] = set()
    while pos < end:
        length = _nal(buf, pos, end)
        if length is None:
            nxt = _next_candidate(buf, pos + 1, end, MIN_RESYNC, need_idr=False)
            if nxt is None:
                break
            skipped += nxt - pos
            pos = nxt
            continue
        types.add(buf[pos + 4] & 0x1F)
        out += b"\x00\x00\x00\x01" + bytes(buf[pos + 4:pos + 4 + length])
        pos += 4 + length
        count += 1
    return bytes(out), count, skipped, types


def carve(src: Path, out: Path, fps: float = 25.0, timeout: int = 1800) -> dict:
    notes: list[str] = []
    buf = src.read_bytes()

    start = find_stream_start(buf)
    if start is None:
        return {"ok": False, "reason": "no length-prefixed H.264 stream found in this file",
                "notes": ["the payload may be AV1/HEVC/audio-only, or the head is overwritten"]}
    if start:
        notes.append(f"stream starts at byte {start:,}; {start:,} bytes before it were skipped")

    annexb, count, skipped, types = extract_annexb(buf, start)
    if count < MIN_CHAIN:
        return {"ok": False, "reason": f"only {count} NAL units found; too little to rebuild"}
    if not {7, 8} <= types:
        # The usual case: encoders store SPS/PPS once, in the moov's avcC. Gone with the index.
        return {"ok": False, "notes": notes,
                "reason": "the stream carries no SPS/PPS (NAL 7/8): the encoder stored them in "
                          "the index, so nothing can decode these pictures without a "
                          "--reference file from the same encoder"}
    notes.append(f"{count:,} NAL units carried over")
    if skipped:
        notes.append(f"{skipped:,} bytes of non-video data (interleaved audio, junk) skipped")
        if len(annexb) < 0.5 * len(buf):
            return {"ok": False, "notes": notes,
                    "reason": f"only {100 * len(annexb) / max(len(buf), 1):.0f}% of the payload "
                              f"could be delimited: the samples are interleaved one-per-chunk, "
                              f"which cannot be split without the index — supply --reference "
                              f"from the same encoder"}
    notes.append(f"frame rate assumed {fps:g} fps: the source's timing information is lost "
                 f"with the index (use --fps to correct it)")

    with tempfile.TemporaryDirectory(prefix="mediarescue-carve-") as tmp:
        raw = Path(tmp) / "stream.h264"
        raw.write_bytes(annexb)
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y",
               "-f", "h264", "-framerate", f"{fps:g}", "-i", str(raw),
               "-c", "copy", "-movflags", "+faststart", str(out)]
        p = run(cmd, timeout)
        if p.returncode != 0 or not out.exists():
            return {"ok": False, "reason": f"ffmpeg could not rebuild the stream: "
                                           f"{(p.stderr or '').strip()[:300]}", "notes": notes}

    info = probe(out)
    clean, lines, first = decode_check(out)
    notes.append("video only: audio frames carry no delimiters once the index is gone, so "
                 "they cannot be carved")
    if not clean:
        notes.append(f"decode check: {lines} error line(s) — {first}")
        notes.append("samples are probably interleaved one-per-chunk; carving cannot delimit "
                     "them without the index")
        return {"ok": False, "output": str(out), "strategy": "carve", "nals": count,
                "probe": info, "verify": {"ok": False, "decode_errors": lines}, "notes": notes,
                "reason": "the rebuilt stream does not decode cleanly — supply --reference "
                          "from the same encoder"}
    return {"ok": bool(info.get("ok")), "output": str(out), "strategy": "carve",
            "nals": count, "bytes_carved": len(annexb), "fps": fps,
            "probe": info, "verify": {"ok": clean, "decode_errors": lines}, "notes": notes}
