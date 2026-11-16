from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .diagnose import MEDIA_SUFFIXES, diagnose
from .ffmpeg import have_ffmpeg, probe, remux
from .report import Severity

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
    for f in iter_media(args.paths):
        d = diagnose(f, hash_file=args.hash)
        rows.append(d)
        if not args.json:
            print(f"{d.severity.value:<16} {d.size:>12,}  {'yes' if d.recoverable else 'no ':<3}  {f}")
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


def cmd_fix(args) -> int:
    src = Path(args.file)
    d = diagnose(src, hash_file=False)
    print(f"diagnosis: {d.severity.value} — {'; '.join(d.notes)}")
    if d.severity in (Severity.EMPTY, Severity.UNSUPPORTED, Severity.HEADERLESS):
        print(f"refusing: {d.severity.value} is not repairable by this tool "
              f"(no stream payload to rebuild from)", file=sys.stderr)
        return 2
    if not have_ffmpeg():
        print("ffmpeg/ffprobe not found on PATH", file=sys.stderr)
        return 3

    out = Path(args.out) if args.out else src.with_name(src.stem + ".repaired" + src.suffix)
    strategy = args.strategy
    if strategy == "auto":
        strategy = {
            # salvage is trim + remux + (only when needed) dropping a junk head, so it is a
            # strict superset of trim: same result on a clean tail, usable file when the head
            # was overwritten.
            Severity.MISSING_TRAILER: "salvage",
            Severity.FRAGMENTED: "flatten",
        }.get(d.severity, "remux" if d.severity == Severity.HEALTHY else "graft")

    if strategy == "trim":
        from .trim import trim_mdat
        trimmed = out.with_name(out.stem + ".trimmed" + out.suffix)
        t = trim_mdat(src, trimmed)
        if not t["ok"]:
            print(json.dumps(t, indent=2))
            return 1
        ok, log = remux(trimmed, out)
        if not ok and probe(trimmed).get("ok"):
            result = {"ok": True, "output": str(trimmed), "note": "remux failed; trimmed copy verified instead"}
        else:
            trimmed.unlink(missing_ok=True)
            result = {"ok": ok, "output": str(out) if ok else "", "log": log[-400:]}
        result.update({"strategy": "trim", "trim": t})
    elif strategy == "salvage":
        from .salvage import salvage
        result = salvage(src, out)
        result["strategy"] = "salvage"
    elif strategy == "remux":
        ok, log = remux(src, out)
        result = {"ok": ok, "strategy": "remux", "output": str(out) if ok else "", "log": log[-400:]}
    else:
        if not args.reference:
            print(f"{strategy} needs --reference: an intact file from the same device/encoder",
                  file=sys.stderr)
            return 2
        ref = Path(args.reference)
        if strategy == "flatten":
            from .fragmented import flatten
            result = flatten(src, ref, out, force_timescale=args.timescale)
            result["strategy"] = "flatten"
        else:
            from .graft import graft
            r = graft(src, ref, out)
            result = {"ok": r.ok, "strategy": "graft", "output": r.output,
                      "tracks": r.tracks, "samples": r.samples, "reason": r.reason}

    if result.get("ok"):
        result["probe"] = probe(Path(result["output"]))
        result["source_untouched"] = True
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") and result.get("probe", {}).get("ok", True) else 1


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
    s.set_defaults(func=cmd_scan)

    f = sub.add_parser("fix", help="repair one file into a new output file")
    f.add_argument("file")
    f.add_argument("-o", "--out")
    f.add_argument("--reference", help="intact file from the same device/encoder/session")
    f.add_argument("--timescale", type=int,
                   help="flatten only: force one timescale for all tracks, e.g. 90000")
    f.add_argument("--strategy", choices=["auto", "remux", "graft", "flatten", "trim", "salvage"],
                   default="auto",
                   help="salvage: trim, remux, then drop a head that was overwritten with junk")
    f.set_defaults(func=cmd_fix)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
