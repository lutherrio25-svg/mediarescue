"""Terminal output helpers: colour when a human is watching, plain text when it is piped.

A repair tool gets run in scripts, cron jobs and CI as often as by hand, so ANSI escapes must
never leak into a log file. `isatty` decides by default, `NO_COLOR`/`FORCE_COLOR` and
`--no-color` override it.
"""
from __future__ import annotations

import os
import sys

_CODES = {
    "green": "32",
    "red": "31",
    "yellow": "33",
    "blue": "34",
    "cyan": "36",
    "dim": "2",
    "bold": "1",
    "reverse": "7",
}


def enabled(stream=None, override: bool | None = None) -> bool:
    if override is not None:
        return override
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    stream = stream if stream is not None else sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def paint(text: str, colour: str, on: bool) -> str:
    """Wrap `text` in an ANSI colour when `on`, otherwise return it untouched."""
    if not on or colour not in _CODES:
        return text
    return f"\033[{_CODES[colour]}m{text}\033[0m"


def colour_for(on: bool):
    """`paint` with the decision already baked in."""
    def _paint(text: str, colour: str) -> str:
        return paint(text, colour, on)
    return _paint
