# Windows build

Goal: one `mediarescue-<version>-setup.exe` that a non-technical person double-clicks. No Python, no
pip, no terminal, no `PATH` editing — ffmpeg and ffprobe travel inside the program.

```
entry.py            console launcher: absolute import, UTF-8 streams, exits with the CLI's verdict
entry_gui.py        windowed launcher: same, but imports mediarescue.gui and has no console at all
mediarescue.spec    PyInstaller, one folder, two exes sharing _internal/ (ffmpeg travels inside)
version_info.txt    Explorer "Details" tab; tests/test_packaging.py fails if it drifts from __version__
mediarescue.iss     Inno Setup: per-user install, Start Menu shortcuts, uninstaller
```

Two exes, one folder: `mediarescue.exe` keeps the console (scripts, `doctor`, CI smoke test) and
`mediarescue-window.exe` is the same program with no console — the Start Menu entry points at it,
because a program opened from Explorer must not flash a terminal at someone who has never used one.
"Console or not" is a property of the exe, so the window costs a launcher, not a second ffmpeg.

## Locally

```bash
# 1. get the same shared LGPL build CI uses, minus ffplay
curl -L -o ff-shared.zip \
  https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl-shared.zip
unzip -q ff-shared.zip -d ff-shared
mv ff-shared/ffmpeg-master-latest-win64-lgpl-shared/bin ff-shared/bin
rm -f ff-shared/bin/ffplay.exe

# 2. build the folder (dist/mediarescue/, onedir — there is no dist/*.exe)
MEDIARESCUE_BUNDLE_FFMPEG_DIR="$PWD/ff-shared/bin" \
  python -m PyInstaller packaging/mediarescue.spec --noconfirm --clean

# 3. prove it works with no ffmpeg on PATH
env PATH="/c/Windows/system32" ./dist/mediarescue/mediarescue.exe doctor

# 4. prove the window opens (this one needs a desktop; CI only checks that the file exists)
./dist/mediarescue/mediarescue-window.exe

# 5. installer (Inno Setup 6; it is per-user on a normal install)
mkdir -p dist-exe && cp -a dist/mediarescue dist-exe/
"$LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe" /DAppVersion=0.14.0 packaging/mediarescue.iss
# -> dist-setup/mediarescue-0.14.0-setup.exe
```

Without `MEDIARESCUE_BUNDLE_FFMPEG_DIR` the spec falls back to `ffmpeg`/`ffprobe` from `PATH`, and the
folder grows to ~351 MB because both static binaries carry the same codecs. That fallback exists so a
source checkout can still build; the release must use the shared directory.

CI does exactly this on `windows-latest` for every `v*` tag (`.github/workflows/build-exe.yml`) and
attaches the installer to the release.

## The two things that bit us

- **`__main__.py` cannot be the entry script.** It uses a relative import, which PyInstaller's
  top-level script context refuses (`attempted relative import with no known parent package`).
  `entry.py` exists for that, and it also has to `raise SystemExit(main())` — the exit code *is* the
  verdict.
- **The frozen interpreter defaults to the ANSI code page.** `doctor` printed `mediarescue 0.12.0
  \x97 doctor` from the exe and UTF-8 from the wheel. `entry.py` reconfigures stdout/stderr to
  UTF-8 so both print the same bytes; `tests/test_packaging.py` guards it.

A one-file (`--onefile`) build is deliberately not used: it unpacks the ~190 MB folder to a temp
location on every start, which is a visible delay for someone who just double-clicked an icon.

## The third thing that bit us

- **An unsigned exe is a warning, not a bug.** SmartScreen says "Unknown publisher" for every
  download that carries no trusted signature, and no amount of packaging changes that. `sign.ps1`
  signs the two exes and the installer (timestamped, then verified), and `docs/SIGNING.md` records
  why the free route was declined and what a paid certificate actually buys.

## Winget

`packaging/winget/manifests/s/StarCorpsBks/MediaRescue/<version>/` holds the three manifests that make
`winget install mediarescue` possible. Bump all three `PackageVersion` fields and the `InstallerUrl`
together with a release; `tests/test_packaging.py` fails if any of them, the digest, or the `Scope`
drifts from `packaging/mediarescue.iss`.

Two things cost real time to find:

- **The default locale manifest must say `ManifestType: defaultLocale`.** Label it `locale` and `winget
  validate` answers *"the multi file manifest is incomplete"* — an error that names no file at all, no
  matter which of the three is wrong.
- **`winget install --manifest` is disabled by default.** It needs
  `winget settings --enable LocalManifestFiles` from an elevated shell; without that it prints the help
  text and exits, which reads like a syntax error rather than a policy.

`winget validate --manifest <dir>` is the check that matters; a clean run prints
`Manifest validation succeeded.` and nothing else.

- **A layout bug fails exactly like a crash.** `ttk.Label(...).grid(row=8, ..., **pad)` passed
  `pady` twice, and Tk raised `duplicate keyword` at widget-build time: the window never appeared,
  and a screenshot script happily captured some *other* window and reported success. `tests/test_gui.py::test_the_window_can_actually_be_built`
  now constructs the real window and calls `update()` — skipped when there is no display, which is
  why it has to run locally before a tag.
