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

import os
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable
from pathlib import Path

from . import __version__

APP_NAME = "mediarescue"
AUTHOR = "Star Corps bks"

# Sampled from the window this mimics: window #252525, panels #363636, the toolbar band a shade
# lighter, one green accent for the action the window exists for.
BG = "#252525"
PANEL = "#363636"
TOOLBAR = "#464646"
MENU = "#2b2b2b"
HOVER = "#575757"
FIELD = "#3a3a3a"
BORDER = "#1b1b1b"
FG = "#f0f0f0"
DIM = "#a6a6a6"
OFF = "#6f6f6f"
ACCENT = "#8ac65f"
WARN = "#e0a13a"
BAD = "#e06c6c"
OK = "#8ac65f"

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
                if codecs:
                    facts["video"] = codecs[0]
                    facts["audio"] = ", ".join(codecs[1:]) or "none"
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


class ToolButton:
    """A flat icon-plus-text action, the way the window it mimics draws its toolbar."""

    def __init__(self, parent, tk, label: str, icon: str, command, *, accent: bool = False,
                 bg: str = TOOLBAR) -> None:
        import tkinter.font as tkfont

        self.command = command
        self.icon = icon
        self.label = label
        self.accent = accent
        self.bg = bg
        self._state = "normal"
        font = tkfont.Font(family="Segoe UI", size=9, weight="bold" if accent else "normal")
        width = 40 + font.measure(label) + 16
        self.canvas = tk.Canvas(parent, width=width, height=42, bg=bg, highlightthickness=0,
                                bd=0, cursor="hand2")
        self._text_id = self.canvas.create_text(42, 21, text=label, anchor="w", fill=FG,
                                                font=font)
        self.holder = tk.Canvas(self.canvas, width=24, height=26, bg=bg, highlightthickness=0,
                                bd=0)
        self.canvas.create_window(8, 21, window=self.holder, anchor="w")
        self._paint_icon()
        self.canvas.bind("<Enter>", self._enter)
        self.canvas.bind("<Leave>", self._leave)
        self.canvas.bind("<Button-1>", self._click)

    def _paint_icon(self) -> None:
        self.holder.delete("all")
        colour = ACCENT if self.accent and self._state == "normal" else FG
        if self._state != "normal":
            colour = OFF
        _draw_icon(self.holder, self.icon, 1, colour)
        if self._state != "normal" and self.accent:
            # A disabled green play would still look like the thing to press.
            for item in self.holder.find_all():
                if self.holder.type(item) == "oval":
                    self.holder.itemconfigure(item, fill=OFF, outline=OFF)

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)

    # the two verbs the rest of the window (and the tests) use on a ttk.Button
    def configure(self, **kwargs):
        if "state" in kwargs:
            self._state = str(kwargs["state"])
            self.canvas.itemconfigure(self._text_id, fill=FG if self._state == "normal" else OFF)
            self.canvas.configure(cursor="hand2" if self._state == "normal" else "")
            self._paint_icon()
        return self

    def cget(self, key: str):
        if key == "state":
            return self._state
        if key == "text":
            return self.label
        raise KeyError(key)

    def _enter(self, _event=None):
        if self._state == "normal":
            self.canvas.configure(bg=HOVER)
            self.holder.configure(bg=HOVER)

    def _leave(self, _event=None):
        self.canvas.configure(bg=self.bg)
        self.holder.configure(bg=self.bg)

    def _click(self, _event=None):
        if self._state == "normal" and self.command:
            self.command()


class App:
    """The window itself. tkinter is a parameter so importing this module needs no display."""

    def __init__(self, tk, ttk, filedialog, prefill: str | None = None) -> None:
        self.tk, self.ttk, self.filedialog = tk, ttk, filedialog
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {__version__}")
        self.root.configure(bg=BG)
        self.root.minsize(960, 620)
        self.root.geometry("1060x690")
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
    def _dark_title_bar(self) -> None:
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
            hwnd = ctypes.c_void_p(int(self.root.wm_frame(), 16))

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

    def _style(self) -> None:
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                        bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                        troughcolor=BG, focuscolor=ACCENT, font=("Segoe UI", 9))
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=PANEL, foreground=FG)
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG,
                        bordercolor=BORDER, padding=4)
        style.map("TEntry", fieldbackground=[("readonly", FIELD)])
        style.configure("TCombobox", fieldbackground=FIELD, background=TOOLBAR, foreground=FG,
                        arrowcolor=FG, bordercolor=BORDER, padding=4)
        style.map("TCombobox",
                  fieldbackground=[("readonly", FIELD)],
                  selectbackground=[("readonly", FIELD)],
                  selectforeground=[("readonly", FG)],
                  foreground=[("readonly", FG)])
        style.configure("TSpinbox", fieldbackground=FIELD, background=TOOLBAR, foreground=FG,
                        arrowcolor=FG, bordercolor=BORDER, padding=4)
        style.configure("TCheckbutton", background=PANEL, foreground=FG)
        style.map("TCheckbutton", background=[("active", PANEL)],
                  indicatorcolor=[("selected", ACCENT)])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(8, 6, 0, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=DIM, padding=(16, 7),
                        borderwidth=0, font=("Segoe UI", 9))
        style.map("TNotebook.Tab",
                  background=[("selected", PANEL)],
                  foreground=[("selected", "#ffffff")],
                  expand=[("selected", (0, 0, 0, 0))])
        style.configure("TSeparator", background="#5a5a5a")
        style.configure("TProgressbar", background=ACCENT, troughcolor=FIELD,
                        bordercolor=FIELD, lightcolor=ACCENT, darkcolor=ACCENT, thickness=6)
        self.root.option_add("*TCombobox*Listbox.background", FIELD)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", TOOLBAR)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    def _menu(self) -> None:
        """Menus sit inside the client area, not in the native strip Windows draws itself.

        A tkinter menubar is a win32 menu belonging to the desktop theme: it ignores bg/fg and always
        arrives light, which leaves a white band under a dark title. Drawing our own row keeps the top
        of the window one colour; the dropdowns themselves are still native and stay light.
        """
        tk = self.tk
        opts = {"tearoff": 0, "bg": PANEL, "fg": FG, "activebackground": TOOLBAR,
                "activeforeground": "#ffffff", "bd": 0}
        strip = tk.Frame(self.root, bg=MENU, height=26)
        strip.pack(fill="x")
        strip.pack_propagate(False)
        self.menubar = strip
        file_menu = tk.Menu(self.root, **opts)
        file_menu.add_command(label="Open source…", command=self._choose_file)
        file_menu.add_command(label="Open a folder…", command=self._choose_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Open the results folder", command=self._open_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self.root.destroy)
        self._menubar_entry(strip, "File", file_menu)

        tools = tk.Menu(self.root, **opts)
        tools.add_command(label="Diagnosis details",
                          command=lambda: self.tabs.select(self.tab_diag))
        tools.add_command(label="Repair options", command=lambda: self.tabs.select(self.tab_opt))
        tools.add_separator()
        tools.add_command(label="Copy the command line for this file", command=self._copy_command)
        self._menubar_entry(strip, "Tools", tools)

        help_menu = tk.Menu(self.root, **opts)
        help_menu.add_command(label="About mediarescue", command=self._about)
        self._menubar_entry(strip, "Help", help_menu)

    def _menubar_entry(self, strip, label: str, menu) -> None:
        """One word of the menu row: hover to light it, click to drop its menu underneath."""
        item = self.tk.Label(strip, text=label, bg=MENU, fg=FG, padx=12,
                             font=("Segoe UI", 9))
        item.pack(side="left", fill="y")
        item.bind("<Enter>", lambda _event: item.configure(bg=HOVER, fg="#ffffff"))
        item.bind("<Leave>", lambda _event: item.configure(bg=MENU, fg=FG))
        item.bind("<Button-1>", lambda event: menu.post(item.winfo_rootx(),
                                                        item.winfo_rooty() + item.winfo_height()))

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        self._menu()

        # --- toolbar: flat icon buttons, one green primary action, thin separators between groups
        bar = tk.Frame(self.root, bg=TOOLBAR, height=46)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.open_source = ToolButton(bar, tk, "Open Source", "open", self._choose_file)
        self.open_source.pack(side="left")
        self._sep(bar)
        self.open_folder_btn = ToolButton(bar, tk, "Open Folder", "folder", self._choose_folder)
        self.open_folder_btn.pack(side="left")
        self._sep(bar)
        self.go = ToolButton(bar, tk, "Start Repair", "play", self._start, accent=True)
        self.go.pack(side="left")
        self.stop_button = ToolButton(bar, tk, "Stop", "stop", self._stop)
        self.stop_button.configure(state="disabled")
        self.stop_button.pack(side="left")
        self._sep(bar)
        self.open_dir = ToolButton(bar, tk, "Results Folder", "folder", self._open_folder)
        self.open_dir.configure(state="disabled")
        self.open_dir.pack(side="left")
        self._sep(bar)
        self.log_button = ToolButton(bar, tk, "Activity Log", "log",
                                     lambda: self.tabs.select(self.tab_log))
        self.log_button.pack(side="left")

        # --- source line, exactly where the other window puts it
        band = tk.Frame(self.root, bg=BG)
        band.pack(fill="x", pady=(10, 2), padx=14)
        self.source_name = tk.Label(band, text="Source: no file chosen", bg=BG, fg=FG,
                                    font=("Segoe UI", 10, "bold"), anchor="w")
        self.source_name.pack(side="left")
        self.source_info = tk.Label(band, text="", bg=BG, fg=DIM, anchor="w",
                                    font=("Segoe UI", 9))
        self.source_info.pack(side="left", padx=(10, 0))

        # --- the two option rows the other window keeps above its tabs
        rows = tk.Frame(self.root, bg=BG)
        rows.pack(fill="x", padx=14, pady=(6, 2))
        rows.columnconfigure(1, weight=1)

        self.target_var = tk.StringVar(value="")
        self.ref_var = tk.StringVar(value="")
        self._ref_job: str | None = None
        self.ref_var.trace_add("write", self._reference_typed)
        label_kw = {"bg": BG, "fg": DIM, "font": ("Segoe UI", 9), "width": 9, "anchor": "w"}

        tk.Label(rows, text="File:", **label_kw).grid(row=0, column=0, sticky="w")
        self.target_entry = ttk.Entry(rows, textvariable=self.target_var, font=("Segoe UI", 9))
        self.target_entry.grid(row=0, column=1, sticky="ew", pady=2)
        ttk.Button(rows, text="Browse…", command=self._choose_file).grid(row=0, column=2,
                                                                        padx=(6, 0))
        self.duration_label = tk.Label(rows, text="Duration: —", bg=BG, fg=DIM,
                                       font=("Segoe UI", 9), width=18, anchor="w")
        self.duration_label.grid(row=0, column=3, padx=(12, 0), sticky="w")

        tk.Label(rows, text="Reference:", **label_kw).grid(row=1, column=0, sticky="w",
                                                           pady=(6, 0))
        ttk.Entry(rows, textvariable=self.ref_var, font=("Segoe UI", 9)).grid(
            row=1, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(rows, text="Browse…", command=self._choose_reference).grid(
            row=1, column=2, padx=(6, 0), pady=(6, 0))
        tk.Label(rows, text="only when the index is gone", bg=BG, fg=DIM,
                 font=("Segoe UI", 8), anchor="w").grid(row=1, column=3, padx=(12, 0),
                                                        pady=(6, 0), sticky="w")

        tk.Label(rows, text="Strategy:", **label_kw).grid(row=2, column=0, sticky="w",
                                                          pady=(6, 0))
        self.strategy_var = tk.StringVar(value=STRATEGIES[0][0])
        self.strategy_box = ttk.Combobox(rows, textvariable=self.strategy_var, state="readonly",
                                         values=[label for label, _value in STRATEGIES],
                                         font=("Segoe UI", 9))
        self.strategy_box.grid(row=2, column=1, sticky="ew", pady=(6, 0))
        ttk.Button(rows, text="Reset", command=self._reset_options).grid(row=2, column=2,
                                                                        padx=(6, 0), pady=(6, 0))

        # --- tabs
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=14, pady=(8, 0))
        self.tab_sum = ttk.Frame(self.tabs, style="Panel.TFrame", padding=14)
        self.tab_diag = ttk.Frame(self.tabs, style="Panel.TFrame", padding=14)
        self.tab_opt = ttk.Frame(self.tabs, style="Panel.TFrame", padding=14)
        self.tab_log = ttk.Frame(self.tabs, style="Panel.TFrame", padding=8)
        self.tabs.add(self.tab_sum, text="Summary")
        self.tabs.add(self.tab_diag, text="Diagnosis")
        self.tabs.add(self.tab_opt, text="Options")
        self.tabs.add(self.tab_log, text="Activity Log")
        self._build_summary()
        self._build_diagnosis()
        self._build_options()
        self._build_log()

        # --- save row, then the status bar: left the verdict, right the standing rule
        save = tk.Frame(self.root, bg=BG)
        save.pack(fill="x", padx=14, pady=(8, 6))
        save.columnconfigure(1, weight=1)
        tk.Label(save, text="Save in:", **label_kw).grid(row=0, column=0, sticky="w")
        self.output_var = tk.StringVar(value="")
        ttk.Entry(save, textvariable=self.output_var, font=("Segoe UI", 9)).grid(
            row=0, column=1, sticky="ew")
        ttk.Button(save, text="Browse…", command=self._choose_output).grid(row=0, column=2,
                                                                          padx=(6, 0))
        tk.Label(save, text="empty = beside the original", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).grid(row=0, column=3, padx=(12, 0), sticky="w")

        status = tk.Frame(self.root, bg=PANEL, height=30)
        status.pack(fill="x", side="bottom")
        status.pack_propagate(False)
        self.status = tk.StringVar(value="Ready")
        self.status_label = tk.Label(status, textvariable=self.status, bg=PANEL, fg=FG,
                                     font=("Segoe UI", 9), anchor="w")
        self.status_label.pack(side="left", padx=14)
        self.progress = ttk.Progressbar(status, mode="determinate", maximum=100, length=180)
        self.progress.pack(side="left", padx=10, pady=8)
        tk.Label(status, text=f"Original files are never modified  ·  {AUTHOR}", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8)).pack(side="right", padx=14)

    def _sep(self, parent) -> None:
        self.ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", pady=8,
                                                           padx=2)

    def _build_summary(self) -> None:
        tk = self.tk
        frame = self.tab_sum
        frame.columnconfigure(1, weight=1)
        # tkinter arrives as a parameter here, so the element types stay unannotated.
        self.fact_vars: dict = {}
        self.fact_labels: dict = {}
        rows = [("file", "File"), ("size", "Size"), ("kind", "Container"),
                ("duration", "Duration"), ("video", "Video"), ("audio", "Audio"),
                ("verdict", "Verdict"), ("write", "Output")]
        for index, (key, label) in enumerate(rows):
            tk.Label(frame, text=label, bg=PANEL, fg=DIM, font=("Segoe UI", 9), width=12,
                     anchor="w").grid(row=index, column=0, sticky="w", pady=3)
            var = tk.StringVar(value="—")
            value = tk.Label(frame, textvariable=var, bg=PANEL,
                             fg=DIM if key == "verdict" else FG,
                             font=("Segoe UI", 9), anchor="w")
            value.grid(row=index, column=1, sticky="w", pady=3)
            self.fact_vars[key] = var
            self.fact_labels[key] = value
        tk.Label(frame, text="The original is never written to. A repair that fails its own "
                             "decode check is deleted, not left behind looking like it worked.",
                 bg=PANEL, fg=DIM, font=("Segoe UI", 8), justify="left",
                 wraplength=620).grid(row=len(rows), column=0, columnspan=2, sticky="w",
                                      pady=(12, 0))

    def _build_diagnosis(self) -> None:
        tk = self.tk
        frame = self.tab_diag
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        self.diag_head = tk.Label(frame, text="No file chosen yet.", bg=PANEL, fg=FG,
                                  font=("Segoe UI", 10, "bold"), anchor="w")
        self.diag_head.grid(row=0, column=0, sticky="w")
        self.notes = tk.Text(frame, height=8, width=1, wrap="word", state="disabled",
                             bg=PANEL, fg="#dcdcdc", relief="flat", borderwidth=0,
                             font=("Segoe UI", 9), padx=0, pady=6)
        self.notes.grid(row=1, column=0, sticky="nsew", pady=(6, 0))

    def _build_options(self) -> None:
        tk, ttk = self.tk, self.ttk
        frame = self.tab_opt
        frame.columnconfigure(1, weight=1)
        self.deep_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, variable=self.deep_var,
                        text="Deep check — decode the whole file first (slower, finds damage "
                             "the header hides)").grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(frame, text="Frame rate used when rebuilding timestamps", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 9)).grid(row=1, column=0, sticky="w", pady=(12, 0))
        self.fps_var = tk.StringVar(value="25.0")
        ttk.Spinbox(frame, from_=1.0, to=240.0, increment=0.001, width=10,
                    textvariable=self.fps_var).grid(row=1, column=1, sticky="w", pady=(12, 0))
        tk.Label(frame, text="Attempts before a strategy is called beaten", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.tries_var = tk.StringVar(value="8")
        ttk.Spinbox(frame, from_=1, to=99, width=10, textvariable=self.tries_var).grid(
            row=2, column=1, sticky="w", pady=(10, 0))
        self.open_when_done = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, variable=self.open_when_done,
                        text="Open the results folder when it finishes").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(14, 0))
        tk.Label(frame, text="These are the same switches the command line takes: --deep, --fps, "
                             "--max-tries, --strategy.", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8)).grid(row=4, column=0, columnspan=2, sticky="w",
                                            pady=(16, 0))

    def _build_log(self) -> None:
        tk = self.tk
        frame = self.tab_log
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.log = tk.Text(frame, height=10, width=1, wrap="word", state="disabled",
                           font=("Consolas", 9), bg="#2b2b2b", fg="#dcdcdc", relief="flat",
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

    def _about(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME} {__version__}\n\n"
            "Repairs damaged video and audio files without touching the original.\n\n"
            "The window runs the same code as the command line: mediarescue fix <file>. Anything "
            "this window can do, the command line can do too.\n\n"
            f"Made by {AUTHOR}.")

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
            self.source_name.configure(text="Source: no file chosen")
            self.source_info.configure(text="")
            self.duration_label.configure(text="Duration: —")
            return
        self.source_name.configure(text=f"Source: {target.name or target}")
        self.source_info.configure(text="reading it…")
        threading.Thread(target=self._facts_worker, args=(token, target), daemon=True).start()

    def _facts_worker(self, token: int, target: Path) -> None:
        facts = inspect(target, deep=False)
        self.root.after(0, lambda: self._show_facts(token, facts))

    def _show_facts(self, token: int, facts: dict) -> None:
        if token != self._facts_token:              # a later choice already won
            return
        self.duration_label.configure(text=f"Duration: {facts['duration_text']}")
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
        if facts["video"] != "—":
            bits.append(facts["video"])
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
        self.go.configure(state="normal")
        self.stop_button.configure(state="disabled")
        if summary is None:
            self.status.set("Something went wrong — see the Activity Log.")
            return
        repaired, skipped, failed = (summary["repaired"], summary["skipped"], summary["failed"])
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
