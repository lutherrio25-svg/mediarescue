"""Pick a reference file automatically: a healthy sibling with the same video codec.

Requiring `--reference` by hand is the last manual step in a batch: a folder of segments from
one recorder usually already contains an intact file that can lend its index. Choosing it by
codec, not by name or size, is what keeps `flatten` from producing garbage.
"""
from __future__ import annotations

import mmap
from pathlib import Path

from .av1 import sniff_video_codec
from .diagnose import MEDIA_SUFFIXES, diagnose
from .mp4 import find, iter_all, read_boxes
from .report import Severity

STSD_TO_CODEC = {"avc1": "h264", "avc3": "h264", "av01": "av1", "hvc1": "hevc", "hev1": "hevc"}


def _mapped_codec(path: Path) -> str | None:
    """Video codec named by a file's own stsd entry, or None."""
    try:
        with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            moov = find(mm, "moov")
            if moov is None:
                return None
            for trak in read_boxes(mm, moov.payload, moov.end):
                if trak.type != "trak":
                    continue
                kids = list(iter_all(mm, trak.payload, trak.end))
                hdlr = next((b for b, _ in kids if b.type == "hdlr"), None)
                if hdlr is None or bytes(mm[hdlr.payload + 8:hdlr.payload + 12]) != b"vide":
                    continue
                stsd = next((b for b, _ in kids if b.type == "stsd"), None)
                if stsd is None:
                    continue
                entries = read_boxes(mm, stsd.payload + 8, stsd.end)
                if entries:
                    return STSD_TO_CODEC.get(entries[0].type)
    except (ValueError, OSError):
        return None
    return None


def source_video_codec(path: Path) -> str | None:
    """Codec of the damaged file, sniffed from its own bytes (its index may be gone)."""
    try:
        with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            if find(mm, "moof") is not None:
                from .fragmented import parse_fragments
                for track in parse_fragments(mm).values():
                    probe = [mm[s.offset:s.offset + s.size] for s in track.samples[:60]]
                    codec = sniff_video_codec(probe)
                    if codec != "unknown":
                        return codec
                return None
            from .carve import find_stream_start
            return "h264" if find_stream_start(mm) is not None else None
    except (ValueError, OSError):
        return None


def find_reference(src: Path, limit: int = 40) -> Path | None:
    """A healthy file beside `src` whose video codec matches, or None."""
    wanted = source_video_codec(src)
    if wanted is None:
        return None
    siblings = [p for p in sorted(src.parent.iterdir())
                if p.is_file() and p != src and p.suffix.lower() in MEDIA_SUFFIXES]
    for candidate in siblings[:limit]:
        if _mapped_codec(candidate) != wanted:
            continue
        if diagnose(candidate, hash_file=False).severity != Severity.HEALTHY:
            continue
        return candidate
    return None
