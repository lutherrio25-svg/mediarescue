"""Rebuild a Matroska file whose EBML header, Info and Tracks were cut away.

`ffmpeg -c copy` cannot help here: it refuses to open the file before it reads a single frame
("EBML header parsing failed"), even though every cluster after the damage is intact. The
track table is the only thing that is really gone, so the repair is a transplant: take
Info+Tracks from any healthy file of the same source, keep the clusters from the damaged file,
and rebase their timestamps so playback starts at zero. Nothing is re-encoded and the damaged
file is never written to.
"""
from __future__ import annotations

import math
import mmap
import re
from pathlib import Path

from . import ebml
from .ffmpeg import remux
from .reference import DERIVED_MARKS

EBML_HEADER = 0x1A45DFA3
SEGMENT = 0x18538067
INFO = 0x1549A966
TRACKS = 0x1654AE6B
CLUSTER = 0x1F43B675
TIMESTAMP = 0xE7
SIMPLE_BLOCK = 0xA3
BLOCK_GROUP = 0xA0
BLOCK = 0xA1
REFERENCE_BLOCK = 0xFB
TRACK_ENTRY = 0xAE
TRACK_NUMBER = 0xD7
TIMESTAMP_SCALE = 0x2AD7B1
DEFAULT_SCALE = 1_000_000  # ns per tick when Info says nothing

MATROSKA_SUFFIXES = {".mkv", ".mka", ".webm"}
MAGIC = b"\x1aE\xdf\xa3"
UNKNOWN_SIZE = b"\x01" + b"\xff" * 7
HEADER_WINDOW = 1 << 20  # Info and Tracks live near the front; no need to read a whole film


# --------------------------------------------------------------------------- reading


def _cluster_timestamp(buf, element: ebml.Element) -> tuple[int, int, int] | None:
    """(ticks, byte offset of the value, width in bytes) for a cluster's Timestamp child."""
    for child in ebml.children(buf, element.payload, element.end):
        if child.id == TIMESTAMP:
            width = child.end - child.payload
            if not 1 <= width <= 8:
                return None
            return ebml.uint(buf, child.payload, width), child.payload, width
        if child.id in (SIMPLE_BLOCK, BLOCK_GROUP):
            return None  # blocks come after the timestamp; its absence is the abnormality
    return None


def _is_cluster(buf, pos: int) -> ebml.Element | None:
    element = ebml.element(buf, pos)
    if element is None or element.id != CLUSTER:
        return None
    return element if _cluster_timestamp(buf, element) is not None else None


def _find_cluster(buf, start: int = 0) -> ebml.Element | None:
    """First structurally plausible Cluster at or after `start`."""
    needle = CLUSTER.to_bytes(4, "big")
    pos = start
    while True:
        pos = buf.find(needle, pos)
        if pos < 0:
            return None
        found = _is_cluster(buf, pos)
        if found is not None:
            return found
        pos += 1


def _clusters(buf, start: int) -> list[dict]:
    """Walk clusters forward by declared size; stop at the first hole in the chain."""
    out: list[dict] = []
    pos = start
    while pos < len(buf) - 4:
        element = ebml.element(buf, pos)
        if element is None:
            break
        if element.id == CLUSTER:
            stamp = _cluster_timestamp(buf, element)
            if stamp is None:
                break
            ticks, value_at, width = stamp
            if out and ticks + 1 < out[-1]["ts"]:
                break  # timestamps only move forward: this is debris, not a cluster
            out.append({"offset": element.offset, "payload": element.payload,
                        "end": element.end, "ts": ticks, "ts_offset": value_at,
                        "ts_len": width})
            if element.size is not None:
                pos = element.end
            else:  # live-style cluster: it ends where the next one starts
                nxt = buf.find(CLUSTER.to_bytes(4, "big"), element.payload)
                pos = nxt if nxt > element.offset else len(buf)
            if pos <= element.offset:
                break
        elif element.size is None:
            break
        else:
            pos = element.end
    return out


def _header(buf) -> dict | None:
    """EBML header bytes plus Info/Tracks copied verbatim, or None when either is missing."""
    window = min(len(buf), HEADER_WINDOW)
    first = ebml.element(buf, 0, window)
    if first is None or first.id != EBML_HEADER:
        return None
    segment = ebml.element(buf, first.end, window)
    if segment is None or segment.id != SEGMENT:
        return None
    info = tracks = None
    scale = DEFAULT_SCALE
    for child in ebml.children(buf, segment.payload, min(segment.end, window)):
        if child.id == INFO:
            info = child
            found = _timestamp_scale(buf, child)
            if found:
                scale = found
        elif child.id == TRACKS:
            tracks = child
        elif child.id == CLUSTER:
            break
    if info is None or tracks is None:
        return None
    return {
        "ebml": bytes(buf[0:first.end]),
        "info": bytes(buf[info.offset:info.end]),
        "tracks": bytes(buf[tracks.offset:tracks.end]),
        "scale": scale,
        "track_numbers": _track_numbers(buf, tracks),
    }


def _timestamp_scale(buf, info: ebml.Element) -> int | None:
    for child in ebml.children(buf, info.payload, info.end):
        if child.id == TIMESTAMP_SCALE:
            return ebml.uint(buf, child.payload, child.end - child.payload) or None
    return None


def _track_numbers(buf, tracks: ebml.Element) -> set[int]:
    numbers: set[int] = set()
    for entry in ebml.children(buf, tracks.payload, tracks.end):
        if entry.id != TRACK_ENTRY:
            continue
        for field in ebml.children(buf, entry.payload, entry.end):
            if field.id == TRACK_NUMBER:
                numbers.add(ebml.uint(buf, field.payload, field.end - field.payload))
    return numbers


def _block_tracks(buf, cluster: dict) -> set[int]:
    """Track numbers referenced by the first cluster — what the transplant must cover."""
    numbers: set[int] = set()
    for child in ebml.children(buf, cluster["payload"], cluster["end"]):
        if child.id == SIMPLE_BLOCK:
            number, _ = ebml.read_size(buf, child.payload)
            if number:
                numbers.add(number)
        elif child.id == BLOCK_GROUP:
            for field in ebml.children(buf, child.payload, child.end):
                if field.id == BLOCK:
                    number, _ = ebml.read_size(buf, field.payload)
                    if number:
                        numbers.add(number)
    return numbers


def _block_keyframe(buf, cluster: dict) -> bool | None:
    """Whether the cluster opens on a keyframe; None when no block is present."""
    for child in ebml.children(buf, cluster["payload"], cluster["end"]):
        if child.id == SIMPLE_BLOCK:
            _, number_len = ebml.read_size(buf, child.payload)
            if not number_len or child.payload + number_len + 3 > child.end:
                return None
            flags = buf[child.payload + number_len + 2]
            return bool(flags & 0x80)
        if child.id == BLOCK_GROUP:
            referenced = False
            has_block = False
            for field in ebml.children(buf, child.payload, child.end):
                if field.id == REFERENCE_BLOCK:
                    referenced = True
                elif field.id == BLOCK:
                    has_block = True
            if has_block:
                return not referenced
    return None


def check(path: Path) -> dict:
    """Container verdict used by `scan`: is the header there, and is there anything to save?"""
    verdict = {"matroska": False, "header": False, "tracks": False, "recoverable": False,
               "cut": None, "tracks_used": []}
    try:
        with path.open("rb") as handle:
            if not path.stat().st_size:
                return verdict
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as buf:
                verdict["matroska"] = bytes(buf[:4]) == MAGIC
                header = _header(buf) if verdict["matroska"] else None
                if header is not None:
                    verdict.update(header=True, tracks=True,
                                   tracks_used=sorted(header["track_numbers"]))
                    return verdict
                cut = _find_cluster(buf, 0)
                if cut is not None:
                    verdict.update(recoverable=True, cut=cut.offset, matroska=True)
                return verdict
    except (ValueError, OSError):
        return verdict


def healthy(path: Path) -> bool:
    verdict = check(path)
    return bool(verdict["header"] and verdict["tracks"])


# --------------------------------------------------------------------------- candidates


def _words(name: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", name.lower()) if len(w) > 2}


def _rank(src: Path, candidate: Path) -> tuple[int, float]:
    overlap = len(_words(src.stem) & _words(candidate.stem))
    try:
        ratio = candidate.stat().st_size / max(1, src.stat().st_size)
    except OSError:
        ratio = 1.0
    return (-overlap, abs(math.log(max(ratio, 1e-9))))


def reference_candidates(src: Path, limit: int = 6, depth: int = 2) -> list[Path]:
    """Healthy same-container neighbours, best first: shared words, then similar size."""
    roots = [src.parent]
    for _ in range(max(depth - 1, 0)):
        if roots[-1].parent == roots[-1]:
            break
        roots.append(roots[-1].parent)
    seen: set[str] = set()
    found: list[Path] = []
    for root in roots:
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.suffix.lower() not in MATROSKA_SUFFIXES or entry == src:
                continue
            if entry.name in seen or any(m in entry.name.lower() for m in DERIVED_MARKS):
                continue
            if not entry.is_file():
                continue
            seen.add(entry.name)
            found.append(entry)
    ranked = sorted(found, key=lambda p: _rank(src, p))
    return [p for p in ranked if healthy(p)][:limit]


# --------------------------------------------------------------------------- repair


def salvage(src: Path, reference: Path, out: Path) -> dict:
    """Write a playable Matroska to `out` using `reference`'s track table. Never touches src."""
    result: dict = {"ok": False, "output": str(out), "notes": []}
    try:
        with src.open("rb") as source, reference.open("rb") as ref_file, \
                mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as dmg, \
                mmap.mmap(ref_file.fileno(), 0, access=mmap.ACCESS_READ) as ref:
            return _salvage(dmg, ref, out, result)
    except (ValueError, OSError) as exc:
        result["reason"] = f"could not read the file: {exc}"
        return result


def _salvage(dmg, ref, out: Path, result: dict) -> dict:
    notes = result["notes"]
    header = _header(ref)
    if header is None:
        result["reason"] = ("the reference is not a Matroska file with a readable Info and "
                            "Tracks element: nothing to transplant")
        return result

    first = _find_cluster(dmg, 0)
    if first is None:
        result["reason"] = ("no intact cluster anywhere in the file: the damage reaches the "
                            "last frame, so there is nothing to rebuild from")
        return result
    clusters = _clusters(dmg, first.offset)
    if not clusters:
        result["reason"] = "the cluster chain breaks immediately; no contiguous footage to keep"
        return result

    used = _block_tracks(dmg, clusters[0])
    missing = used - header["track_numbers"]
    if missing:
        result["reason"] = (f"the reference has tracks {sorted(header['track_numbers'])} but the "
                            f"damaged part uses {sorted(missing)}: the transplant would decode as "
                            f"garbage. Point --reference at a file of the same source.")
        return result
    result["tracks"] = sorted(used)

    start = 0
    for index, cluster in enumerate(clusters):
        if _block_keyframe(dmg, cluster):
            start = index
            break
    base = clusters[start]["ts"]

    scale = header["scale"]
    if scale != DEFAULT_SCALE:
        notes.append(f"the reference declares a timestamp scale of {scale} ns/tick; if the "
                     f"damaged file used the default, check the result's duration")

    stage = out.with_name(out.stem + ".stage" + out.suffix)
    try:
        written = _write_stage(stage, header, dmg, clusters, start, base)
        ok, log = remux(stage, out)
        if not ok:
            result["reason"] = f"remux of the rebuilt stream failed: {log[-300:]}"
            return result
    finally:
        stage.unlink(missing_ok=True)

    dropped = base * scale / 1e9
    result.update(ok=True, dropped_seconds=round(dropped, 3), clusters=written,
                  dropped_clusters=start)
    notes.append(f"rebuilt Info and Tracks for {len(header['track_numbers'])} track(s) from the "
                 f"reference: the first {dropped:.3f}s were cut away with the original header")
    if start:
        notes.append(f"started at cluster #{start}: the clusters before it open on a "
                     f"non-keyframe and would not decode")
    notes.append("the old Cues were dropped with the header; the remux writes a fresh index")
    return result


def _write_stage(stage: Path, header: dict, dmg, clusters: list[dict], start: int,
                 base: int) -> int:
    """EBML header + unknown-size Segment + Info/Tracks, then the damaged clusters rebased to 0."""
    written = 0
    with stage.open("wb") as fh:
        fh.write(header["ebml"])
        fh.write(SEGMENT.to_bytes(4, "big") + UNKNOWN_SIZE)
        fh.write(header["info"])
        fh.write(header["tracks"])
        cursor = clusters[start]["offset"]
        for cluster in clusters[start:]:
            chunk = bytearray(dmg[cursor:cluster["end"]])
            if cluster["ts_len"] and cluster["ts_offset"] >= cursor:
                at = cluster["ts_offset"] - cursor
                value = max(0, cluster["ts"] - base)
                chunk[at:at + cluster["ts_len"]] = value.to_bytes(cluster["ts_len"], "big")
            fh.write(chunk)
            cursor = cluster["end"]
            written += 1
    return written
