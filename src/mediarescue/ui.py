"""Terminal output helpers: colour when a human is watching, plain text when it is piped.

A repair tool gets run in scripts, cron jobs and CI as often as by hand, so ANSI escapes must
never leak into a log file. `isatty` decides by default, `NO_COLOR`/`FORCE_COLOR` and
`--no-color` override it.
"""
from __future__ import annotations

import os
import re
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


_SURROGATE = re.compile("[\ud800-\udfff]")


def printable(text: str) -> str:
    """Replace unpaired surrogates a filesystem allows but a console refuses to display.

    NTFS stores names as raw UTF-16 units, so a damaged or hand-made directory entry can hold a
    lone surrogate. Python hands it back verbatim, and writing it to a Windows console fails with
    OSError EINVAL -- which used to kill a scan half-way through its table instead of printing one
    odd-looking filename.
    """
    return _SURROGATE.sub("\ufffd", text)


class ForgivingStream:
    """A stdout/stderr proxy that degrades instead of raising.

    Normal text goes straight through, so the happy path costs nothing. Two failure modes are
    folded into one place:

    * text the terminal cannot encode (a lone surrogate from NTFS, a CJK name on a legacy code
      page) -- retried sanitised, then with backslash escapes, never fatal;
    * a reader that walked away -- `mediarescue scan big-folder | head` closes the pipe, and
      Windows reports that as OSError EINVAL rather than BrokenPipeError. The stream goes quiet
      instead of burying the report in a traceback.
    """

    def __init__(self, stream):
        self._stream = stream
        self._dead = False

    def write(self, text: str) -> int:
        if self._dead:
            return len(text)
        try:
            return self._stream.write(text)
        except UnicodeEncodeError:
            pass
        except OSError:
            # Probably a closed pipe, but it can also be a console rejecting one character;
            # a sanitised retry tells the two apart.
            return self._retry(printable(text), text) if printable(text) != text else self._gone(text)
        cleaned = printable(text)
        try:
            return self._stream.write(cleaned)
        except UnicodeEncodeError:
            encoding = getattr(self._stream, "encoding", None) or "ascii"
            try:
                return self._stream.write(cleaned.encode(encoding, "backslashreplace").decode("ascii", "ignore"))
            except UnicodeEncodeError:
                return self._gone(text)
        except OSError:
            return self._gone(text)

    def _retry(self, cleaned: str, original: str) -> int:
        try:
            return self._stream.write(cleaned)
        except (UnicodeEncodeError, OSError):
            return self._gone(original)

    def _gone(self, text: str) -> int:
        """The reader left. Nothing else can be written, and that is not an error."""
        self._dead = True
        return len(text)

    def flush(self) -> None:
        if self._dead:
            return
        try:
            self._stream.flush()
        except OSError:
            self._dead = True

    def __getattr__(self, name):
        return getattr(self._stream, name)


def forgiving_output() -> None:
    """Install `ForgivingStream` on both streams, once per process."""
    if not isinstance(sys.stdout, ForgivingStream):
        sys.stdout = ForgivingStream(sys.stdout)  # type: ignore[assignment]
    if not isinstance(sys.stderr, ForgivingStream):
        sys.stderr = ForgivingStream(sys.stderr)  # type: ignore[assignment]
