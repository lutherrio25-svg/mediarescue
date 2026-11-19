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


def _size_alone(src: Path, candidate: Path) -> bool:
    """Last-resort test: a size within 5% of the damaged file, with nothing else agreeing."""
    try:
        mine, theirs = src.stat().st_size, candidate.stat().st_size
    except OSError:
        return False
    return bool(mine) and 0.95 <= theirs / mine <= 1.05


def _words(name: str) -> set[str]:
    """Lower-case words of a filename, without the download-site noise around them."""
    from .source import clean_title
    return {w for w in clean_title(name).lower().replace("_", " ").split() if len(w) > 2}


def _same_upload(src: Path, candidate: Path) -> bool:
    """Is `candidate` plausibly the same recording as `src`, judged by name and size alone?

    Needed when the damaged file cannot say what codec it holds (its sample table is gone, so
    the payload sniff has nothing to lock onto). Size within the same band the online search
    uses, plus at least one shared word of the filename, is enough to *try* it -- the repair
    is still verified by a full decode afterwards.
    """
    try:
        mine, theirs = src.stat().st_size, candidate.stat().st_size
    except OSError:
        return False
    if not mine or not theirs or not 0.75 <= theirs / mine <= 1.35:
        return False
    shared = _words(src.stem) & _words(candidate.stem)
    return bool(shared) and (len(shared) > 1 or max(len(w) for w in shared) >= 4)


DERIVED_MARKS = (".repaired.", ".stage.", ".trimmed.", ".salvaged.", ".flattened.", ".grafted.")


def _is_derived(path: Path) -> bool:
    """Files this tool writes must never be borrowed as a reference.

    A `.repaired.mp4` sitting beside the damaged original is this program's own output: grafting
    its index back would at best rebuild the same file and at worst chain one repair onto another.
    """
    lowered = path.name.lower()
    return any(mark in lowered for mark in DERIVED_MARKS)


def find_reference(src: Path, limit: int = 40, depth: int = 2) -> Path | None:
    """A healthy file near `src` that can lend its index, or None.

    The codec is the strict rule when the damaged file can still name it. When it cannot --
    moov gone and the payload sniff finds no anchored stream -- a healthy file with the same
    name and a nearly identical size is tried instead, because that is the same evidence the
    online search would have to go on.

    Searches the same folder, its sub-folders, then one level up (a recorder that writes
    segments into dated folders keeps the intact file either below or beside the damaged one).
    Everything is capped, so a huge tree cannot turn this into a full-disk scan.
    """
    wanted = source_video_codec(src)

    roots = [src.parent]
    if depth >= 2 and src.parent.parent != src.parent:
        roots.append(src.parent.parent)

    candidates: list[Path] = []
    for root in roots:
        try:
            candidates += [p for p in sorted(root.iterdir())
                           if p.is_file() and p != src and p.suffix.lower() in MEDIA_SUFFIXES]
            subdirs = [p for p in sorted(root.iterdir()) if p.is_dir()][:20]
        except OSError:
            continue
        for sub in subdirs:
            try:
                candidates += [p for p in sorted(sub.rglob("*"))
                               if p.is_file() and p != src and p.suffix.lower() in MEDIA_SUFFIXES]
            except OSError:
                continue
            if len(candidates) >= limit * 4:
                break

    candidates = [p for p in candidates if not _is_derived(p)]
    fallback: Path | None = None
    for candidate in candidates[:limit]:
        if wanted is not None:
            if _mapped_codec(candidate) != wanted:
                continue
        elif not _same_upload(src, candidate):
            # No codec, no shared name: only an almost identical size is left to go on, and that
            # is a guess -- kept as a last resort, behind anything that looks more like a twin.
            if fallback is None and _size_alone(src, candidate):
                if diagnose(candidate, hash_file=False).severity == Severity.HEALTHY:
                    fallback = candidate
            continue
        if diagnose(candidate, hash_file=False).severity != Severity.HEALTHY:
            continue
        return candidate
    return fallback
