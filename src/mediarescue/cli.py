from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, ui
from .diagnose import MEDIA_SUFFIXES, diagnose
from .ffmpeg import have_ffmpeg, probe, remux
from .report import Severity

SEVERITY_COLOUR = {
    Severity.HEALTHY: "green",
    Severity.CORRUPT_PAYLOAD: "yellow",
    Severity.MISSING_TRAILER: "yellow",
    Severity.FRAGMENTED: "yellow",
    Severity.MISSING_ATOMS: "yellow",
    Severity.HEADERLESS: "red",
    Severity.EMPTY: "red",
    Severity.UNSUPPORTED: "red",
}

SKIP_DIRS = {
    "windows", "program files", "program files (x86)", "programdata", "$recycle.bin",
    "system volume information", "appdata", "node_modules", ".git", ".venv", "venv",
    "site-packages", "winsxs", "driverstore",
}


def iter_media(paths: list[str]):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in MEDIA_SUFFIXES:
                    # Only the path *below* the requested root is filtered, so a
                    # root that itself lives under AppData still scans.
                    if not any(part.lower() in SKIP_DIRS for part in f.relative_to(p).parts):
                        yield f
        elif p.is_file():
            yield p
        else:
            print(f"skip (not found): {p}", file=sys.stderr)


def cmd_scan(args) -> int:
    rows = []
    missing = [p for p in args.paths if not Path(p).exists()]
    if missing:
        for m in missing:
            print(f"not found: {m}", file=sys.stderr)
        return 2
    paint = ui.colour_for(ui.enabled(override=False if args.no_color else None))
    for f in iter_media(args.paths):
        d = diagnose(f, hash_file=args.hash, deep=args.deep, deep_seconds=args.deep_seconds)
        rows.append(d)
        if not args.json:
            label = paint(f"{d.severity.value:<16}", SEVERITY_COLOUR.get(d.severity, ""))
            print(f"{label} {d.size:>12,}  {'yes' if d.recoverable else 'no ':<3}  {f}")
    if args.json:
        print(json.dumps([d.to_dict() for d in rows], indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps([d.to_dict() for d in rows], indent=2), encoding="utf-8")
        print(f"\nwrote {args.out} ({len(rows)} files)", file=sys.stderr)
    if not args.json:
        bad = [d for d in rows if d.severity != Severity.HEALTHY]
        print(f"\n{len(rows)} scanned, {len(bad)} damaged, "
              f"{sum(1 for d in bad if d.recoverable)} repairable, "
              f"{sum(1 for d in bad if d.needs_reference)} need a reference file")
    return 0


def cmd_doctor(args) -> int:
    from .doctor import doctor
    colour = ui.enabled(override=False if getattr(args, "no_color", False) else None)
    code, text = doctor(Path(args.workdir) if args.workdir else None, colour=colour)
    print(text)
    return code


def _pick_strategy(severity: Severity, requested: str) -> str:
    if requested != "auto":
        return requested
    return {
        # salvage is trim + remux + (only when needed) dropping a junk head, so it is a strict
        # superset of trim: same result on a clean tail, usable file when the head was overwritten.
        Severity.MISSING_TRAILER: "salvage",
        Severity.CORRUPT_PAYLOAD: "salvage",
        Severity.FRAGMENTED: "flatten",
    }.get(severity, "remux" if severity == Severity.HEALTHY else "graft")


def _repair(src: Path, out: Path, args, d) -> dict:
    """Run one strategy and verify the result. Prints nothing; returns the result dict."""
    strategy = _pick_strategy(d.severity, args.strategy)

    # Damage inside an MP3 cannot be fixed by rewriting the container; mp3val can rebuild the
    # frame stream, so use it when it is installed and the header scan already saw damage.
    from . import external
    mp3_damaged = (src.suffix.lower() in (".mp3", ".mp2")
                   and d.severity in (Severity.CORRUPT_PAYLOAD, Severity.MISSING_ATOMS))
    mp3val = external.mp3val_path() if mp3_damaged else None
    if mp3val and args.strategy == "auto":
        strategy = "mp3val"

    if strategy == "mp3val":
        if mp3val is None:
            result = {"ok": False, "reason": "mp3val was found a moment ago but is gone now"}
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(src.read_bytes())
            ok, log = external.run_mp3val(mp3val, out)
            result = {"ok": ok, "output": str(out) if ok else "", "log": log, "tool": "mp3val"}
    elif strategy == "trim":
        from .trim import trim_mdat
        trimmed = out.with_name(out.stem + ".trimmed" + out.suffix)
        t = trim_mdat(src, trimmed)
        if not t["ok"]:
            return {"ok": False, "strategy": "trim", "reason": t.get("reason"), "notes": []}
        ok, log = remux(trimmed, out)
        if not ok and probe(trimmed).get("ok"):
            result = {"ok": True, "output": str(trimmed),
                      "note": "remux failed; trimmed copy verified instead"}
        else:
            trimmed.unlink(missing_ok=True)
            result = {"ok": ok, "output": str(out) if ok else "", "log": log[-400:]}
        result.update({"strategy": "trim", "trim": t})
    elif strategy == "salvage":
        from .salvage import salvage
        result = salvage(src, out)
    elif strategy == "remux":
        ok, log = remux(src, out)
        result = {"ok": ok, "output": str(out) if ok else "", "log": log[-400:]}
    elif strategy == "carve":
        from .carve import carve
        result = carve(src, out, fps=args.fps)
    elif strategy in ("flatten", "graft") and not args.reference:
        # No --reference: look for a healthy sibling with the same codec before giving up on
        # the index entirely and falling back to carve.
        from .reference import find_reference
        auto = find_reference(src)
        if auto is None:
            from .carve import carve
            result = carve(src, out, fps=args.fps)
            if not result.get("ok"):
                result["note"] = ("no --reference given and no matching sibling found, so "
                                  "carve was tried instead")
        elif strategy == "flatten":
            from .fragmented import flatten
            result = flatten(src, auto, out, force_timescale=args.timescale)
            result.setdefault("notes", []).append(f"reference picked automatically: {auto.name}")
            result["reference"] = str(auto)
        else:
            from .graft import graft
            r = graft(src, auto, out)
            result = {"ok": r.ok, "output": r.output, "tracks": r.tracks,
                      "samples": r.samples, "reason": r.reason,
                      "reference": str(auto),
                      "notes": [f"reference picked automatically: {auto.name}"]}
    elif not args.reference:
        # Index gone and no reference: rebuild from the payload itself (video only).
        from .carve import carve
        result = carve(src, out, fps=args.fps)
        if not result.get("ok"):
            result["note"] = "no --reference given, so carve was tried instead"
    else:
        ref = Path(args.reference)
        if strategy == "flatten":
            from .fragmented import flatten
            result = flatten(src, ref, out, force_timescale=args.timescale)
        else:
            from .graft import graft
            r = graft(src, ref, out)
            result = {"ok": r.ok, "output": r.output, "tracks": r.tracks,
                      "samples": r.samples, "reason": r.reason}
    result.setdefault("strategy", strategy)
    if mp3_damaged and mp3val is None:
        result.setdefault("notes", []).append(
            "mp3val is not installed: damaged MPEG frames inside were left alone "
            "(set MEDIARESCUE_MP3VAL or put mp3val on PATH)")

    if result.get("ok"):
        result["probe"] = probe(Path(result["output"]))
        if not args.no_verify:
            from .ffmpeg import decode_check
            clean, lines, first = decode_check(Path(result["output"]))
            result["verify"] = {"ok": clean, "decode_errors": lines}
            if not clean:
                result["verify"]["first_error"] = first
        result["source_untouched"] = True
    return result


def _result_ok(result: dict) -> bool:
    return bool(result.get("ok")) \
        and result.get("probe", {}).get("ok", True) \
        and result.get("verify", {}).get("ok", True)


def cmd_fix(args) -> int:
    targets = [Path(p) for p in args.file]
    missing = [t for t in targets if not t.exists()]
    if missing:
        for m in missing:
            print(f"not found: {m}", file=sys.stderr)
        return 2
    if len(targets) == 1 and targets[0].is_file():
        return _fix_single(targets[0], args)
    return _fix_batch(targets, args)


def _fix_single(src: Path, args) -> int:
    paint = ui.colour_for(ui.enabled(override=False if getattr(args, "no_color", False) else None))
    d = diagnose(src, hash_file=False, deep=args.deep)
    label = paint(d.severity.value, SEVERITY_COLOUR.get(d.severity, ""))
    print(f"diagnosis: {label} — {'; '.join(d.notes)}")
    if d.severity in (Severity.EMPTY, Severity.UNSUPPORTED, Severity.HEADERLESS):
        print(f"refusing: {d.severity.value} is not repairable by this tool "
              f"(no stream payload to rebuild from)", file=sys.stderr)
        return 2
    if not have_ffmpeg():
        print("ffmpeg/ffprobe not found on PATH", file=sys.stderr)
        return 3

    out = Path(args.out) if args.out else src.with_name(src.stem + ".repaired" + src.suffix)
    if not getattr(args, "quiet", False):
        # A 300 MB repair takes minutes; say what is happening instead of going silent.
        print(paint(f"working: {_pick_strategy(d.severity, args.strategy)} → {out.name}", "dim"),
              file=sys.stderr)
    result = _repair(src, out, args, d)
    print(json.dumps(result, indent=2))
    return 0 if _result_ok(result) else 1


def _fix_batch(targets: list[Path], args) -> int:
    if args.out:
        print("refusing: -o names one output file; use --out-dir for a batch", file=sys.stderr)
        return 2
    if not have_ffmpeg():
        print("ffmpeg/ffprobe not found on PATH", file=sys.stderr)
        return 3

    paint = ui.colour_for(ui.enabled(override=False if getattr(args, "no_color", False) else None))
    out_dir = Path(args.out_dir) if args.out_dir else None
    files = list(iter_media([str(t) for t in targets]))
    repaired, failed, skipped = [], [], []
    total = len(files)

    for index, f in enumerate(files, 1):
        counter = paint(f"[{index}/{total}]".ljust(9), "dim")
        d = diagnose(f, hash_file=False, deep=args.deep)
        if d.severity in (Severity.EMPTY, Severity.UNSUPPORTED, Severity.HEADERLESS):
            skipped.append(f)
            if not args.quiet:
                print(f"  {counter} {paint('skip  ', 'dim')} {f.name:<44} "
                      f"{d.severity.value}: nothing to rebuild from")
            continue
        if d.severity == Severity.HEALTHY and not args.force:
            skipped.append(f)
            if not args.quiet:
                print(f"  {counter} {paint('ok    ', 'green')} {f.name:<44} healthy, left untouched")
            continue

        out = ((out_dir / (f.stem + ".repaired" + f.suffix)) if out_dir
               else f.with_name(f.stem + ".repaired" + f.suffix))
        if not args.quiet and sys.stdout.isatty():
            # Only on a real terminal: a carriage-return progress line is noise in a log file.
            print(f"  {counter} {paint('work  ', 'cyan')} {f.name:<44} repairing…", end="\r")
        result = _repair(f, out, args, d)
        if _result_ok(result):
            repaired.append((f, result))
            dropped = result.get("dropped_seconds")
            extra = paint(f"{dropped:g}s dropped", "yellow") if dropped else ""
            if not args.quiet:
                print(f"  {counter} {paint('fixed ', 'green')} {f.name:<44} "
                      f"{result['strategy']:<8} {extra}")
            if args.verbose:
                for note in result.get("notes", []):
                    print(paint(f"            {note}", "dim"))
        else:
            failed.append((f, result))
            why = result.get("reason") or (
                f"{result.get('verify', {}).get('decode_errors', 0)} decode errors in the output")
            print(f"  {counter} {paint('FAILED', 'red')} {f.name:<44} {paint(why, 'red')}")

    summary = (f"{total} files: {len(repaired)} repaired, {len(failed)} failed, "
               f"{len(skipped)} skipped")
    print("\n" + (paint(summary, "red") if failed else paint(summary, "green")))
    if out_dir:
        print(paint(f"output folder: {out_dir}", "dim"))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mediarescue",
                                 description="Diagnose and repair damaged media; originals are never modified.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="classify damage across files or directories")
    s.add_argument("paths", nargs="+")
    s.add_argument("--json", action="store_true")
    s.add_argument("--hash", action="store_true", help="sha256 every file (slow on large trees)")
    s.add_argument("--out", help="also write the full JSON report here")
    s.add_argument("--deep", action="store_true",
                   help="decode each file instead of only reading headers (slow, catches "
                        "damage inside MP3/MKV/AVI and junk payloads in MP4)")
    s.add_argument("--deep-seconds", type=float,
                   help="with --deep: stop decoding after N seconds per file")
    s.add_argument("--no-color", action="store_true", help="never emit ANSI colour")
    s.set_defaults(func=cmd_scan)

    d = sub.add_parser("doctor", help="check that this machine can run a repair")
    d.add_argument("--workdir",
                   help="folder repairs will write into (default: current directory)")
    d.add_argument("--no-color", action="store_true", help="never emit ANSI colour")
    d.set_defaults(func=cmd_doctor)

    f = sub.add_parser("fix", help="repair one file, or every damaged file in a folder")
    f.add_argument("file", nargs="+",
                   help="one media file, or one or more folders to repair in batch")
    f.add_argument("-o", "--out", help="output file (single input only)")
    f.add_argument("--out-dir", help="batch: write repaired files into this folder")
    f.add_argument("--force", action="store_true",
                   help="batch: also rewrite files that scan calls healthy")
    f.add_argument("--quiet", action="store_true",
                   help="print only failures and the summary")
    f.add_argument("--verbose", action="store_true",
                   help="batch: print each repaired file's notes")
    f.add_argument("--no-color", action="store_true", help="never emit ANSI colour")
    f.add_argument("--reference", help="intact file from the same device/encoder/session")
    f.add_argument("--timescale", type=int,
                   help="flatten only: force one timescale for all tracks, e.g. 90000")
    f.add_argument("--deep", action="store_true",
                   help="decode the input first: catches payload damage that headers hide")
    f.add_argument("--no-verify", action="store_true",
                   help="skip the decode check of the repaired file (it runs by default)")
    f.add_argument("--fps", type=float, default=25.0,
                   help="carve only: frame rate to assume when the index is lost (default 25)")
    f.add_argument("--strategy",
                   choices=["auto", "remux", "graft", "flatten", "trim", "salvage", "carve"],
                   default="auto",
                   help="salvage: trim, remux, then drop a head that was overwritten with junk; "
                        "carve: rebuild video from the raw H.264 payload without a reference")
    f.set_defaults(func=cmd_fix)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
