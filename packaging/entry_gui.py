"""Launcher for the windowed build: no console, so this is the exe a Start Menu shortcut points at.

Same two rules as `entry.py`: absolute imports (PyInstaller runs this as a top-level script) and a
UTF-8 stdout/stderr when there is one -- a windowed build has no streams at all, which `gui.main`
handles.
"""

import contextlib
import sys

from mediarescue.gui import main


def _utf8(stream) -> None:
    if stream is None:
        return
    with contextlib.suppress(ValueError, OSError):
        stream.reconfigure(encoding="utf-8", errors="replace")


for _stream in (sys.stdout, sys.stderr):
    _utf8(_stream)

raise SystemExit(main(sys.argv[1:]))
