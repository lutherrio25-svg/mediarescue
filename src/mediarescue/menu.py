"""Interactive all-in-one menu: one framed screen, numbered buttons, no flags to memorise.

Deliberately built on top of `cli.main()` instead of re-implementing each command: the menu can
never drift from the real CLI, and every action it offers behaves exactly like typing it.

Two input modes, chosen at runtime:

* a real terminal gets arrow-key navigation (up/down + Enter) with the current row highlighted;
* anything else (a pipe, a test, a redirected stdin) gets the numbered prompt, so a scripted
  session still works and nothing ever blocks waiting for a keypress.

The menu takes its reader, writer and key source as arguments, so tests can drive a whole
session without a terminal.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__, ui

Reader = Callable[[str], str]
Writer = Callable[[str], None]
KeyReader = Callable[[], str]

TAGLINE = "repair damaged video and audio files; originals are never touched"

# Labels and hints are kept short on purpose: the frame is as wide as its longest row, and a menu
# that wraps in an 80-column terminal is a menu that looks broken on the machine it ships to.
CHOICES: list[tuple[str, str]] = [
    ("1", "Check this machine can repair files"),
    ("2", "Look at a file or folder"),
    ("3", "Repair one file"),
    ("4", "Repair every damaged file in a folder"),
    ("5", "Show the damage classes"),
    ("6", "Find a file's original online"),
    ("7", "Check a file really decodes"),
    ("0", "Quit"),
]

HINTS = {
    "1": "ffmpeg, ffprobe, disk space",
    "2": "what's wrong, no changes",
    "3": "leaves the original alone",
    "4": "skips the healthy ones",
    "5": "one line per class",
    "6": "searches the web first",
    "7": "every frame, not just headers",
    "0": "close the menu",
}

DAMAGE_HELP = """\
Damage classes, and the repair each one triggers

  healthy           index reachable, boxes ok     remux, optional
  corrupt-payload   index fine, pictures junk     salvage from keyframe
  corrupt-index     AVI idx1 wrong or missing     remux with +ignidx
  missing-trailer   mdat past EOF, moov inside    salvage, then trim
  fragmented        starts at moof, init lost     flatten or carve
  missing-atoms     ftyp+mdat there, moov gone    graft or carve
  mkv-headless      EBML header cut, clusters ok  transplant Info+Tracks
  headerless        nothing parses at offset 0    not repairable
  empty             0 bytes                       nothing to rebuild

Fragmented AV1 files whose audio configuration died with the index need
--reference: any intact MP4 encoded the same way will do, it does not have
to be AV1."""


# --- glyphs -----------------------------------------------------------------------------------
# Windows consoles still default to a legacy code page often enough that a box-drawing character
# raises UnicodeEncodeError and kills the menu, so the frame degrades to ASCII instead.

_UNICODE = {
    "tl": "\u250c", "tr": "\u2510", "bl": "\u2514", "br": "\u2518",
    "h": "\u2500", "v": "\u2502", "sel": "\u25b8",
}
_ASCII = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|", "sel": ">"}


def _can_print(text: str) -> bool:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def _glyphs() -> dict[str, str]:
    return _UNICODE if _can_print("".join(_UNICODE.values())) else _ASCII


def _text(unicode_form: str, ascii_form: str) -> str:
    return unicode_form if _can_print(unicode_form) else ascii_form


def _label_width() -> int:
    return max(len(label) for _, label in CHOICES)


def _row(key: str, label: str, marker: str, paint=None) -> str:
    """One menu row: marker slot, number, label, hint. Every row is built here, coloured or not,
    so the hint column cannot drift by a space between the highlighted row and the rest."""
    number, hint = f"{key:>2}", HINTS.get(key, "")
    if paint is not None:
        number, hint = paint(number, "yellow"), paint(hint, "dim")
    return f"  {marker}{number}  {label.ljust(_label_width())}  {hint}"


def _plain_choices(selected: int | None) -> list[str]:
    g = _glyphs()
    return [_row(key, label, f"{g['sel']} " if selected == index else "  ")
            for index, (key, label) in enumerate(CHOICES)]


def _content_width() -> int:
    """One width for the whole screen, so the frame closes and the highlight spans it fully."""
    header = [f"mediarescue {__version__}", TAGLINE]
    footer = _text("\u2191/\u2193 move  \u00b7  Enter choose  \u00b7  0 quits",
                   "up/down move  -  Enter choose  -  0 quits")
    lengths = [len(line) for line in header + _plain_choices(None) if line] + [len(footer)]
    return max(lengths)


def _box(lines: list[str], width: int, paint) -> list[str]:
    g = _glyphs()
    out = [paint(g["tl"] + g["h"] * (width + 2) + g["tr"], "dim")]
    for line in lines:
        pad = " " * max(0, width - len(line))
        out.append(paint(g["v"], "dim") + " " + line + pad + " " + paint(g["v"], "dim"))
    out.append(paint(g["bl"] + g["h"] * (width + 2) + g["br"], "dim"))
    return out


def _header(paint, width: int | None = None) -> list[str]:
    width = _content_width() if width is None else width
    left = f"mediarescue {__version__}"
    title = paint(left, "bold") + " " * max(0, width - len(left))
    return _box([title, TAGLINE], width, paint)


def _choice_lines(paint, selected: int | None, width: int | None = None) -> list[str]:
    """The buttons. `selected` is the highlighted row when arrow navigation is active.

    The highlighted row is built from uncoloured text and then given one reverse span: nesting it
    around coloured spans would not work, because the first inner reset ends the highlight.
    """
    width = _content_width() if width is None else width
    plain_lines = _plain_choices(selected)
    out = []
    for index, (key, label) in enumerate(CHOICES):
        plain = plain_lines[index]
        pad = " " * max(0, width - len(plain))
        if selected == index:
            # one reverse span over uncoloured text: the bar reaches the frame's edge, and an inner
            # reset from a coloured span cannot end the highlight halfway along the row
            # the bar spans the frame's outer width, borders included, so it reads as one row
            out.append(paint(plain + " " * max(0, width + 4 - len(plain)), "reverse"))
            continue
        out.append(_row(key, label, "  ", paint) + pad)
    return out


def _footer(paint, arrows: bool) -> str:
    if arrows:
        return paint("  " + _text("\u2191/\u2193 move  \u00b7  Enter choose  \u00b7  0 quits",
                                  "up/down move  -  Enter choose  -  0 quits"), "dim")
    return paint("  type a number, then Enter  -  0 quits", "dim")


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


# --- key input --------------------------------------------------------------------------------

def _windows_keys() -> KeyReader | None:
    try:
        import msvcrt
    except ImportError:
        return None

    def read() -> str:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):           # arrow and function keys arrive as a prefix pair
            return {"H": "up", "P": "down", "G": "home", "O": "end"}.get(msvcrt.getwch(), "")
        if ch == "\r":
            return "enter"
        if ch in ("\x03", "\x1b"):
            return "quit"
        return ch
    return read


def _posix_keys() -> KeyReader | None:
    try:
        import termios
        import tty
    except ImportError:
        return None

    def read() -> str:
        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)          # type: ignore[attr-defined]
        try:
            tty.setraw(fd)                     # type: ignore[attr-defined]
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                return {"[A": "up", "[B": "down", "[H": "home", "[F": "end"}.get(seq, "")
            if ch in ("\r", "\n"):
                return "enter"
            if ch == "\x03":
                return "quit"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)  # type: ignore[attr-defined]
    return read


def make_key_reader() -> KeyReader | None:
    """A single-keystroke reader when someone is at a terminal, otherwise None."""
    if not (getattr(sys.stdin, "isatty", lambda: False)()
            and getattr(sys.stdout, "isatty", lambda: False)()):
        return None
    return _windows_keys() if sys.platform == "win32" else _posix_keys()


def run_menu(reader: Reader = input, writer: Writer = print,
             dispatch: Callable[[list[str]], int] | None = None,
             keys: KeyReader | None = None) -> int:
    """Framed menu that loops until Quit. Returns the last non-zero exit code, or 0."""
    from .cli import main as cli_main

    run = dispatch or cli_main
    paint = ui.colour_for(ui.enabled())
    arrows = keys is not None
    last = 0
    selected = 0

    def draw() -> None:
        writer("")
        for line in _header(paint):
            writer(line)
        writer("")
        for line in _choice_lines(paint, selected if arrows else None):
            writer(line)
        writer("")
        writer(_footer(paint, arrows))
        writer("")

    while True:
        draw()
        if arrows:
            key = keys()
            choice = None
            if key in ("quit", "q"):
                choice = "0"
            elif key == "up":
                selected = (selected - 1) % len(CHOICES)
                continue
            elif key == "down":
                selected = (selected + 1) % len(CHOICES)
                continue
            elif key == "home":
                selected = 0
                continue
            elif key == "end":
                selected = len(CHOICES) - 1
                continue
            elif key == "enter":
                choice = CHOICES[selected][0]
            elif key.isdigit():
                match = [i for i, (k, _) in enumerate(CHOICES) if k == key]
                if not match:
                    continue
                selected = match[0]
                choice = key
            else:
                continue
        else:
            try:
                choice = reader("  choice> ").strip()
            except (EOFError, KeyboardInterrupt):
                writer("")
                return last
            if not choice:
                continue                 # a bare Enter just redraws the menu

        if choice in ("0", "q", "quit", "exit"):
            writer(paint("  bye", "green"))
            return last
        if choice == "1":
            last = run(["doctor"])
        elif choice == "2":
            path = _ask_path(reader, writer, "  path to scan> ")
            if path is None:
                continue
            deep = _yes(reader, "  decode the whole file too? slower, catches damage inside "
                                "MP3/AVI/MKV [y/N]> ")
            last = run(["scan", str(path)] + (["--deep"] if deep else []))
        elif choice == "3":
            path = _ask_path(reader, writer, "  file to repair> ")
            if path is None:
                continue
            out = reader("  write the repair to (blank = next to the original)> ").strip().strip('"')
            ref = reader("  reference file or link, if the index is gone (blank = none)> ").strip().strip('"')
            argv = ["fix", str(path)]
            if out:
                argv += ["-o", out]
            if ref:
                argv += ["--reference", ref]
            last = run(argv)
        elif choice == "4":
            path = _ask_path(reader, writer, "  folder to repair> ")
            if path is None:
                continue
            default = (path / "recovered") if path.is_dir() else (path.parent / "recovered")
            out_dir = reader(f"  write repairs into (blank = {default})> ").strip().strip('"')
            ref = reader("  one intact file or link from the same source (blank = none)> ").strip().strip('"')
            argv = ["fix", str(path), "--out-dir", out_dir or str(default)]
            if ref:
                argv += ["--reference", ref]
            last = run(argv)
        elif choice == "5":
            writer(DAMAGE_HELP)
        elif choice == "7":
            path = _ask_path(reader, writer, "  file to decode> ")
            if path is None:
                continue
            last = run(["verify", str(path)])
        elif choice == "6":
            path = _ask_path(reader, writer, "  damaged file> ")
            if path is None:
                continue
            out = reader("  write the repair to (blank = next to the original)> ").strip().strip('"')
            writer("  searching for the original upload of this file (needs internet)…")
            argv = ["source", str(path)]
            if out:
                argv += ["-o", out]
            last = run(argv)
        else:
            writer(f"  {choice!r} is not one of the numbers above")
            continue

        try:
            reader("  press Enter to return to the menu ")
        except (EOFError, KeyboardInterrupt):
            writer("")
            return last
