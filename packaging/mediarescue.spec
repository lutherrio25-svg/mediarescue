# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone Windows build.

One *folder*, not one file: it starts instantly, it carries ffmpeg and ffprobe beside the exe so
the person running it never installs anything, and Inno Setup then turns that folder into a single
setup.exe. ffmpeg is found through MEDIARESCUE_BUNDLE_FFMPEG/FFPROBE when set (CI downloads a
static build), otherwise from PATH.
"""
import os
import shutil
from pathlib import Path

root = Path(SPECPATH).parent
src = root / "src"


def _tool(name: str) -> str:
    override = os.environ.get(f"MEDIARESCUE_BUNDLE_{name.upper()}")
    if override:
        return override
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found: set MEDIARESCUE_BUNDLE_{name.upper()} to its exe")
    return found


icon = root / "packaging" / "mediarescue.ico"

a = Analysis(
    [str(root / "packaging" / "entry.py")],
    pathex=[str(src)],
    binaries=[(_tool("ffmpeg"), "."), (_tool("ffprobe"), ".")],
    datas=[],
    hiddenimports=[],
    # Nothing here draws a window or runs tests; leaving them out keeps the folder honest about
    # what it contains.
    excludes=["tkinter", "pytest", "_pytest"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mediarescue",
    console=True,
    upx=False,
    icon=str(icon) if icon.is_file() else None,
    version=str(root / "packaging" / "version_info.txt")
    if (root / "packaging" / "version_info.txt").is_file()
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="mediarescue",
)
