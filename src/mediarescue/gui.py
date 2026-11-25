"""A window for people who will not read a command line.

This is a shell, not a second implementation: it asks `cli.build_parser()` for the defaults so a
flag added to `fix` cannot silently go missing here, calls `diagnose()` and `cli._repair()` for
every decision, and keeps the rule that the command line keeps -- the original file is never
written to, and a repair that fails its own decode check is deleted rather than left behind
looking like it worked.

The layout follows the one window anybody who has ever converted a video already knows: a menu
strip, a flat icon toolbar with one green primary action, a source line, a couple of option rows, a
tab strip, a save row and a status bar.  Nothing here is a second opinion about the damage.

`inspect()`, `repair_one()` and `repair_target()` are pure Python with no tkinter in sight, so the
behaviour can be tested on a machine without a display.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import traceback
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from . import __version__

APP_NAME = "mediarescue"
AUTHOR = "Star Corps bks"
REPO = "lutherrio25-svg/mediarescue"
RELEASES_URL = f"https://github.com/{REPO}/releases"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
LICENCE = "MIT"


def _version_tuple(text: str) -> tuple[int, ...]:
    """('v0.13.0') -> (0, 13, 0), so two releases can be compared without packaging."""
    parts = []
    for chunk in str(text).replace("-", ".").split("."):
        digits = "".join(character for character in chunk if character.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts[:3])

# The same palette as the app icon (packaging/brand/README.md): ink surfaces, cream type, one amber
# accent for the thing the window exists for. Values are separated by brightness, not hue, so the
# window stays legible at 100 % scaling and in high contrast mode.
BG = "#0B1220"           # window
MENU = "#0B1220"         # top bar and status bar, so the chrome is one colour
PANEL = "#121B2B"        # cards
PANEL_HI = "#16233A"     # fields, log surface, hovered pills
PANEL_DEEP = "#0E1726"   # the log, one step below the card it sits in
TOOLBAR = "#0F1726"
HOVER = "#1D2A3F"
FIELD = "#182742"
BORDER = "#243450"
FG = "#F4EFE6"           # cream
TEXT_SOFT = "#D9DEE8"
DIM = "#9FAABC"
LABEL = "#B4BFCE"        # micro labels and field names, one step above DIM
OFF = "#657089"
DASH = "#4B6288"         # the empty drop target
ACCENT = "#F2A03D"       # amber
ACCENT_INK = "#0B1220"   # type on amber
WARN = "#F2A03D"
BAD = "#F2706D"
OK = "#2FD4C4"

# Family names are resolved once, against the fonts the machine actually has: Segoe UI Variable is
# what Windows 11 ships, plain Segoe UI is the fallback for Windows 10 and for a stripped install.
FONTS = {"text": "Segoe UI", "display": "Segoe UI", "mono": "Consolas"}


def _f(size: int, weight: str | None = None, face: str = "text"):
    """A font tuple from the resolved family, so no call site hard-codes a family name."""
    family = FONTS.get(face, "Segoe UI")
    return (family, size, weight) if weight else (family, size)

STRATEGIES = [
    ("Automatic — decide from the damage", "auto"),
    ("Remux — rebuild the container", "remux"),
    ("Salvage — trim, remux, drop a junk head", "salvage"),
    ("Salvage Matroska head — needs a reference", "salvage-mkv"),
    ("Graft — rebuild the index", "graft"),
    ("Flatten — undo fragmentation", "flatten"),
    ("Carve — rebuild raw H.264, no reference", "carve"),
]


# ---------------------------------------------------------------- behaviour (no tkinter below)
def _output_path(src: Path, output_dir: Path | None = None) -> Path:
    """Where this repair goes: beside the source, and never over an earlier repair."""
    folder = Path(output_dir) if output_dir else src.parent
    out = folder / (src.stem + ".repaired" + src.suffix)
    if not out.exists():
        return out
    for n in range(2, 1000):
        candidate = folder / f"{src.stem}.repaired-{n}{src.suffix}"
        if not candidate.exists():
            return candidate
    return folder / (src.stem + ".repaired-new" + src.suffix)


def _human_size(size: float) -> str:
    for unit in ("B", "kB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} GB"


def _clock(seconds: float | None) -> str:
    if not seconds:
        return "—"
    total = round(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def inspect(src: Path, deep: bool = False) -> dict:
    """The facts the window shows before the button is pressed. Never raises."""
    facts: dict = {
        "file": str(src), "name": src.name, "size": 0, "size_text": "—", "kind": "—",
        "duration": None, "duration_text": "—", "video": "—", "audio": "—",
        "severity": "", "severity_text": "", "tone": DIM, "notes": [], "needs_reference": False,
        "recoverable": True, "would_write": "", "error": "",
    }
    try:
        if src.is_file():
            facts["size"] = src.stat().st_size
            facts["size_text"] = _human_size(facts["size"])
        facts["kind"] = (src.suffix.lower().lstrip(".") or "folder").upper()
        facts["would_write"] = _output_path(src).name if src.is_file() else "one per file"
    except OSError as exc:
        facts["error"] = f"could not read it: {exc}"
        return facts

    try:
        from .diagnose import diagnose

        d = diagnose(src, hash_file=False, deep=deep)
    except Exception as exc:                      # a window must never die on one bad file
        facts["error"] = f"could not read it: {exc}"
        return facts

    facts["severity"] = d.severity.value
    facts["severity_text"] = d.severity.value
    facts["tone"] = (OK if d.severity.value == "healthy"
                     else BAD if not d.recoverable else WARN)
    facts["notes"] = list(d.notes)
    facts["needs_reference"] = bool(d.needs_reference)
    facts["recoverable"] = bool(d.recoverable)

    try:
        from .ffmpeg import have_ffmpeg, probe

        if src.is_file() and have_ffmpeg():
            p = probe(src)
            if p.get("ok"):
                facts["duration"] = p.get("duration") or None
                facts["duration_text"] = _clock(p.get("duration"))
                codecs = [c for c in (p.get("streams") or []) if c]
                kinds = p.get("kinds") or []
                if codecs:
                    # An audio-only file used to be labelled "Video: pcm_s16le": the first stream is
                    # not the video stream, it is just the first one. Ask ffprobe what each is.
                    if kinds:
                        video = [n for n, k in zip(codecs, kinds, strict=False) if k == "video"]
                        audio = [n for n, k in zip(codecs, kinds, strict=False) if k == "audio"]
                    else:
                        video, audio = codecs[:1], codecs[1:]
                    facts["video"] = ", ".join(video) or "none"
                    facts["audio"] = ", ".join(audio) or "none"
    except Exception:                            # a probe that fails is not a fact worth showing
        pass
    return facts


def repair_one(src: Path, reference: Path | None = None, deep: bool = False, *,
               strategy: str | None = None, fps: float | None = None,
               max_tries: int | None = None, output_dir: Path | None = None) -> dict:
    """Repair one file and return the verdict the command line would print. Never raises."""
    from .cli import _discard_written, _gate_reason, _repair, _result_ok, build_parser
    from .diagnose import diagnose
    from .ffmpeg import have_ffmpeg
    from .report import Severity

    # The same parser the command line uses, so `--fps`, `--strategy`, `--max-tries` and anything
    # added later arrive with their real defaults instead of a copy that drifts.
    args = build_parser().parse_args(["fix", str(src)])
    args.deep = bool(deep)
    if reference is not None:
        args.reference = str(reference)
    if strategy:
        args.strategy = strategy
    if fps is not None:
        args.fps = fps
    if max_tries is not None:
        args.max_tries = max_tries

    try:
        d = diagnose(src, hash_file=False, deep=args.deep)
    except Exception as exc:                      # a window must never die on one bad file
        return {"ok": False, "file": str(src), "reason": f"could not read it: {exc}"}
    if d.severity in (Severity.EMPTY, Severity.UNSUPPORTED, Severity.HEADERLESS):
        return {"ok": False, "file": str(src), "severity": d.severity.value,
                "reason": f"{d.severity.value}: there is no stream payload to rebuild from"}
    if d.severity is Severity.HEALTHY:
        return {"ok": True, "file": str(src), "severity": "healthy", "output": "",
                "reason": "nothing to repair: this file already reads cleanly"}
    if not have_ffmpeg():
        return {"ok": False, "file": str(src), "reason": "ffmpeg/ffprobe not found"}

    out = _output_path(src, output_dir)
    result = _repair(src, out, args, d)
    result["file"] = str(src)
    if not _result_ok(result):
        # The strategy's own words first: when nothing was written, `_gate_reason` can only guess
        # ("ffmpeg cannot open the file that was written"), while the strategy knows it needed a
        # reference file. The command line prints the same thing for the same reason.
        reason = (result.get("reason") or _gate_reason(result)
                  or "the repaired file did not pass its own checks")
        _discard_written(result, src)
        return {"ok": False, "file": str(src), "severity": d.severity.value, "reason": reason,
                "output": "", "discarded": str(out)}
    return {"ok": True, "file": str(src), "severity": d.severity.value,
            "strategy": result.get("strategy", ""),
            "output": result.get("output") or str(out),
            "seconds": (result.get("verify") or {}).get("duration")}


def find_files(target: Path) -> list[Path]:
    """Every media file in a folder, using the command line's own idea of what media is."""
    from .cli import iter_media

    return [Path(p) for p in iter_media([str(target)])]


def repair_target(target: Path, reference: Path | None = None, deep: bool = False,
                  log: Callable[[str], None] | None = None,
                  stopped: threading.Event | None = None, *,
                  strategy: str | None = None, fps: float | None = None,
                  max_tries: int | None = None, output_dir: Path | None = None,
                  progress: Callable[[int, int], None] | None = None) -> dict:
    """Repair one file or a whole folder, reporting progress as it goes."""
    say = log or (lambda _msg: None)
    step = progress or (lambda _done, _total: None)
    files = [target] if target.is_file() else find_files(target)
    stats = {"files": len(files), "repaired": 0, "skipped": 0, "failed": 0, "folder": None,
             "written": []}
    if not files:
        say("no video or audio files found there")
        return stats
    stats["folder"] = str(files[0].parent)
    for index, src in enumerate(files, 1):
        if stopped is not None and stopped.is_set():
            say("stopped — the files finished so far are on disk")
            break
        say(f"[{index}/{len(files)}] {src.name}")
        step(index - 1, len(files))
        result = repair_one(src, reference=reference, deep=deep, strategy=strategy, fps=fps,
                            max_tries=max_tries, output_dir=output_dir)
        written = (result.get("output") or "").strip()
        if result.get("ok") and written:
            stats["repaired"] += 1
            stats["folder"] = str(Path(written).parent)
            stats["written"].append(written)
            used = result.get("strategy") or ""
            say(f"    repaired — {Path(written).name}" + (f"  ({used})" if used else ""))
        elif result.get("ok"):
            stats["skipped"] += 1
            say(f"    skipped — {result.get('reason', '')}")
        else:
            stats["failed"] += 1
            say(f"    failed — {result.get('reason', '')}")
        step(index, len(files))
    return stats


def _devnull_streams() -> None:
    """A windowed build has no stdout/stderr; libraries that print must not crash on None."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            # Lives for the life of the process on purpose: a window has no terminal to close.
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


# ---------------------------------------------------------------- widgets that carry the look
def _draw_icon(canvas, kind: str, x: int, colour: str) -> None:
    """Four flat shapes drawn by hand: cheaper and steadier than six bitmaps on disk."""
    mid = 13
    top, bottom = 5, 21
    if kind == "open":                              # folder, opened
        canvas.create_polygon(x, top + 2, x + 10, top + 2, x + 12, top + 6, x + 20, top + 6,
                              x + 20, bottom - 2, x, bottom - 2, outline=colour, width=1)
        canvas.create_line(x + 2, bottom - 5, x + 18, bottom - 5, fill=colour)
    elif kind == "folder":                          # plain folder
        canvas.create_rectangle(x + 1, top + 4, x + 20, bottom - 2, outline=colour, width=1)
        canvas.create_line(x + 1, top + 8, x + 9, top + 8, fill=colour)
    elif kind == "play":                            # the one coloured icon
        canvas.create_oval(x, top - 2, x + 22, bottom, fill=colour, outline=colour)
        canvas.create_polygon(x + 8, top + 3, x + 8, bottom - 5, x + 17, mid - 1,
                              fill="#ffffff", outline="#ffffff")
    elif kind == "stop":
        canvas.create_rectangle(x + 5, top + 3, x + 17, bottom - 3, fill=colour, outline=colour)
    elif kind == "log":
        canvas.create_rectangle(x + 2, top + 1, x + 19, bottom - 1, outline=colour, width=1)
        for row in range(4):
            y = top + 5 + row * 4
            canvas.create_line(x + 5, y, x + 16, y, fill=colour)
    else:                                           # a dot, so an unknown name is still visible
        canvas.create_oval(x + 6, mid - 4, x + 14, mid + 4, fill=colour, outline=colour)


def _round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    """A rounded rectangle as one polygon: tkinter has no rounded primitive and a Canvas has no
    clipping, so the corners are walked as arc points and handed to create_polygon."""
    import math

    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points: list[float] = []
    for cx, cy, start_deg in ((x2 - r, y1 + r, -90), (x2 - r, y2 - r, 0),
                              (x1 + r, y2 - r, 90), (x1 + r, y1 + r, 180)):
        for step in range(9):
            angle = math.radians(start_deg + step * 11.25)
            points.extend((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    return canvas.create_polygon(points, **kwargs)


def _round_rect_dashed(canvas, x1, y1, x2, y2, radius, *, colour: str, dash=(6, 5), width=1):
    """A dashed rounded outline. create_polygon ignores -dash on its outline, so the edges are
    lines and the corners are arcs, each carrying the dash pattern themselves."""
    r = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    common = {"fill": colour, "dash": dash, "width": width}
    canvas.create_line(x1 + r, y1, x2 - r, y1, **common)
    canvas.create_line(x1 + r, y2, x2 - r, y2, **common)
    canvas.create_line(x1, y1 + r, x1, y2 - r, **common)
    canvas.create_line(x2, y1 + r, x2, y2 - r, **common)
    for cx, cy, start in ((x1 + r, y1 + r, 90), (x2 - r, y1 + r, 0),
                          (x2 - r, y2 - r, 270), (x1 + r, y2 - r, 180)):
        canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=start, extent=90, style="arc",
                          outline=colour, dash=dash, width=width)


def _draw_mark(canvas, size: int, *, tile: str = PANEL_HI, outline: str = BORDER,
               glyph: str = FG) -> None:
    """The app icon, drawn from the same geometry as packaging/brand/source/make.mjs."""
    scale = size / 256.0
    _round_rect(canvas, 0, 0, size - 1, size - 1, max(3, size * 0.22), fill=tile, outline=outline)
    shield = [(128, 34), (206, 62), (206, 128), (200, 158), (178, 190), (128, 220),
              (78, 190), (56, 158), (50, 128), (50, 62)]
    canvas.create_polygon([coord * scale for point in shield for coord in point],
                          fill=glyph, outline=glyph)
    canvas.create_polygon([100 * scale, 88 * scale, 100 * scale, 168 * scale,
                           172 * scale, 128 * scale], fill=tile, outline=tile)


class Card:
    """A rounded surface with a hairline edge: the whole layout is a stack of these.

    tkinter cannot round a Frame, so a Canvas is placed behind the content and redrawn on resize;
    the body keeps normal pack/grid behaviour, which means a card is as tall as what is in it.
    """

    def __init__(self, parent, tk, *, fill: str = PANEL, border: str = BORDER,
                 radius: int = 14, pad=(18, 16), bg: str = BG, dashed: bool = False) -> None:
        self.tk = tk
        self._fill, self._border, self._radius = fill, border, radius
        self.dashed = dashed
        self.frame = tk.Frame(parent, bg=bg, highlightthickness=0, bd=0)
        self._canvas = tk.Canvas(self.frame, bg=bg, highlightthickness=0, bd=0)
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self.frame, bg=fill)
        self.body.pack(fill="both", expand=True, padx=pad[0], pady=pad[1])
        # The canvas is placed, not packed, so its own size arrives in a Configure event: binding
        # only the outer frame painted once, at 1x1, and every card came out with no surface at all.
        self._canvas.bind("<Configure>", self._paint)
        self.frame.bind("<Configure>", self._paint)
        self.frame.after_idle(self._paint)

    def _paint(self, _event=None) -> None:
        width, height = self._canvas.winfo_width(), self._canvas.winfo_height()
        if width < 6 or height < 6:
            return
        self._canvas.delete("all")
        if self.dashed:
            _round_rect(self._canvas, 1, 1, width - 1, height - 1, self._radius,
                        fill=self._fill, outline=self._fill)
            _round_rect_dashed(self._canvas, 2, 2, width - 2, height - 2, self._radius,
                               colour=DASH, dash=(9, 5))
        else:
            _round_rect(self._canvas, 1, 1, width - 1, height - 1, self._radius,
                        fill=self._fill, outline=self._border)

    def pack(self, **kwargs):
        self.frame.pack(**kwargs)
        return self

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)
        return self


class PillButton:
    """A rounded action. Four kinds cover the whole window: primary, ghost, quiet, tab."""

    _KINDS: ClassVar[dict[str, tuple]] = {
        # kind: (fill, outline, text, hover fill, hover text)
        "primary": (ACCENT, ACCENT, ACCENT_INK, "#F7B45F", ACCENT_INK),
        "ghost": (None, BORDER, TEXT_SOFT, HOVER, FG),
        "quiet": (None, None, DIM, HOVER, FG),
    }

    def __init__(self, parent, tk, label: str, command, *, kind: str = "ghost",
                 width: int = 104, height: int = 34) -> None:
        import tkinter.font as tkfont

        self.command, self.kind, self.label = command, kind, label
        self._state, self._active, self._hover = "normal", False, False
        self._width, self._height = width, height
        parent_bg = parent.cget("bg") if "bg" in parent.keys() else BG  # noqa: SIM118 (Tk widget, not a dict)
        self._bg = parent_bg
        self.canvas = tk.Canvas(parent, width=width, height=height, bg=parent_bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        weight = "bold" if kind == "primary" else "normal"
        self._font = tkfont.Font(family=FONTS["text"], size=9, weight=weight)
        self.canvas.bind("<Enter>", self._enter)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<Button-1>", self._click)
        self._paint()

    def _colours(self):
        fill, outline, text, hover_fill, hover_text = self._KINDS[self.kind]
        if self._state != "normal":            # a dark primary must not stay amber and lie
            return (PANEL_HI, BORDER, OFF) if self.kind == "primary" else (fill, outline, OFF)
        if self._hover:
            return hover_fill, outline, hover_text
        return fill, outline, text

    def _paint(self) -> None:
        self.canvas.delete("all")
        fill, outline, text = self._colours()
        if fill or outline:
            _round_rect(self.canvas, 0.5, 0.5, self._width - 0.5, self._height - 0.5,
                        self._height / 2, fill=fill or self._bg, outline=outline or fill or self._bg)
        self.canvas.create_text(self._width / 2, self._height / 2, text=self.label,
                                fill=text, font=self._font)

    # ttk.Button's two verbs, so the rest of the window (and the tests) do not care what this is
    def configure(self, **kwargs):
        if "state" in kwargs:
            self._state = str(kwargs["state"])
            self.canvas.configure(cursor="hand2" if self._state == "normal" else "")
            self._paint()
        if "text" in kwargs:
            self.label = str(kwargs["text"])
            self._paint()
        return self

    def cget(self, key: str):
        if key == "state":
            return self._state
        if key == "text":
            return self.label
        raise KeyError(key)

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)
        return self

    def _enter(self, _event=None):
        if self._state == "normal":
            self._hover = True
            self._paint()

    def _leave(self, _event=None):
        self._hover = False
        self._paint()

    def _click(self, _event=None):
        if self._state == "normal" and self.command:
            self.command()


def _window_icon() -> Path | None:
    """The .ico that lives beside the exe when frozen, or in packaging/ when run from source.

    Tk ships its own default icon -- the blue feather -- and it wins over the icon embedded in the
    exe the moment a Tk window appears, so the taskbar and the title bar have to be told
    explicitly which icon belongs to this program.
    """
    candidates = []
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidates.append(Path(frozen) / "mediarescue.ico")
    candidates.append(Path(sys.executable).resolve().parent / "mediarescue.ico")
    candidates.append(Path(__file__).resolve().parents[2] / "packaging" / "mediarescue.ico")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def apply_window_icon(window) -> None:
    import contextlib
    import tkinter

    icon = _window_icon()
    if icon is None:
        return
    # a Tk built without .ico support must still open the window
    with contextlib.suppress(tkinter.TclError):
        window.iconbitmap(default=str(icon))


class App:
    """The window itself. tkinter is a parameter so importing this module needs no display."""

    def __init__(self, tk, ttk, filedialog, prefill: str | None = None) -> None:
        self.tk, self.ttk, self.filedialog = tk, ttk, filedialog
        self.root = tk.Tk()
        self._pick_fonts()
        self.root.title(f"{APP_NAME} {__version__}")
        apply_window_icon(self.root)
        self.root.configure(bg=BG)
        self.root.minsize(1000, 690)
        self.root.geometry("1080x720")
        self.target: Path | None = None
        self.reference: Path | None = None
        self.busy = False
        self.stop = threading.Event()
        self._facts_token = 0
        self._style()
        self._build()
        # DWM refuses to colour a caption that does not exist yet, so ask again once it is mapped.
        self.root.after(120, self._dark_title_bar)
        self.root.bind("<Map>", lambda _event: self._dark_title_bar(), add="+")
        self.root.bind("<Return>", lambda _event: self._start())
        if prefill:                     # a file dropped onto the exe arrives here as argv[1]
            self.target = Path(prefill)
            self.target_var.set(str(self.target))
            self.status.set("Ready")
            self._refresh_facts()

    # ---------------------------------------------------------------- styling
    def _dark_title_bar(self, window=None) -> None:
        """Windows paints a white caption above a dark window unless it is told otherwise.

        The title bar belongs to the desktop compositor, not to tkinter, so the only way to keep
        the strip above the toolbar from glaring is to ask DWM for a dark frame -- and to colour
        the caption itself, because the older dark-mode flag alone still leaves it light grey.
        """
        if os.name != "nt":
            return
        try:
            import ctypes

            dwm = ctypes.windll.dwmapi
            # A window handle is pointer sized: without argtypes ctypes truncates it to 32 bits
            # and every call quietly fails with E_HANDLE, leaving a white caption in place.
            dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                  ctypes.c_void_p, ctypes.c_uint]
            dwm.DwmSetWindowAttribute.restype = ctypes.c_long
            target = window or self.root
            hwnd = ctypes.c_void_p(int(target.wm_frame(), 16))

            on = ctypes.c_int(1)
            for attribute in (20, 19):                   # 20 on current Windows, 19 before that
                if dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(on),
                                             ctypes.sizeof(on)) == 0:
                    break
            colour = ctypes.c_int(_colourref(BG))        # caption colour, then caption text
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colour), ctypes.sizeof(colour))
            text = ctypes.c_int(_colourref(FG))
            dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        except Exception:                                 # an old Windows is not a reason to fail
            pass

    def _pick_fonts(self) -> None:
        """Ask the machine which families exist instead of assuming: Windows 11 ships Segoe UI
        Variable, Windows 10 does not, and a locked-down install may have neither."""
        import tkinter.font as tkfont

        available = set(tkfont.families(self.root))
        for face, candidates in (
                ("text", ("Segoe UI Variable Text", "Segoe UI")),
                ("display", ("Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI")),
                ("mono", ("Cascadia Mono", "Consolas", "Courier New"))):
            FONTS[face] = next((name for name in candidates if name in available), candidates[-1])

    def _style(self) -> None:
        """Flat fields, amber focus, no bevels: clam defaults are borrowed from Windows 95."""
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        troughcolor=PANEL_HI, focuscolor=ACCENT, font=_f(9))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=FG)
        for widget in ("TEntry", "TSpinbox"):
            style.configure(widget, fieldbackground=FIELD, foreground=FG, insertcolor=FG,
                            bordercolor="#2E405E", lightcolor="#2E405E", darkcolor="#2E405E",
                            padding=7, relief="flat", arrowcolor=LABEL)
            style.map(widget,
                      bordercolor=[("focus", ACCENT)],
                      lightcolor=[("focus", ACCENT)],
                      darkcolor=[("focus", ACCENT)])
        style.configure("TCombobox", fieldbackground=FIELD, background=PANEL_HI, foreground=FG,
                        arrowcolor=LABEL, bordercolor="#2E405E", padding=7, relief="flat")
        style.map("TCombobox",
                  fieldbackground=[("readonly", FIELD), ("focus", FIELD)],
                  selectbackground=[("readonly", FIELD)],
                  selectforeground=[("readonly", FG)],
                  foreground=[("readonly", FG)],
                  bordercolor=[("focus", ACCENT)],
                  lightcolor=[("focus", ACCENT)],
                  darkcolor=[("focus", ACCENT)],
                  arrowcolor=[("active", FG)])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT_SOFT, focuscolor=PANEL)
        style.map("TCheckbutton",
                  background=[("active", PANEL)],
                  foreground=[("active", FG)],
                  indicatorcolor=[("selected", ACCENT), ("!selected", FIELD)])
        # Flat tab row: an amber label on the selected tab, no bevels, no white band. The tab row
        # itself cannot be hidden (overriding the Notebook layout does not remove it on this Tk), so
        # it is styled to match the rest of the window instead of being fought.
        style.configure("TNotebook", background=PANEL, borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", background=PANEL, foreground=DIM, borderwidth=0,
                        padding=(18, 9), font=_f(9))
        style.map("TNotebook.Tab",
                  background=[("selected", "#1E3050"), ("active", HOVER)],
                  foreground=[("selected", ACCENT), ("active", LABEL)],
                  lightcolor=[("selected", "#1E3050")], darkcolor=[("selected", "#1E3050")],
                  bordercolor=[("selected", "#1E3050")],
                  expand=[("selected", (0, 0, 0, 2))])
        style.configure("TScrollbar", background=BORDER, troughcolor=PANEL, bordercolor=PANEL,
                        arrowcolor=DIM, lightcolor=BORDER, darkcolor=BORDER, width=10)
        style.map("TScrollbar", background=[("active", OFF)])
        style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL_HI,
                        bordercolor=PANEL_HI, lightcolor=ACCENT, darkcolor=ACCENT, thickness=5)
        self.root.option_add("*TCombobox*Listbox.background", PANEL_HI)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", HOVER)
        self.root.option_add("*TCombobox*Listbox.selectForeground", FG)
        self.root.option_add("*TCombobox*Listbox.borderWidth", 0)

    def _menu(self) -> None:
        """Brand on the left, the action that matters on the right, menus as plain words.

        A tkinter menubar is a win32 menu owned by the desktop theme: it ignores bg/fg and always
        arrives light, which leaves a white band under a dark title. Drawing the words ourselves
        keeps the top of the window one colour; the dropdowns behind them are still native.
        """
        tk = self.tk
        bar = tk.Frame(self.root, bg=MENU, height=62)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.menubar = bar

        # Actions are packed first so they keep the right edge when the window is dragged narrow.
        self.go = PillButton(bar, tk, "Repair", self._start, kind="primary", width=118, height=36)
        self.go.pack(side="right", padx=(10, 18), pady=13)
        self.stop_button = PillButton(bar, tk, "Stop", self._stop, kind="ghost", width=80,
                                      height=36)
        self.stop_button.configure(state="disabled")
        self.stop_button.pack(side="right", pady=13)
        self.open_dir = PillButton(bar, tk, "Results", self._open_folder, kind="ghost", width=88,
                                   height=36)
        self.open_dir.configure(state="disabled")
        self.open_dir.pack(side="right", pady=13)

        mark = tk.Canvas(bar, width=34, height=34, bg=MENU, highlightthickness=0, bd=0)
        mark.pack(side="left", padx=(18, 11), pady=14)
        _draw_mark(mark, 34)
        tk.Label(bar, text=APP_NAME, bg=MENU, fg=FG, font=_f(14, "bold", "display")).pack(
            side="left", padx=(0, 18))

        opts = {"tearoff": 0, "bg": PANEL_HI, "fg": FG, "activebackground": HOVER,
                "activeforeground": FG, "bd": 0, "font": _f(9)}
        file_menu = tk.Menu(self.root, **opts)
        file_menu.add_command(label="Open source…", command=self._choose_file)
        file_menu.add_command(label="Open a folder…", command=self._choose_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Open the results folder", command=self._open_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.destroy)
        self._menubar_entry(bar, "File", file_menu)

        tools = tk.Menu(self.root, **opts)
        tools.add_command(label="Diagnosis details",
                          command=lambda: self.tabs.select(self.tab_diag))
        tools.add_command(label="Repair options", command=lambda: self.tabs.select(self.tab_opt))
        tools.add_separator()
        tools.add_command(label="Copy the command line for this file", command=self._copy_command)
        self._menubar_entry(bar, "Tools", tools)

        help_menu = tk.Menu(self.root, **opts)
        help_menu.add_command(label="Check for updates…", command=lambda: self._about(check=True))
        help_menu.add_command(label="Release notes", command=lambda: self._open_url(RELEASES_URL))
        help_menu.add_separator()
        help_menu.add_command(label="About mediarescue", command=self._about)
        self._menubar_entry(bar, "Help", help_menu)

    def _menubar_entry(self, strip, label: str, menu) -> None:
        """One word of the menu row: hover to light it, click to drop its menu underneath."""
        item = self.tk.Label(strip, text=label, bg=MENU, fg=DIM, padx=10, font=_f(9))
        item.pack(side="left", fill="y")
        item.bind("<Enter>", lambda _event: item.configure(fg=FG))
        item.bind("<Leave>", lambda _event: item.configure(fg=DIM))
        item.bind("<Button-1>", lambda _event: menu.post(item.winfo_rootx(),
                                                         item.winfo_rooty() + item.winfo_height()))

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        tk = self.tk
        self._menu()
        self._status_bar()

        shell = tk.Frame(self.root, bg=BG)
        shell.pack(fill="both", expand=True, padx=18, pady=(14, 12))
        self._source_card(shell)
        self._paths_card(shell)
        self._tabs_card(shell)

    def _micro(self, parent, text: str):
        """An 8 pt uppercase label: the only place the window shouts, and it never shouts twice."""
        return self.tk.Label(parent, text=text, bg=PANEL, fg=LABEL, font=_f(8, "bold"), anchor="w")

    def _source_card(self, parent) -> None:
        tk = self.tk
        self.source_card = Card(parent, tk, dashed=True)
        card = self.source_card.pack(fill="x")
        row = card.body
        left = tk.Frame(row, bg=PANEL)
        left.pack(side="left", fill="x", expand=True)
        self._micro(left, "SOURCE").pack(anchor="w")
        self.source_name = tk.Label(left, text="No file chosen", bg=PANEL, fg=FG,
                                    font=_f(13, "bold", "display"), anchor="w", justify="left")
        self.source_name.pack(anchor="w", pady=(1, 0))
        meta = tk.Frame(left, bg=PANEL)
        meta.pack(anchor="w", pady=(4, 0))
        self.source_info = tk.Label(meta, text="Drag a damaged video or audio file onto this window.",
                                    bg=PANEL, fg=DIM, font=_f(9), anchor="w", justify="left")
        self.source_info.pack(side="left")
        self.duration_label = tk.Label(meta, text="", bg=PANEL, fg=OFF, font=_f(9))
        self.duration_label.pack(side="left", padx=(10, 0))
        actions = tk.Frame(row, bg=PANEL)
        actions.pack(side="right")
        # one entry point for choosing a file: the field it used to duplicate is gone
        self.open_source = PillButton(actions, tk, "Choose file", self._choose_file, width=118)
        self.open_source.pack(side="left", padx=(0, 8))
        self.open_folder_btn = PillButton(actions, tk, "Open folder", self._choose_folder,
                                          width=118)
        self.open_folder_btn.pack(side="left")

    def _paths_card(self, parent) -> None:
        tk, ttk = self.tk, self.ttk
        card = Card(parent, tk, pad=(18, 14)).pack(fill="x", pady=(12, 0))
        grid = card.body
        grid.columnconfigure(1, weight=1)
        self._micro(grid, "FILES & OPTIONS").grid(row=0, column=0, columnspan=2, sticky="w",
                                                 pady=(0, 4))
        tk.Frame(grid, bg=BORDER, height=1).grid(row=1, column=0, columnspan=4, sticky="ew",
                                                 pady=(0, 10))

        self.target_var = tk.StringVar(value="")
        self.ref_var = tk.StringVar(value="")
        self._ref_job: str | None = None
        self.ref_var.trace_add("write", self._reference_typed)
        self.output_var = tk.StringVar(value="")
        self.strategy_var = tk.StringVar(value=STRATEGIES[0][0])
        self.target_var.trace_add("write", self._source_typed)

        reference_entry = ttk.Entry(grid, textvariable=self.ref_var, font=_f(9))
        self._field_row(grid, 2, "REFERENCE", reference_entry, "Browse…",
                        self._choose_reference, hint="only when the index is gone")
        output_entry = ttk.Entry(grid, textvariable=self.output_var, font=_f(9))
        self._field_row(grid, 3, "SAVE IN", output_entry, "Browse…", self._choose_output,
                        hint="empty = beside the original")
        self.strategy_box = ttk.Combobox(grid, textvariable=self.strategy_var, state="readonly",
                                         values=[label for label, _value in STRATEGIES],
                                         font=_f(9))
        self._field_row(grid, 4, "STRATEGY", self.strategy_box, "Reset", self._reset_options)
        self._source_typed()          # empty field on boot: Repair starts dark

    def _source_typed(self, *_args) -> None:
        """Repair is the primary action, so it stays dark until there is a file to act on."""
        has_file = bool(self.target_var.get().strip())
        self.go.configure(state="normal" if has_file else "disabled")
        if not hasattr(self, "source_card"):
            return
        self.source_card.dashed = not has_file
        self.source_card._paint()
        self.open_source.configure(text="Change…" if has_file else "Choose file")
        if not has_file:
            self.source_name.configure(text="No file chosen")
            self.source_info.configure(text="Drag a damaged video or audio file onto this window.")

    def _field_row(self, grid, row: int, label: str, field, button_label: str, command,
                   hint: str | None = None) -> None:
        """One option: micro label, the field, one action, and a hint for the empty case."""
        tk = self.tk
        pad = (0, 8) if row < 4 else (0, 0)
        self._micro(grid, label).grid(row=row, column=0, sticky="w", padx=(0, 14), pady=pad)
        field.grid(row=row, column=1, sticky="ew", pady=pad)
        PillButton(grid, tk, button_label, command, kind="ghost", width=92, height=30).grid(
            row=row, column=2, sticky="e", padx=(10, 0), pady=pad)
        if hint:
            tk.Label(grid, text=hint, bg=PANEL, fg=LABEL, font=_f(8), anchor="w").grid(
                row=row, column=3, sticky="w", padx=(12, 0), pady=pad)

    def _tabs_card(self, parent) -> None:
        tk, ttk = self.tk, self.ttk
        holder = tk.Frame(parent, bg=BG)
        holder.pack(fill="both", expand=True, pady=(14, 0))
        card = Card(holder, tk, pad=(10, 10))
        self.tabs = ttk.Notebook(card.body)
        self.tabs.pack(fill="both", expand=True)
        self.tab_sum = ttk.Frame(self.tabs, style="Panel.TFrame", padding=(18, 16))
        self.tab_diag = ttk.Frame(self.tabs, style="Panel.TFrame", padding=(18, 16))
        self.tab_opt = ttk.Frame(self.tabs, style="Panel.TFrame", padding=(18, 16))
        self.tab_log = ttk.Frame(self.tabs, style="Panel.TFrame", padding=(8, 8))
        labels = ["Summary", "Diagnosis", "Options", "Activity Log"]
        frames = (self.tab_sum, self.tab_diag, self.tab_opt, self.tab_log)
        for frame, label in zip(frames, labels, strict=True):
            self.tabs.add(frame, text=label)
        self._build_summary()
        self._build_diagnosis()
        self._build_options()
        self._build_log()
        card.pack(fill="both", expand=True)

    def _status_bar(self) -> None:
        tk, ttk = self.tk, self.ttk
        tk.Frame(self.root, bg=BORDER, height=1).pack(fill="x", side="bottom")
        bar = tk.Frame(self.root, bg=MENU, height=38)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status = tk.StringVar(value="Ready")
        self.status_dot = tk.Canvas(bar, width=20, height=20, bg=MENU, highlightthickness=0, bd=0)
        self.status_dot.pack(side="left", padx=(18, 0))
        self._dot = self.status_dot.create_oval(6, 6, 14, 14, fill=OK, outline="")
        self.status_label = tk.Label(bar, textvariable=self.status, bg=MENU, fg=DIM, font=_f(9),
                                     anchor="w")
        self.status_label.pack(side="left", padx=(7, 0))
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=100, length=220)
        self._progress_pad = {"side": "right", "padx": (12, 18), "pady": 16}
        self.version_label = tk.Label(bar, text=f"{AUTHOR} · v{__version__}", bg=MENU, fg=OFF,
                                      font=_f(8), cursor="hand2")
        self.version_label.pack(side="right", padx=(12, 18))
        self.version_label.bind("<Button-1>", lambda _event: self._about())

    def _set_dot(self, colour: str) -> None:
        self.status_dot.itemconfigure(self._dot, fill=colour)

    def _build_summary(self) -> None:
        tk = self.tk
        frame = self.tab_sum
        # tkinter arrives as a parameter here, so the element types stay unannotated.
        self.fact_vars: dict = {}
        self.fact_labels: dict = {}
        rows = [("file", "File"), ("size", "Size"), ("kind", "Container"),
                ("duration", "Duration"), ("video", "Video"), ("audio", "Audio"),
                ("verdict", "Verdict"), ("write", "Output")]
        half = (len(rows) + 1) // 2
        header = tk.Label(frame, text="WHAT IS IN THE FILE", bg=PANEL, fg=DIM, font=_f(8, "bold"),
                          anchor="w")
        header.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        for index, (key, label) in enumerate(rows):
            block, line = divmod(index, half)
            base = block * 2
            tk.Label(frame, text=label, bg=PANEL, fg=OFF, font=_f(9), width=10,
                     anchor="w").grid(row=line + 1, column=base, sticky="w", pady=2)
            var = tk.StringVar(value="—")
            value = tk.Label(frame, textvariable=var, bg=PANEL,
                             fg=DIM if key == "verdict" else FG,
                             font=_f(9), anchor="w", justify="left")
            value.grid(row=line + 1, column=base + 1, sticky="w", pady=2,
                       padx=(0, 34 if block == 0 else 0))
            self.fact_vars[key] = var
            self.fact_labels[key] = value
        footer = tk.Label(
            frame,
            text="The original is never written to; a repair that fails its own decode check is "
                 "deleted instead of being left behind.",
            bg=PANEL, fg=DIM, font=_f(8), justify="left", wraplength=560)
        footer.grid(row=half + 1, column=0, columnspan=4, sticky="w", pady=(10, 0))

    def _build_diagnosis(self) -> None:
        tk = self.tk
        frame = self.tab_diag
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.diag_head = tk.Label(frame, text="No file chosen yet.", bg=PANEL, fg=FG,
                                  font=_f(11, "bold", "display"), anchor="w")
        self.diag_head.grid(row=0, column=0, sticky="w")
        self.notes = tk.Text(frame, height=8, width=1, wrap="word", state="disabled",
                             bg=PANEL, fg=TEXT_SOFT, relief="flat", borderwidth=0,
                             font=_f(9), padx=0, pady=6)
        self.notes.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

    def _build_options(self) -> None:
        tk, ttk = self.tk, self.ttk
        frame = self.tab_opt
        frame.columnconfigure(1, weight=1)
        self.deep_var = tk.BooleanVar(value=False)
        self._check(frame, "Deep check — decode the whole file first (slower, finds what the "
                           "header hides)", self.deep_var).grid(row=0, column=0, columnspan=2,
                                                               sticky="w")
        self.fps_var = tk.StringVar(value="25.0")
        self.tries_var = tk.StringVar(value="8")
        self._option_row(frame, 1, "Frame rate for rebuilt timestamps",
                         ttk.Spinbox(frame, from_=1.0, to=240.0, increment=0.001, width=8,
                                     textvariable=self.fps_var, font=_f(9)))
        self._option_row(frame, 2, "Attempts per strategy before the next one",
                         ttk.Spinbox(frame, from_=1, to=99, width=8,
                                     textvariable=self.tries_var, font=_f(9)))
        self.open_when_done = tk.BooleanVar(value=True)
        self._check(frame, "Open the results folder when it finishes", self.open_when_done).grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        tk.Label(frame, text="The same switches the command line takes: --deep, --fps, "
                             "--max-tries, --strategy.", bg=PANEL, fg=OFF,
                 font=_f(8)).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

    def _check(self, parent, text: str, variable):
        """tk.Checkbutton, not ttk: clam draws its indicator as a white box that ignores every
        colour option, which is the one bright square on a dark page."""
        return self.tk.Checkbutton(parent, text=text, variable=variable, bg=PANEL, fg=TEXT_SOFT,
                                   activebackground=PANEL, activeforeground=FG,
                                   selectcolor=FIELD, highlightthickness=0, bd=0,
                                   font=_f(9), anchor="w", cursor="hand2")

    def _option_row(self, parent, row: int, label: str, widget) -> None:
        tk = self.tk
        tk.Label(parent, text=label, bg=PANEL, fg=DIM, font=_f(9), anchor="w").grid(
            row=row, column=0, sticky="w", pady=(8, 0), padx=(0, 16))
        widget.grid(row=row, column=1, sticky="w", pady=(8, 0))

    def _build_log(self) -> None:
        tk = self.tk
        frame = self.tab_log
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log = tk.Text(frame, height=10, width=1, wrap="word", state="disabled",
                           font=_f(9, face="mono"), bg=PANEL_DEEP, fg=TEXT_SOFT, relief="flat",
                           borderwidth=0, padx=8, pady=6, insertbackground=FG)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = self.ttk.Scrollbar(frame, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        self._write_log("Progress and the verdict for each file will appear here.")

    # ---------------------------------------------------------------- picking
    _MEDIA_TYPES = ("*.mp4 *.mkv *.mov *.avi *.m4v *.webm *.flv *.mp3 *.m4a "
                    "*.wav *.flac *.aac *.ogg *.ts *.mts *.m2ts *.3gp")

    def _choose_file(self) -> None:
        chosen = self.filedialog.askopenfilename(
            title="Open source",
            filetypes=[("Video and audio", self._MEDIA_TYPES), ("All files", "*.*")])
        if chosen:
            self.target = Path(chosen)
            self.target_var.set(str(self.target))
            self._refresh_facts()

    def _choose_folder(self) -> None:
        chosen = self.filedialog.askdirectory(title="Open a folder of damaged files")
        if chosen:
            self.target = Path(chosen)
            self.target_var.set(str(self.target))
            self._refresh_facts()

    def _choose_reference(self) -> None:
        chosen = self.filedialog.askopenfilename(title="Open a file that still plays",
                                                 filetypes=[("All files", "*.*")])
        if chosen:
            self.reference = Path(chosen)
            self.ref_var.set(str(self.reference))

    def _choose_output(self) -> None:
        chosen = self.filedialog.askdirectory(title="Where the repairs should be written")
        if chosen:
            self.output_var.set(chosen)
            self._refresh_facts()

    def _reset_options(self) -> None:
        self.strategy_var.set(STRATEGIES[0][0])
        self.deep_var.set(False)
        self.fps_var.set("25.0")
        self.tries_var.set("8")
        self.output_var.set("")
        self.status.set("Options back to what the command line would do by itself.")

    def _open_url(self, url: str) -> None:
        webbrowser.open(url)

    def _about(self, check: bool = False) -> None:
        """An About box that answers the two questions people actually open it for: which build is
        this, and is there a newer one. The update check is a network round trip, so it runs off the
        UI thread and reports back through a StringVar."""
        tk = self.tk
        existing = getattr(self, "_about_window", None)
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            if check:
                self._check_updates(self._about_status)
            return

        win = tk.Toplevel(self.root, bg=BG)
        win.title(f"About {APP_NAME}")
        apply_window_icon(win)
        win.transient(self.root)
        win.resizable(False, False)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        self._about_window = win
        win.after(30, lambda: self._dark_title_bar(win))

        shell = tk.Frame(win, bg=BG, padx=26, pady=22)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=BG)
        header.pack(fill="x")
        mark = tk.Canvas(header, width=46, height=46, bg=BG, highlightthickness=0, bd=0)
        mark.pack(side="left")
        _draw_mark(mark, 46)
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left", padx=(14, 0))
        tk.Label(titles, text=APP_NAME, bg=BG, fg=FG,
                 font=_f(17, "bold", "display")).pack(anchor="w")
        tk.Label(titles, text=f"version {__version__} · {LICENCE} licence · {AUTHOR}",
                 bg=BG, fg=DIM, font=_f(9)).pack(anchor="w")

        tk.Label(shell, text="Repairs damaged video and audio files without touching the original. "
                             "This window runs exactly the same engine as the command line.",
                 bg=BG, fg=TEXT_SOFT, font=_f(9), justify="left",
                 wraplength=470).pack(anchor="w", pady=(14, 12))

        card = Card(shell, tk, bg=BG, pad=(16, 12))
        card.pack(fill="x")
        body = card.body
        body.columnconfigure(1, weight=1)
        platform_note = f"{platform.system()} {platform.release()} · Python {platform.python_version()}"
        rows = [("Build", platform_note), ("Repository", REPO)]
        for index, (label, value) in enumerate(rows):
            tk.Label(body, text=label, bg=PANEL, fg=LABEL, font=_f(8, "bold"), width=11,
                     anchor="w").grid(row=index, column=0, sticky="w", pady=3)
            if label == "Repository":
                link = tk.Label(body, text=value, bg=PANEL, fg=ACCENT, font=_f(9, face="mono"),
                                cursor="hand2", anchor="w")
                link.bind("<Button-1>", lambda _event: self._open_url(RELEASES_URL))
            else:
                link = tk.Label(body, text=value, bg=PANEL, fg=DIM, font=_f(9), anchor="w")
            link.grid(row=index, column=1, sticky="w", pady=3)

        frame = tk.Frame(shell, bg=BG)
        frame.pack(fill="x", pady=(14, 0))
        self._about_status = tk.StringVar(value="Not checked yet.")
        self._about_dot = tk.Canvas(frame, width=16, height=16, bg=BG, highlightthickness=0, bd=0)
        self._about_dot.pack(side="left", pady=(2, 0))
        self._about_dot_item = self._about_dot.create_oval(4, 4, 12, 12, fill=OFF, outline="")
        tk.Label(frame, textvariable=self._about_status, bg=BG, fg=DIM, font=_f(9),
                 anchor="w", justify="left", wraplength=330).pack(side="left", padx=(6, 8))
        self._check_button = PillButton(frame, tk, "Check now",
                                        lambda: self._check_updates(self._about_status),
                                        kind="primary", width=112, height=32)
        self._check_button.pack(side="right")

        footer = tk.Frame(shell, bg=BG)
        footer.pack(fill="x", pady=(20, 0))
        PillButton(footer, tk, "Close", win.destroy, width=96, height=32).pack(side="right")
        PillButton(footer, tk, "Release notes", lambda: self._open_url(RELEASES_URL),
                   width=126, height=32).pack(side="right", padx=(0, 8))
        PillButton(footer, tk, "Copy details", self._copy_about_details,
                   width=118, height=32).pack(side="right", padx=(0, 8))

        win.update_idletasks()
        width = max(520, shell.winfo_reqwidth())
        height = shell.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() + 80
        win.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        if check:
            self._check_updates(self._about_status)

    def _copy_about_details(self) -> None:
        details = (f"{APP_NAME} {__version__} ({LICENCE}, {AUTHOR})\n"
                   f"{platform.system()} {platform.release()} · Python {platform.python_version()}\n"
                   f"{RELEASES_URL}")
        self.root.clipboard_clear()
        self.root.clipboard_append(details)
        if hasattr(self, "_about_status"):
            self._about_status.set("Details copied to the clipboard.")

    def _check_updates(self, status_var) -> None:
        """Ask GitHub for the newest release in a thread; report back on the UI thread."""
        status_var.set("Checking GitHub…")
        if hasattr(self, "_about_dot"):
            self._about_dot.itemconfigure(self._about_dot_item, fill=ACCENT)

        def worker() -> None:
            message, colour = "", OFF
            try:
                request = urllib.request.Request(
                    RELEASES_API,
                    headers={"User-Agent": f"{APP_NAME}/{__version__}",
                             "Accept": "application/vnd.github+json"})
                with urllib.request.urlopen(request, timeout=8) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                latest = str(payload.get("tag_name") or "").lstrip("vV")
                published = str(payload.get("published_at") or "")[:10]
                if latest and _version_tuple(latest) > _version_tuple(__version__):
                    message = (f"v{latest} is out ({published}). You are on v{__version__} — "
                               f"open Release notes to update.")
                    colour = ACCENT
                else:
                    message = f"You are on the latest release (v{__version__})."
                    colour = OK
            except urllib.error.HTTPError as error:
                message = f"GitHub answered {error.code}; no release found to compare against."
                colour = OFF
            except Exception:
                message = "Could not reach GitHub. Check the connection and try again."
                colour = BAD

            def report() -> None:
                status_var.set(message)
                if hasattr(self, "_about_dot"):
                    self._about_dot.itemconfigure(self._about_dot_item, fill=colour)

            self.root.after(0, report)

        threading.Thread(target=worker, daemon=True).start()

    def _copy_command(self) -> None:
        target = self._typed_target()
        if target is None:
            self.status.set("Choose a file first.")
            return
        parts = [APP_NAME, "fix", f'"{target}"']
        reference = self.ref_var.get().strip().strip('"')
        if reference:
            parts += ["--reference", f'"{reference}"']
        strategy = self._strategy_value()
        if strategy != "auto":
            parts += ["--strategy", strategy]
        if self.deep_var.get():
            parts.append("--deep")
        self.root.clipboard_clear()
        self.root.clipboard_append(" ".join(parts))
        self.status.set("The command line for this file is on the clipboard.")

    # ---------------------------------------------------------------- facts
    def _typed_target(self) -> Path | None:
        typed = self.target_var.get().strip().strip('"')
        return Path(typed) if typed else None

    def _strategy_value(self) -> str:
        chosen = self.strategy_var.get()
        for label, value in STRATEGIES:
            if label == chosen:
                return value
        return "auto"

    def _reference_typed(self, *_args) -> None:
        """Re-read the verdict once the typing stops, not on every key."""
        if self._ref_job is not None:
            self.root.after_cancel(self._ref_job)
        self._ref_job = self.root.after(400, self._refresh_facts)

    def _refresh_facts(self) -> None:
        target = self._typed_target()
        self._facts_token += 1
        token = self._facts_token
        if target is None:
            self.source_name.configure(text="No file chosen")
            self.source_info.configure(text="")
            self.duration_label.configure(text="")
            return
        self.source_name.configure(text=f"{target.name or target}")
        self.source_info.configure(text="reading it…")
        threading.Thread(target=self._facts_worker, args=(token, target), daemon=True).start()

    def _facts_worker(self, token: int, target: Path) -> None:
        facts = inspect(target, deep=False)
        self.root.after(0, lambda: self._show_facts(token, facts))

    def _show_facts(self, token: int, facts: dict) -> None:
        if token != self._facts_token:              # a later choice already won
            return
        self.duration_label.configure(
            text=facts["duration_text"] if facts["duration_text"] != "—" else "")
        self.fact_vars["file"].set(facts["name"])
        self.fact_vars["size"].set(facts["size_text"])
        self.fact_vars["kind"].set(facts["kind"])
        self.fact_vars["duration"].set(facts["duration_text"])
        self.fact_vars["video"].set(facts["video"])
        self.fact_vars["audio"].set(facts["audio"])
        if facts["error"]:
            self.fact_vars["verdict"].set(facts["error"])
            self.fact_labels["verdict"].configure(fg=BAD)
            self.source_info.configure(text=facts["error"])
            return
        verdict = facts["severity_text"]
        if facts["needs_reference"]:
            verdict += (" — reference chosen" if self.ref_var.get().strip()
                        else " — needs a reference file")
        self.fact_vars["verdict"].set(verdict)
        self.fact_labels["verdict"].configure(fg=facts["tone"])
        self.fact_vars["write"].set(facts["would_write"])
        bits = [facts["size_text"], facts["kind"]]
        if facts["duration_text"] != "—":
            bits.append(facts["duration_text"])
        for key in ("video", "audio"):
            if facts[key] not in ("—", "none"):
                bits.append(facts[key])
        self.source_info.configure(text=" · ".join(b for b in bits if b))
        self.diag_head.configure(text=f"{verdict}  ·  {facts['name']}", fg=facts["tone"])
        self._set_notes(facts["notes"] or ["nothing to report"])
        if facts["needs_reference"] and not self.ref_var.get().strip():
            self.status.set("This damage needs a file that still plays: pick one under Reference.")

    def _set_notes(self, notes: list[str]) -> None:
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        for note in notes:
            self.notes.insert("end", f"• {note}\n")
        self.notes.configure(state="disabled")

    # ---------------------------------------------------------------- running
    def _start(self) -> None:
        if self.busy:
            return
        target = self._typed_target()
        if target is None or not target.exists():
            self.status.set("Choose a file or a folder first.")
            return
        typed_ref = self.ref_var.get().strip().strip('"')
        reference = Path(typed_ref) if typed_ref else None
        if reference is not None and not reference.exists():
            self.status.set("The file that plays fine cannot be found.")
            return
        typed_out = self.output_var.get().strip().strip('"')
        out_path = Path(typed_out) if typed_out else None
        if out_path is not None:
            try:
                out_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.status.set(f"Cannot write there: {exc}")
                return
        self.target, self.reference = target, reference
        self.busy = True
        self.stop.clear()
        self.go.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.open_dir.configure(state="disabled")
        self.progress.configure(mode="determinate", maximum=100, value=0)
        self.progress.pack(**self._progress_pad)
        self._set_dot(ACCENT)
        self._clear_log()
        self.status.set("Working… a long file can take a few minutes.")
        threading.Thread(target=self._work, args=(out_path,), daemon=True).start()

    def _work(self, output_dir: Path | None) -> None:
        summary = None
        target = self.target
        try:
            if target is None:                       # cannot happen: _start refuses a missing target
                raise RuntimeError("no target chosen")
            summary = repair_target(
                target, reference=self.reference, deep=bool(self.deep_var.get()),
                strategy=self._strategy_value(), fps=_as_float(self.fps_var.get()),
                max_tries=_as_int(self.tries_var.get()), output_dir=output_dir,
                log=self._say_threadsafe, stopped=self.stop,
                progress=self._progress_threadsafe)
        except Exception:
            self._say_threadsafe(traceback.format_exc())
        finally:
            self.root.after(0, lambda: self._done(summary))

    def _stop(self) -> None:
        self.stop.set()
        self.status.set("Stopping after the file being repaired…")

    def _progress_threadsafe(self, done: int, total: int) -> None:
        self.root.after(0, lambda: self._progress(done, total))

    def _progress(self, done: int, total: int) -> None:
        self.progress.configure(mode="determinate", maximum=max(total, 1), value=done)

    def _done(self, summary: dict | None) -> None:
        self.busy = False
        self._source_typed()          # Repair is dark again if the source field was cleared
        self.stop_button.configure(state="disabled")
        self.progress.pack_forget()
        if summary is None:
            self._set_dot(BAD)
            self.status.set("Something went wrong — see the Activity Log.")
            return
        repaired, skipped, failed = (summary["repaired"], summary["skipped"], summary["failed"])
        self._set_dot(BAD if failed and not repaired else OK)
        if not summary["files"]:
            self.status.set("No video or audio files in there.")
        else:
            self.status.set(f"{repaired} repaired · {skipped} skipped · {failed} failed "
                            f"of {summary['files']} file(s)")
        written = summary.get("written") or []
        if written:
            self.fact_vars["write"].set(Path(written[-1]).name)
            self.fact_labels["write"].configure(fg=ACCENT)
        if repaired:
            self.open_dir.configure(state="normal")
            if self.open_when_done.get():
                self._open_folder()

    def _open_folder(self) -> None:
        folder = None
        typed = self.output_var.get().strip().strip('"')
        if typed and Path(typed).is_dir():
            folder = Path(typed)
        for candidate in (self.target, folder):
            if candidate is not None and candidate.is_dir():
                folder = candidate
                break
        if folder is None and self.target is not None:
            folder = self.target.parent
        if folder is None:
            return
        try:
            os.startfile(str(folder))
        except (AttributeError, OSError):
            opener = "explorer" if os.name == "nt" else "xdg-open"
            subprocess.Popen([opener, str(folder)])

    # ---------------------------------------------------------------- logging
    def _say_threadsafe(self, message: str) -> None:
        self.root.after(0, self._say, message)

    def _write_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _say(self, message: str) -> None:
        self._write_log(message)

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")


def _colourref(hex_colour: str) -> int:
    """#rrggbb as the COLORREF DWM wants: 0x00bbggrr, not 0x00rrggbb."""
    value = hex_colour.lstrip("#")
    red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return (blue << 16) | (green << 8) | red


def _as_float(text: str) -> float | None:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def _as_int(text: str) -> int | None:
    try:
        return int(float(str(text).strip()))
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    """Open the window. Returns 0 when it closes normally."""
    _devnull_streams()
    try:
        import tkinter as tk
        from tkinter import filedialog, ttk
    except ImportError as exc:                        # a Python without Tk should say so plainly
        print(f"this build has no window support ({exc}); use the command line instead",
              file=sys.stderr)
        return 3
    # A path on the command line means "open with this file": Windows does that when someone drops
    # a damaged video onto the exe or the Start Menu shortcut, and it saves them a file dialog.
    # The word "gui" may still be in front of it -- the windowed exe is handed `gui <file>` -- so the
    # first argument that names something on disk is the one to open, not necessarily the first one.
    prefill = next((str(Path(a)) for a in (argv or []) if a and Path(a).exists()), None)
    app = App(tk, ttk, filedialog, prefill=prefill)
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
