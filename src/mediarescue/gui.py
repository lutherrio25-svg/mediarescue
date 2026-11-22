"""A window for people who will not read a command line.

This is a shell, not a second implementation: it asks `cli.build_parser()` for the defaults so a
flag added to `fix` cannot silently go missing here, calls `diagnose()` and `cli._repair()` for
every decision, and keeps the rule that the command line keeps -- the original file is never
written to, and a repair that fails its own decode check is deleted rather than left behind
looking like it worked.

`repair_one()` and `repair_target()` are pure Python with no tkinter in sight, so the behaviour can
be tested on a machine without a display.
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


def _output_path(src: Path) -> Path:
    """Where this repair goes: beside the source, and never over an earlier repair."""
    out = src.with_name(src.stem + ".repaired" + src.suffix)
    if not out.exists():
        return out
    for n in range(2, 1000):
        candidate = src.with_name(f"{src.stem}.repaired-{n}{src.suffix}")
        if not candidate.exists():
            return candidate
    return src.with_name(src.stem + ".repaired-new" + src.suffix)


def repair_one(src: Path, reference: Path | None = None, deep: bool = False) -> dict:
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

    out = _output_path(src)
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
                  stopped: threading.Event | None = None) -> dict:
    """Repair one file or a whole folder, reporting progress as it goes."""
    say = log or (lambda _msg: None)
    files = [target] if target.is_file() else find_files(target)
    stats = {"files": len(files), "repaired": 0, "skipped": 0, "failed": 0, "folder": None}
    if not files:
        say("no video or audio files found there")
        return stats
    stats["folder"] = str(files[0].parent)
    for index, src in enumerate(files, 1):
        if stopped is not None and stopped.is_set():
            say("stopped — the files finished so far are on disk")
            break
        say(f"[{index}/{len(files)}] {src.name}")
        result = repair_one(src, reference=reference, deep=deep)
        written = (result.get("output") or "").strip()
        if result.get("ok") and written:
            stats["repaired"] += 1
            stats["folder"] = str(Path(written).parent)
            strategy = result.get("strategy") or ""
            say(f"    repaired — {Path(written).name}" + (f"  ({strategy})" if strategy else ""))
        elif result.get("ok"):
            stats["skipped"] += 1
            say(f"    skipped — {result.get('reason', '')}")
        else:
            stats["failed"] += 1
            say(f"    failed — {result.get('reason', '')}")
    return stats


def _devnull_streams() -> None:
    """A windowed build has no stdout/stderr; libraries that print must not crash on None."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            # Lives for the life of the process on purpose: a window has no terminal to close.
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


class App:
    """The window itself. tkinter is a parameter so importing this module needs no display."""

    def __init__(self, tk, ttk, filedialog, prefill: str | None = None) -> None:
        self.tk, self.ttk, self.filedialog = tk, ttk, filedialog
        self.root = tk.Tk()
        self.root.title(f"{APP_NAME} {__version__}")
        self.root.minsize(680, 500)
        self.root.geometry("780x580")
        self.target: Path | None = None
        self.reference: Path | None = None
        self.busy = False
        self.stop = threading.Event()
        self._build()
        if prefill:                     # a file dropped onto the exe arrives here as argv[1]
            self.target = Path(prefill)
            self.target_var.set(str(self.target))
            self.status.set("Press Repair to fix this file.")

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        tk, ttk = self.tk, self.ttk
        style = ttk.Style(self.root)
        # One bold style for the action the whole window exists for; the native theme ignores
        # background colours, so weight is the honest way to say "this is the button".
        style.configure("Primary.TButton", font=("Segoe UI", 9, "bold"))

        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)

        ttk.Label(outer, text="Repair a damaged video or audio file",
                  font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Label(outer, foreground="#555", wraplength=720, justify="left",
                  text="Type a file or a folder, or pick one. The original is never modified — the "
                       "repair is written next to it with .repaired in the name.").grid(
            row=1, column=0, sticky="w", pady=(2, 14))

        # Both rows share one grid so the two fields line up: separate frames made the second row
        # start at a different x than the first.
        form = ttk.Frame(outer)
        form.grid(row=2, column=0, sticky="ew")
        form.columnconfigure(1, weight=1, minsize=260)
        form.columnconfigure(0, minsize=130)

        ttk.Label(form, text="File or folder").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.target_var = tk.StringVar(value="")
        self.target_entry = ttk.Entry(form, textvariable=self.target_var)
        self.target_entry.grid(row=0, column=1, sticky="ew", columnspan=2)
        ttk.Button(form, text="Choose file…", command=self._choose_file).grid(row=0, column=3,
                                                                             padx=(8, 0))
        ttk.Button(form, text="Choose folder…", command=self._choose_folder).grid(row=0, column=4,
                                                                                 padx=(6, 0))

        ttk.Label(form, text="Plays fine (optional)").grid(row=1, column=0, sticky="w",
                                                          pady=(10, 0), padx=(0, 10))
        self.ref_var = tk.StringVar(value="")
        ttk.Entry(form, textvariable=self.ref_var).grid(row=1, column=1, sticky="ew",
                                                       columnspan=2, pady=(10, 0))
        ttk.Button(form, text="Choose…", command=self._choose_reference).grid(
            row=1, column=3, columnspan=2, sticky="w", padx=(8, 0), pady=(10, 0))

        ttk.Label(outer, foreground="#555", wraplength=720, justify="left",
                  text="Only needed when the damage destroyed the file's index. Any intact file "
                       "from the same phone, app or download works — it does not have to be the "
                       "same video.").grid(row=3, column=0, sticky="w", pady=(6, 12))

        self.deep_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(outer, variable=self.deep_var,
                        text="Look more closely (decodes the whole file first — slower, finds "
                             "damage the header hides)").grid(row=4, column=0, sticky="w")

        actions = ttk.Frame(outer)
        actions.grid(row=5, column=0, sticky="w", pady=(14, 10))
        self.go = ttk.Button(actions, text="Repair", command=self._start, width=16,
                             style="Primary.TButton")
        self.go.grid(row=0, column=0)
        self.open_dir = ttk.Button(actions, text="Open folder", state="disabled",
                                   command=self._open_folder, width=16)
        self.open_dir.grid(row=0, column=1, padx=(8, 0))
        self.stop_button = ttk.Button(actions, text="Stop", state="disabled", command=self._stop,
                                      width=10)
        self.stop_button.grid(row=0, column=2, padx=(8, 0))
        self.root.bind("<Return>", lambda _event: self._start())

        log_frame = ttk.Frame(outer)
        log_frame.grid(row=6, column=0, sticky="nsew")
        outer.rowconfigure(6, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=12, width=1, wrap="word", state="disabled",
                           font=("Consolas", 9), background="#fbfbfb", relief="solid",
                           borderwidth=1)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)
        self._write_log("Progress and the verdict for each file will appear here.")

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(outer, textvariable=self.status, font=("Segoe UI", 9, "bold")).grid(
            row=7, column=0, sticky="w", pady=(8, 0))

    # ---------------------------------------------------------------- picking
    def _choose_file(self) -> None:
        chosen = self.filedialog.askopenfilename(
            title="Choose the damaged file",
            filetypes=[("Video and audio",
                        ("*.mp4 *.mkv *.mov *.avi *.m4v *.webm *.flv *.mp3 *.m4a "
                         "*.wav *.flac *.aac *.ogg *.ts *.mts *.m2ts *.3gp")),
                       ("All files", "*.*")])
        if chosen:
            self.target = Path(chosen)
            self.target_var.set(str(self.target))

    def _choose_folder(self) -> None:
        chosen = self.filedialog.askdirectory(title="Choose a folder of damaged files")
        if chosen:
            self.target = Path(chosen)
            self.target_var.set(str(self.target))

    def _choose_reference(self) -> None:
        chosen = self.filedialog.askopenfilename(title="Choose a file that still plays",
                                                 filetypes=[("All files", "*.*")])
        if chosen:
            self.reference = Path(chosen)
            self.ref_var.set(str(self.reference))

    # ---------------------------------------------------------------- running
    def _typed_target(self) -> Path | None:
        typed = self.target_var.get().strip().strip('"')
        return Path(typed) if typed else None

    def _start(self) -> None:
        if self.busy:
            return
        target = self._typed_target()
        if target is None or not target.exists():
            self.status.set("Choose a file or a folder first.")
            return
        reference = Path(self.ref_var.get().strip().strip('"')) if self.ref_var.get().strip() else None
        if reference is not None and not reference.exists():
            self.status.set("The file that plays fine cannot be found.")
            return
        self.target, self.reference = target, reference
        self.busy = True
        self.stop.clear()
        self.go.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.open_dir.configure(state="disabled")
        self._clear_log()
        self.status.set("Working… a long file can take a few minutes.")
        thread = threading.Thread(target=self._work, daemon=True)
        thread.start()

    def _work(self) -> None:
        summary = None
        target = self.target
        try:
            if target is None:                       # cannot happen: _start refuses a missing target
                raise RuntimeError("no target chosen")
            summary = repair_target(target, reference=self.reference,
                                    deep=bool(self.deep_var.get()), log=self._say_threadsafe,
                                    stopped=self.stop)
        except Exception:
            self._say_threadsafe(traceback.format_exc())
        finally:
            self.root.after(0, lambda: self._done(summary))

    def _stop(self) -> None:
        self.stop.set()
        self.status.set("Stopping after the file being repaired…")

    def _done(self, summary: dict | None) -> None:
        self.busy = False
        self.go.configure(state="normal")
        self.stop_button.configure(state="disabled")
        if summary is None:
            self.status.set("Something went wrong — see the log above.")
            return
        repaired, skipped, failed = (summary["repaired"], summary["skipped"], summary["failed"])
        if not summary["files"]:
            self.status.set("No video or audio files in there.")
        else:
            self.status.set(f"{repaired} repaired · {skipped} skipped · {failed} failed "
                            f"of {summary['files']} file(s)")
        if repaired:
            self.open_dir.configure(state="normal")

    def _open_folder(self) -> None:
        folder = None
        if self.reference is not None:
            folder = self.reference.parent
        for candidate in (self.target, folder):
            if candidate is not None and candidate.is_dir():
                folder = candidate
                break
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
    prefill = str(Path(argv[0])) if argv and Path(argv[0]).exists() else None
    app = App(tk, ttk, filedialog, prefill=prefill)
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
