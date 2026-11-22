"""Entry point for the frozen build.

`python -m mediarescue` works because the package gives __main__ a parent package; PyInstaller
runs this file as a top-level script instead, so the relative import inside __main__.py fails.
This launcher imports absolutely and exits with the CLI's return code -- that code is the verdict
(success, decode failure, bad arguments), so it must survive into the .exe.
"""
import contextlib
import sys

from mediarescue.cli import main


def _utf8(stream) -> None:
    """The frozen interpreter defaults to the ANSI code page, so an em dash came out as byte 0x97
    while the wheel printed UTF-8: the same program saying the same thing in two different bytes.
    Verdicts get pasted into issue trackers, so keep them identical to the pip install."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    with contextlib.suppress(ValueError, OSError):
        reconfigure(encoding="utf-8", errors="replace")


if __name__ == "__main__":
    _utf8(sys.stdout)
    _utf8(sys.stderr)
    raise SystemExit(main())
