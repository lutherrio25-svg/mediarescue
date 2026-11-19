"""`python -m mediarescue` runs the same entry point as the `mediarescue` script."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
