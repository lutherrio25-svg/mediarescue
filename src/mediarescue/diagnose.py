"""Damage classification.

Order matters: emptiness and total header loss are decided before any repair
hint, so the CLI never promises a remux for a file that contains no stream.
"""
from __future__ import annotations

import struct
from pathlib import Path

from .mp4 import find, read_boxes, read_head_tail, zero_ratio
from .report import Atom, Diagnosis, Severity, sha256_of
from .trim import find_tail_moov

MEDIA_SUFFIXES = {
    ".mp4", ".m4v", ".mov", ".m4a", ".3gp",
    ".mp3", ".wav", ".flac", ".ogg", ".aac", ".mka", ".mkv", ".avi",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
}

MP4_SUFFIXES = {".mp4", ".m4v", ".mov", ".m4a", ".3gp"}


def diagnose(path: str | Path, hash_file: bool = True, deep: bool = False,
             deep_seconds: float | None = None) -> Diagnosis:
    """Structural verdict, optionally proven by decoding the file (deep=True)."""
    d = _structural(path, hash_file)
    if not deep or d.severity in (Severity.EMPTY, Severity.UNSUPPORTED):
        return d
    if Path(path).suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return d
    from .ffmpeg import decode_check, have_ffmpeg
    if not have_ffmpeg():
        d.notes.append("decode check skipped: ffmpeg not found on PATH")
        return d
    ok, lines, first = decode_check(Path(path), deep_seconds)
    if ok:
        d.notes.append("decode check: clean")
    else:
        d.notes.append(f"decode check: {lines} error line(s) — {first}")
        if d.severity == Severity.HEALTHY:
            # The index is fine but the payload is not: same class as an overwritten head.
            d.severity = Severity.CORRUPT_PAYLOAD
    return d


def _structural(path: str | Path, hash_file: bool = True) -> Diagnosis:
    p = Path(path)
    if p.is_dir():
        return Diagnosis(str(p), 0, Severity.UNSUPPORTED,
                         notes=["directory, not a file: scan its contents instead"])
    size = p.stat().st_size if p.exists() else 0
    if size == 0:
        return Diagnosis(str(p), 0, Severity.EMPTY, notes=["0 bytes: no payload to rebuild from"])

    head, tail = read_head_tail(p)
    ratio = zero_ratio(head + tail)
    suffix = p.suffix.lower()
    atoms = [Atom(b.type, b.offset, b.size) for b in read_boxes(head)]

    if suffix not in MEDIA_SUFFIXES and not atoms:
        return Diagnosis(str(p), size, Severity.UNSUPPORTED,
                         zero_byte_ratio=ratio, notes=[f"unhandled suffix {suffix!r}"])

    if suffix in (".png", ".jpg", ".jpeg"):
        sev, note = _image_check(head, tail, suffix)
        return Diagnosis(str(p), size, sev, zero_byte_ratio=round(ratio, 4), notes=[note],
                         sha256=sha256_of(p) if hash_file else "")

    # Pure audio/MKV etc. are handed to ffmpeg as-is; structure walk is MP4-only.
    if suffix == ".avi":
        from .avi import check as avi_check
        info = avi_check(p)
        if info is None:
            return Diagnosis(str(p), size, Severity.HEADERLESS, zero_byte_ratio=round(ratio, 4),
                             notes=["not a RIFF/AVI file: the header is gone"],
                             sha256=sha256_of(p) if hash_file else "")
        if not info["has_index"]:
            return Diagnosis(str(p), size, Severity.CORRUPT_INDEX, zero_byte_ratio=round(ratio, 4),
                             notes=["idx1 is missing: the index was lost or the file was cut "
                                    "short. AVI needs no reference — remux rescans movi and "
                                    "writes a fresh index"],
                             sha256=sha256_of(p) if hash_file else "")
        if info["bad"]:
            return Diagnosis(str(p), size, Severity.CORRUPT_INDEX, zero_byte_ratio=round(ratio, 4),
                             notes=[f"{info['bad']} of {info['entries']} idx1 entries point at "
                                    "the wrong chunk: players follow the bad index into frame "
                                    "data, so seeks and playback break even though movi is "
                                    "intact — remux with +ignidx rescans instead"],
                             sha256=sha256_of(p) if hash_file else "")
        return Diagnosis(str(p), size, Severity.HEALTHY, zero_byte_ratio=round(ratio, 4),
                         notes=[f"RIFF/AVI index intact: {info['entries']} entries, each "
                                "pointing at its chunk",
                                "payload not inspected: use --deep to decode it"],
                         sha256=sha256_of(p) if hash_file else "")

    if not atoms and suffix not in MP4_SUFFIXES:
        return Diagnosis(str(p), size, Severity.HEALTHY if ratio < 0.5 else Severity.MISSING_ATOMS,
                         zero_byte_ratio=ratio,
                         notes=["non ISO-BMFF container; use ffmpeg remux/transcode",
                                "payload not inspected: damage inside this format is not "
                                "detected — verify with a full decode"])

    types = {a.type for a in atoms}
    notes: list[str] = []
    gaps = _zero_gaps(head)

    truncated = _oversized_mdat(head, size)
    tail_moov = None if "moov" in types else find_tail_moov(p, tail)

    if truncated and tail_moov is not None:
        sev = Severity.MISSING_TRAILER
        notes.append(f"mdat declares {truncated:,} bytes but file is {size:,}; "
                     f"moov sits inside that dead range at {tail_moov:,} — trim mdat to expose it")
    elif "ftyp" not in types and "moof" in types:
        sev = Severity.FRAGMENTED
        notes.append("starts at moof/mdat: ftyp+moov lost (fragmented MP4, e.g. DASH/CMAF segment)")
    elif "moov" in types and not _has_stbl(head):
        sev = Severity.MISSING_ATOMS
        notes.append("moov present but no stbl sample sizes")
    elif "moov" not in types and tail_moov is None:
        sev = Severity.MISSING_ATOMS if atoms or ratio < 0.4 else Severity.HEADERLESS
        notes.append("no reachable moov anywhere: sample table lost" if atoms else
                     "no parsable box at offset 0")
    elif atoms:
        sev = Severity.HEALTHY
        notes.append("top-level boxes parse cleanly" if tail_moov is None else
                     "moov stored at end of file (normal for non-faststart MP4)")
        # Boxes parsing says nothing about the pictures. A head overwritten with junk keeps a
        # perfect index, so a probe passes while every early frame is garbage.
        if suffix in MP4_SUFFIXES:
            from .tables import head_is_junk
            if head_is_junk(p):
                sev = Severity.CORRUPT_PAYLOAD
                notes.append("first video sample does not parse: the head was overwritten; "
                             "salvage will cut to the first clean IDR")
    else:
        sev = Severity.HEADERLESS if ratio > 0.4 else Severity.MISSING_ATOMS
        notes.append("no parsable box at offset 0")

    # A late moov with a zeroed head is the classic interrupted-copy pattern.
    if sev == Severity.HEADERLESS and tail_moov is not None:
        sev = Severity.MISSING_ATOMS
        notes.append(f"moov found at {tail_moov:,} but the head is zero-filled — "
                     "mdat payload is gone; only a same-source re-download fully restores this")

    return Diagnosis(
        str(p), size, sev, atoms=atoms[:24], gaps=gaps, zero_byte_ratio=round(ratio, 4),
        notes=notes, sha256=sha256_of(p) if hash_file else "",
    )


def _oversized_mdat(head: bytes, size: int) -> int | None:
    """Declared mdat size when it runs past EOF (interrupted copy/download)."""
    i = head.find(b"mdat")
    while i >= 4:
        declared = struct.unpack_from(">I", head, i - 4)[0]
        if declared > 8 and i - 4 + declared > size:
            return declared
        i = head.find(b"mdat", i + 4)
    return None


def _image_check(head: bytes, tail: bytes, suffix: str) -> tuple[Severity, str]:
    if suffix == ".png":
        if not head.startswith(b"\x89PNG\r\n\x1a\n"):
            return Severity.HEADERLESS, "PNG signature missing"
        if b"IEND" not in tail[-64:]:
            return Severity.MISSING_TRAILER, "PNG has no IEND chunk: file truncated"
        return Severity.HEALTHY, "PNG signature and IEND present"
    if not head.startswith(b"\xff\xd8"):
        return Severity.HEADERLESS, "JPEG SOI marker missing"
    if not tail.rstrip(b"\x00").endswith(b"\xff\xd9"):
        return Severity.MISSING_TRAILER, "JPEG has no EOI marker: file truncated"
    return Severity.HEALTHY, "JPEG SOI/EOI present"


def _has_stbl(head: bytes) -> bool:
    moov = find(head, "moov")
    if moov is None:
        return False
    from .mp4 import iter_all
    return any(b.type == "stbl" for b, _ in iter_all(head, moov.payload, moov.end))


def _zero_gaps(buf: bytes, min_run: int = 1 << 20) -> list[dict]:
    """Long zero runs inside the file = sectors that failed to copy."""
    gaps = []
    i, n = 0, len(buf)
    while i < n:
        if buf[i] == 0:
            j = i
            while j < n and buf[j] == 0:
                j += 1
            if j - i >= min_run:
                gaps.append({"offset": i, "length": j - i})
            i = j
        else:
            i += 1
    return gaps[:32]
