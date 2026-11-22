# Windows build

Goal: one `mediarescue-<version>-setup.exe` that a non-technical person double-clicks. No Python, no
pip, no terminal, no `PATH` editing — ffmpeg and ffprobe travel inside the program.

```
entry.py            launcher: absolute import, UTF-8 streams, exits with the CLI's verdict
mediarescue.spec    PyInstaller, one folder (starts instantly, carries the two ffmpeg binaries)
version_info.txt    Explorer "Details" tab; tests/test_packaging.py fails if it drifts from __version__
mediarescue.iss     Inno Setup: per-user install, Start Menu shortcut, uninstaller
```

## Locally

```bash
# 1. get ffmpeg (a static build; any recent one works)
curl -L -o ffmpeg.zip https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
unzip -q ffmpeg.zip -d ffmpeg

# 2. build the folder
MEDIARESCUE_BUNDLE_FFMPEG="$PWD/ffmpeg/ffmpeg-*/bin/ffmpeg.exe" \
MEDIARESCUE_BUNDLE_FFPROBE="$PWD/ffmpeg/ffmpeg-*/bin/ffprobe.exe" \
  python -m PyInstaller --noconfirm --clean --workpath build-exe --distpath dist-exe packaging/mediarescue.spec

# 3. prove it works with no ffmpeg on PATH
env PATH="/c/Windows/system32" ./dist-exe/mediarescue/mediarescue.exe doctor

# 4. installer (Inno Setup 6)
"/c/Program Files (x86)/Inno Setup 6/ISCC.exe" "/DAppVersion=0.11.0" packaging/mediarescue.iss
```

CI does exactly this on `windows-latest` for every `v*` tag (`.github/workflows/build-exe.yml`) and
attaches the installer to the release.

## The two things that bit us

- **`__main__.py` cannot be the entry script.** It uses a relative import, which PyInstaller's
  top-level script context refuses (`attempted relative import with no known parent package`).
  `entry.py` exists for that, and it also has to `raise SystemExit(main())` — the exit code *is* the
  verdict.
- **The frozen interpreter defaults to the ANSI code page.** `doctor` printed `mediarescue 0.11.0
  \x97 doctor` from the exe and UTF-8 from the wheel. `entry.py` reconfigures stdout/stderr to
  UTF-8 so both print the same bytes; `tests/test_packaging.py` guards it.

A one-file (`--onefile`) build is deliberately not used: it unpacks ~230 MB to a temp folder on
every start, which is a visible delay for someone who just double-clicked an icon.
