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
# 1. get ffmpeg (a static build; any recent one works)
curl -L -o ffmpeg.zip https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
unzip -q ffmpeg.zip -d ffmpeg

# 2. build the folder
MEDIARESCUE_BUNDLE_FFMPEG="$PWD/ffmpeg/ffmpeg-*/bin/ffmpeg.exe" \
MEDIARESCUE_BUNDLE_FFPROBE="$PWD/ffmpeg/ffmpeg-*/bin/ffprobe.exe" \
  python -m PyInstaller --noconfirm --clean --workpath build-exe --distpath dist-exe packaging/mediarescue.spec

# 3. prove it works with no ffmpeg on PATH
env PATH="/c/Windows/system32" ./dist-exe/mediarescue/mediarescue.exe doctor

# 4. prove the window opens (this one needs a desktop; CI only checks that the file exists)
./dist-exe/mediarescue/mediarescue-window.exe

# 5. installer (Inno Setup 6)
"/c/Program Files (x86)/Inno Setup 6/ISCC.exe" "/DAppVersion=0.13.0" packaging/mediarescue.iss
```

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

A one-file (`--onefile`) build is deliberately not used: it unpacks ~230 MB to a temp folder on
every start, which is a visible delay for someone who just double-clicked an icon.

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
