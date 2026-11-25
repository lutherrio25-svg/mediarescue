# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the standalone Windows build.

One *folder*, not one file: it starts instantly, it carries ffmpeg and ffprobe beside the exe so
the person running it never installs anything, and Inno Setup then turns that folder into a single
setup.exe. ffmpeg is found through MEDIARESCUE_BUNDLE_FFMPEG/FFPROBE when set (CI downloads a
static build), otherwise from PATH.

Two exes share the one folder, because "console or not" is a property of the exe rather than of the
code: `mediarescue.exe` keeps the command line (scripts, README, CI smoke test) and
`mediarescue-window.exe` is the same program with no console window -- the one the Start Menu entry
points at. They share `_internal/`, so the second exe costs a launcher, not another 200 MB.
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
version_file = root / "packaging" / "version_info.txt"


def _ffmpeg_binaries() -> list[tuple[str, str]]:
    """Prefer the *shared* ffmpeg build: ffmpeg.exe and ffprobe.exe stay under a megabyte while one
    set of avcodec/avformat/avfilter DLLs serves both of them. Two static binaries cost 336 MB here
    -- 96% of the whole folder -- against 162 MB shared, and the LGPL variant keeps this project
    free of GPL-only codecs it never needed for repairing containers."""
    directory = os.environ.get("MEDIARESCUE_BUNDLE_FFMPEG_DIR")
    if directory:
        base = Path(directory)
        files = [(str(base / "ffmpeg.exe"), "."), (str(base / "ffprobe.exe"), ".")]
        files += [(str(dll), ".") for dll in sorted(base.glob("*.dll"))]
        missing = [source for source, _ in files if not Path(source).is_file()]
        if missing:
            raise SystemExit(f"MEDIARESCUE_BUNDLE_FFMPEG_DIR is missing: {', '.join(missing)}")
        return files
    return [(_tool("ffmpeg"), "."), (_tool("ffprobe"), ".")]


def _analysis(entry: str, excludes: list[str], binaries=None, datas=None):
    return Analysis(
        [str(root / "packaging" / entry)],
        pathex=[str(src)],
        binaries=binaries or [],
        datas=datas or [],
        hiddenimports=[],
        excludes=excludes,
        noarchive=False,
        optimize=0,
    )


# The command line does not need Tk; leaving it out keeps that build honest about what it contains.
a_cli = _analysis("entry.py", ["tkinter", "pytest", "_pytest"], binaries=_ffmpeg_binaries())
# The window needs Tk -- the dialogs, the clipboard, the font handling all come with it.
# The .ico travels inside the build: Tk otherwise paints its own feather in the title bar and the
# taskbar, no matter what icon the exe carries.
a_gui = _analysis("entry_gui.py", ["pytest", "_pytest"],
                  datas=[(str(icon), ".")] if icon.is_file() else [])

pyz_cli = PYZ(a_cli.pure)
pyz_gui = PYZ(a_gui.pure)

exe = EXE(
    pyz_cli,
    a_cli.scripts,
    [],
    exclude_binaries=True,
    name="mediarescue",
    console=True,
    upx=False,
    icon=str(icon) if icon.is_file() else None,
    version=str(version_file) if version_file.is_file() else None,
)

exe_gui = EXE(
    pyz_gui,
    a_gui.scripts,
    [],
    exclude_binaries=True,
    name="mediarescue-window",
    console=False,
    upx=False,
    icon=str(icon) if icon.is_file() else None,
    version=str(version_file) if version_file.is_file() else None,
)

coll = COLLECT(
    exe,
    exe_gui,
    a_cli.binaries,
    a_gui.binaries,
    a_cli.datas,
    a_gui.datas,
    strip=False,
    upx=False,
    name="mediarescue",
)
