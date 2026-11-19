from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, fetch, ui
from .diagnose import MEDIA_SUFFIXES, diagnose
from .ffmpeg import have_ffmpeg, probe, remux
from .report import Severity

SEVERITY_COLOUR = {
    Severity.HEALTHY: "green",
    Severity.CORRUPT_PAYLOAD: "yellow",
    Severity.CORRUPT_INDEX: "yellow",
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


def _owns_output(name: str) -> bool:
    """Files this tool itself wrote on a previous run; never worth a second look."""
    lowered = name.lower()
    return ".repaired." in lowered or ".stage." in lowered


def iter_media(paths: list[str]):
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in MEDIA_SUFFIXES:
                    # Only the path *below* the requested root is filtered, so a
                    # root that itself lives under AppData still scans.
                    if not any(part.lower() in SKIP_DIRS for part in f.relative_to(p).parts) \
                            and not _owns_output(f.name):
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
    if not args.json:
        print(paint(f"{'severity':<16} {'bytes':>12}  {'fix':<3}  file", "dim"))
    for f in iter_media(args.paths):
        d = diagnose(f, hash_file=args.hash, deep=args.deep, deep_seconds=args.deep_seconds)
        rows.append(d)
        if not args.json:
            label = paint(f"{d.severity.value:<16}", SEVERITY_COLOUR.get(d.severity, ""))
            fixable = paint(f"{'yes' if d.recoverable else 'no ':<3}",
                            "green" if d.recoverable else "red")
            print(f"{label} {d.size:>12,}  {fixable}  {f}")
    if args.json:
        # printable(): a lone surrogate survives as text but makes the JSON invalid.
        print(ui.printable(json.dumps([d.to_dict() for d in rows], indent=2)))
    if args.out:
        Path(args.out).write_text(ui.printable(json.dumps([d.to_dict() for d in rows], indent=2)),
                                  encoding="utf-8")
        print(f"\nwrote {args.out} ({len(rows)} files)", file=sys.stderr)
    if not args.json:
        bad = [d for d in rows if d.severity != Severity.HEALTHY]
        repairable = [d for d in bad if d.recoverable]
        if not rows:
            print("\n" + paint("0 scanned: no video or audio files found here", "yellow"))
            return 0
        if not bad:
            print("\n" + paint(f"all {len(rows)} file(s) look fine; nothing to repair", "green"))
            return 0
        print("\n" + paint(f"{len(rows)} scanned, {len(bad)} damaged, "
                           f"{len(repairable)} repairable, "
                           f"{sum(1 for d in bad if d.needs_reference)} need a reference file",
                           "yellow" if repairable else "red"))
        # Say the next command out loud: the commonest stall is not knowing what to type next.
        first = Path(args.paths[0])
        where = first if first.is_dir() else first.parent
        if repairable:
            print(f"next: mediarescue fix \"{where}\" --out-dir \"{where / 'recovered'}\"")
        missing_ref = [d for d in bad if d.needs_reference]
        if missing_ref:
            print("those need --reference: an intact file recorded the same way, see --help")
        if not repairable and not missing_ref:
            print("nothing here can be rebuilt: the payload itself is gone")
    return 0


def cmd_doctor(args) -> int:
    from .doctor import doctor
    colour = ui.enabled(override=False if getattr(args, "no_color", False) else None)
    code, text = doctor(Path(args.workdir) if args.workdir else None, colour=colour)
    print(text)
    return code


def cmd_menu(args) -> int:
    from .menu import run_menu
    return run_menu()


def _pick_strategy(severity: Severity, requested: str) -> str:
    if requested != "auto":
        return requested
    return {
        # salvage is trim + remux + (only when needed) dropping a junk head, so it is a strict
        # superset of trim: same result on a clean tail, usable file when the head was overwritten.
        Severity.MISSING_TRAILER: "salvage",
        Severity.CORRUPT_PAYLOAD: "salvage",
        Severity.FRAGMENTED: "flatten",
        # AVI carries no sample table to rebuild; a remux that ignores idx1 and rescans movi
        # writes a fresh index, so the index damage is the whole repair.
        Severity.CORRUPT_INDEX: "remux",
    }.get(severity, "remux" if severity == Severity.HEALTHY else "graft")


def _resolve_strategy(src: Path, d, args) -> tuple[str, str | None]:
    """The strategy that will actually run, and the mp3val binary if one is needed."""
    from . import external
    strategy = _pick_strategy(d.severity, args.strategy)
    mp3_damaged = (src.suffix.lower() in (".mp3", ".mp2")
                   and d.severity in (Severity.CORRUPT_PAYLOAD, Severity.MISSING_ATOMS))
    mp3val = external.mp3val_path() if mp3_damaged else None
    # Damage inside an MP3 cannot be fixed by rewriting the container; mp3val can rebuild the
    # frame stream, so use it when it is installed and the header scan already saw damage.
    if mp3val and args.strategy == "auto":
        strategy = "mp3val"
    return strategy, mp3val


def _carve_or_explain(src: Path, out: Path, fps: float, why: str) -> dict:
    """Last resort when no reference exists: rebuild from the payload, or say precisely why not.

    `carve` only understands H.264. Handing it an AV1 file made it "find" a bogus H.264 stream in
    AV1 bytes and fail with an SPS/PPS complaint that names the wrong codec and points nowhere.
    """
    from .reference import source_video_codec
    codec = source_video_codec(src)
    if codec == "av1":
        return {"ok": False, "strategy": "carve",
                "reason": ("AV1 stream with no index. Its audio configuration lived in the lost "
                           "index, so a --reference file is needed: any intact MP4 whose audio "
                           "was encoded the same way (same device, app or download source). "
                           "It does not have to be AV1."),
                "notes": [why]}
    from .carve import carve
    result = carve(src, out, fps=fps)
    if not result.get("ok"):
        result["note"] = why
    return result


def _repair(src: Path, out: Path, args, d) -> dict:
    """Run one strategy and verify the result. Prints nothing; returns the result dict."""
    strategy, mp3val = _resolve_strategy(src, d, args)
    from . import external
    mp3_damaged = mp3val is not None or (
        src.suffix.lower() in (".mp3", ".mp2")
        and d.severity in (Severity.CORRUPT_PAYLOAD, Severity.MISSING_ATOMS))

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
        ok, log = remux(src, out, ignore_index=src.suffix.lower() == ".avi")
        result = {"ok": ok, "output": str(out) if ok else "", "log": log[-400:]}
    elif strategy == "carve":
        result = _carve_or_explain(src, out, args.fps, "carve requested explicitly")
    elif strategy in ("flatten", "graft") and not args.reference:
        # No --reference: look for a healthy sibling with the same codec before giving up on
        # the index entirely and falling back to carve.
        from .reference import find_reference
        auto = find_reference(src)
        if auto is None:
            result = _carve_or_explain(src, out, args.fps,
                                       "no --reference given and no matching sibling found")
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
        result = _carve_or_explain(src, out, args.fps, "no --reference given")
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
    # A reference given as a link (cloud drive, direct URL) is fetched once, used by every file in
    # the batch, and deleted afterwards -- the user keeps nothing they did not already have.
    fetched = None
    reference = (getattr(args, "reference", None) or "").strip()
    if reference and fetch.is_url(reference):
        try:
            fetched = _fetch_reference(reference, args)
        except fetch.FetchError as exc:
            print(f"cannot use that link: {exc}", file=sys.stderr)
            return 2
        if fetched is None:
            return 2
        args.reference = str(fetched)
    try:
        if len(targets) == 1 and targets[0].is_file():
            return _fix_single(targets[0], args)
        return _fix_batch(targets, args)
    finally:
        if fetched is not None:
            fetch.cleanup(fetched)


def _fetch_reference(url: str, args) -> Path | None:
    """Download `url` into a scratch folder; returns the local file, or None after reporting."""
    import tempfile

    from .ui import colour_for, enabled
    paint = colour_for(enabled(override=False if getattr(args, "no_color", False) else None))
    folder = Path(tempfile.mkdtemp(prefix="mediarescue-ref-"))
    quiet = getattr(args, "quiet", False)

    def report(done: int, total: int) -> None:
        if quiet or not sys.stdout.isatty():
            return
        of = f" / {fetch.describe(total)}" if total else ""
        print(f"\r  downloading reference: {fetch.describe(done)}{of}", end="", flush=True)

    if not quiet:
        print(paint(f"reference link: {url}", "dim"), file=sys.stderr)
    try:
        path = fetch.download(url, folder, name_hint="reference", report=report)
    except fetch.FetchError as exc:
        if not quiet:
            print("\r" + " " * 60 + "\r", end="")
        fetch.cleanup(folder / "reference.mp4")
        raise
    except OSError as exc:
        fetch.cleanup(folder / "reference.mp4")
        raise fetch.FetchError(f"could not write the download: {exc}") from exc
    if not quiet:
        if sys.stdout.isatty():
            print("\r" + " " * 60 + "\r", end="")
        print(paint(f"reference downloaded: {path.name} ({fetch.describe(path.stat().st_size)})",
                    "dim"), file=sys.stderr)
    return path


def _fix_single(src: Path, args) -> int:
    paint = ui.colour_for(ui.enabled(override=False if getattr(args, "no_color", False) else None))
    d = diagnose(src, hash_file=False, deep=args.deep)
    label = paint(d.severity.value, SEVERITY_COLOUR.get(d.severity, ""))
    print(f"diagnosis: {label} — {'; '.join(d.notes)}")
    if d.severity in (Severity.EMPTY, Severity.UNSUPPORTED, Severity.HEADERLESS):
        print(f"refusing: {d.severity.value} is not repairable by this tool "
              f"(no stream payload to rebuild from)", file=sys.stderr)
        return 2
    if d.severity == Severity.HEALTHY and not getattr(args, "force", False):
        # Rewriting a healthy file produced a byte-for-byte equivalent copy and took minutes on a
        # large one; say so instead, and leave --force for the deliberate case.
        print(paint("nothing to repair: this file already reads cleanly "
                    "(use --force to rewrite it anyway)", "green"))
        return 0
    if not have_ffmpeg():
        print("ffmpeg/ffprobe not found on PATH", file=sys.stderr)
        return 3

    out = Path(args.out) if args.out else src.with_name(src.stem + ".repaired" + src.suffix)
    if not getattr(args, "quiet", False):
        # A 300 MB repair takes minutes; say what is happening instead of going silent.
        chosen, _ = _resolve_strategy(src, d, args)
        print(paint(f"working: {chosen} → {out.name}", "dim"), file=sys.stderr)
    result = _repair(src, out, args, d)
    print(_brief_line(result) if args.brief else json.dumps(result, indent=2))
    return 0 if _result_ok(result) else 1


def _brief_line(result: dict) -> str:
    """One line for scripts and screenshots: what ran, what came out, whether it decodes."""
    if not result.get("ok"):
        # A stated reason is short and the cause comes first; a raw ffmpeg log is the opposite,
        # its useful line is the last one.
        reason = result.get("reason")
        if reason:
            return f"{result.get('strategy', '?')}: failed — {reason}"
        lines = (result.get("log") or "").strip().splitlines()
        return f"{result.get('strategy', '?')}: failed — {lines[-1][-200:] if lines else 'no detail'}"
    probe = result.get("probe") or {}
    verify = result.get("verify") or {}
    parts = [f"{result.get('strategy', '?')} → {Path(result['output']).name}", "ok"]
    if probe.get("duration"):
        parts.append(f"{probe['duration']:.2f}s")
    if probe.get("streams"):
        parts.append("+".join(probe["streams"]))
    if "decode_errors" in verify:
        parts.append(f"decode errors {verify['decode_errors']}")
    parts.append("source untouched")
    return "  ".join(parts)


def _fix_batch(targets: list[Path], args) -> int:
    if args.out:
        print("refusing: -o names one output file; use --out-dir for a batch", file=sys.stderr)
        return 2
    if not have_ffmpeg():
        print("ffmpeg/ffprobe not found on PATH", file=sys.stderr)
        return 3

    paint = ui.colour_for(ui.enabled(override=False if getattr(args, "no_color", False) else None))
    out_dir = Path(args.out_dir).resolve() if args.out_dir else None
    files = [f for f in iter_media([str(t) for t in targets])
             if out_dir is None or not out_dir in [f.resolve(), *f.resolve().parents]]
    repaired, failed, need_ref, skipped = [], [], [], []
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
            why = result.get("reason") or (
                f"{result.get('verify', {}).get('decode_errors', 0)} decode errors in the output")
            # Files that need an intact sibling are not "failed" — they are one file away from
            # being repairable. Grouping them apart keeps a red FAILED for real impossibilities.
            # "Needs a reference" is only honest when there is still a payload to graft onto: a
            # file whose boxes are unreadable at offset 0 has nothing a reference could repair.
            has_payload = any(a.type == "mdat" for a in getattr(d, "atoms", None) or [])
            if "--reference" in why or (d.severity.needs_reference and has_payload):
                need_ref.append((f, why))
                if not args.quiet:
                    print(f"  {counter} {paint('need-ref', 'yellow')} {f.name:<44} "
                          f"{paint('needs an intact file recorded the same way', 'yellow')}")
            else:
                failed.append((f, result))
                print(f"  {counter} {paint('FAILED', 'red')} {f.name:<44} {paint(why, 'red')}")

    parts = [f"{len(repaired)} repaired"]
    if need_ref:
        parts.append(f"{len(need_ref)} need a reference file")
    if failed:
        parts.append(f"{len(failed)} failed")
    parts.append(f"{len(skipped)} skipped")
    left = len(need_ref) + len(failed)
    summary = f"{total} files: " + ", ".join(parts)
    print("\n" + (paint(summary, "red" if failed else "yellow" if need_ref else "green")))
    if out_dir:
        print(paint(f"output folder: {out_dir}", "dim"))
    if need_ref:
        # Say what a reference actually is, once, with a copy-pasteable command per folder — the
        # per-file reasons above are long; this is the part a beginner acts on.
        print(paint(f"how to fix the other {len(need_ref)}: they need one intact file from the "
                    f"same source (same phone, app or download site — not the same video). "
                    f"Run once per folder:", "yellow"))
        for folder in dict.fromkeys(f.parent for f, _ in need_ref):
            out = out_dir or folder / "recovered"
            print(paint(f'  mediarescue fix "{folder}" --out-dir "{out}" '
                        f'--reference "C:/path/to/one-intact-file.mp4"', "yellow"))
        print(paint("if that file is online (TeraBox, Google Drive, MediaFire, pixeldrain, GoFile, "
                    "Dropbox, OneDrive), pass the share link instead — it is downloaded, used, and "
                    "deleted:", "yellow"))
        print(paint('  mediarescue fix "<folder>" --out-dir "<out>" --reference "https://..."',
                    "yellow"))
    return 1 if left else 0


def main(argv: list[str] | None = None) -> int:
    # A filename can contain characters the console cannot encode (NTFS allows lone surrogates);
    # without this a whole scan aborts mid-table with OSError EINVAL instead of finishing.
    ui.forgiving_output()
    # Without this, `not argv` is true for every real invocation and the no-argument shortcut
    # swallows the command line: `mediarescue scan x` would open the menu instead of scanning.
    argv = list(sys.argv[1:] if argv is None else argv)
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
                   help="repair files that scan calls healthy instead of skipping them")
    f.add_argument("--quiet", action="store_true",
                   help="print only failures and the summary")
    f.add_argument("--verbose", action="store_true",
                   help="batch: print each repaired file's notes")
    f.add_argument("--no-color", action="store_true", help="never emit ANSI colour")
    f.add_argument("--brief", action="store_true",
                   help="one line per file instead of the full JSON report")
    f.add_argument("--reference", help="intact file from the same device/encoder/session, or a "
                                       "share link (TeraBox, Google Drive, MediaFire, pixeldrain, "
                                       "GoFile, Dropbox, OneDrive); links are downloaded to a "
                                       "temporary folder and deleted afterwards")
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

    m = sub.add_parser("menu", help="interactive all-in-one menu (also opens when run with no arguments)")
    m.set_defaults(func=cmd_menu)

    if not argv:
        # A bare `mediarescue` in a terminal is someone who has not read the README yet: give them
        # the menu. A bare call from a script gets help and exits 2, so nothing ever blocks on input.
        # Both streams are checked: on Windows a redirected stdin can still report itself as a tty.
        if sys.stdin.isatty() and sys.stdout.isatty():
            return cmd_menu(argparse.Namespace())
        ap.print_help()
        return 2

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
