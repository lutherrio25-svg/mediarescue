"""Interactive all-in-one menu: one screen, numbered choices, no flags to memorise.

Deliberately built on top of `cli.main()` instead of re-implementing each command: the menu can
never drift from the real CLI, and every action it offers behaves exactly like typing it.

The menu takes its input and output functions as arguments, so tests can script a whole session
without a terminal.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import __version__

Reader = Callable[[str], str]
Writer = Callable[[str], None]

CHOICES = [
    ("1", "Check this machine can repair files (doctor)"),
    ("2", "Scan a file or folder — what is wrong?"),
    ("3", "Repair one file"),
    ("4", "Repair every damaged file in a folder"),
    ("5", "Show the damage classes and what each repair does"),
    ("0", "Quit"),
]

DAMAGE_HELP = """\
Damage classes and the repair each one triggers

  healthy          boxes parse, index reachable        remux (optional cleanup)
  corrupt-payload  index fine, pictures are junk       salvage (cut to the first clean keyframe)
  corrupt-index    AVI idx1 missing or misdirected     remux with +ignidx (rescan movi)
  missing-trailer  mdat runs past EOF, moov is inside  salvage (trim, then cut a junk head)
  fragmented       starts at moof, init segment lost   flatten (or carve without a reference)
  missing-atoms    ftyp+mdat present, moov gone        graft (or carve without a reference)
  headerless       nothing parsable at offset 0        not repairable
  empty            0 bytes                             nothing to rebuild

Fragmented AV1 files whose audio configuration died with the index need --reference: any intact
MP4 encoded the same way will do, it does not have to be AV1."""


def _ask_path(reader: Reader, writer: Writer, prompt: str, must_exist: bool = True) -> Path | None:
    raw = reader(prompt).strip().strip('"')
    if not raw:
        writer("  cancelled")
        return None
    path = Path(raw)
    if must_exist and not path.exists():
        writer(f"  not found: {path}")
        return None
    return path


def _yes(reader: Reader, prompt: str) -> bool:
    return reader(prompt).strip().lower() in ("y", "yes", "ya", "1")


def run_menu(reader: Reader = input, writer: Writer = print,
             dispatch: Callable[[list[str]], int] | None = None) -> int:
    """Numbered menu that loops until Quit. Returns the last non-zero exit code, or 0."""
    from .cli import main as cli_main

    run = dispatch or cli_main
    last = 0

    writer("")
    writer(f"mediarescue {__version__} — all-in-one menu")
    while True:
        writer("")
        for key, label in CHOICES:
            writer(f"  {key}  {label}")
        writer("")
        try:
            choice = reader("choice> ").strip()
        except (EOFError, KeyboardInterrupt):
            writer("")
            return last

        if choice in ("0", "q", "quit", "exit"):
            writer("bye")
            return last
        if not choice:
            continue                     # a bare Enter just redraws the menu
        if choice == "1":
            last = run(["doctor"])
        elif choice == "2":
            path = _ask_path(reader, writer, "path to scan> ")
            if path is None:
                continue
            deep = _yes(reader, "decode the whole file too? slower, catches junk payloads [y/N]> ")
            last = run(["scan", str(path)] + (["--deep"] if deep else []))
        elif choice == "3":
            path = _ask_path(reader, writer, "file to repair> ")
            if path is None:
                continue
            out = reader("write the repair to (blank = next to the original)> ").strip().strip('"')
            ref = reader("--reference file, if the index is gone (blank = none)> ").strip().strip('"')
            argv = ["fix", str(path)]
            if out:
                argv += ["-o", out]
            if ref:
                argv += ["--reference", ref]
            last = run(argv)
        elif choice == "4":
            path = _ask_path(reader, writer, "folder to repair> ")
            if path is None:
                continue
            out_dir = reader("write repairs into (blank = current folder)> ").strip().strip('"')
            argv = ["fix", str(path)]
            if out_dir:
                argv += ["--out-dir", out_dir]
            last = run(argv)
        elif choice == "5":
            writer(DAMAGE_HELP)
        else:
            writer(f"  {choice!r} is not one of the numbers above")
            continue

        try:
            reader("\npress Enter to return to the menu ")
        except (EOFError, KeyboardInterrupt):
            writer("")
            return last
