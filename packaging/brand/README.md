# mediarescue icon

The mark is a **shield with the play triangle knocked out of it**: shield = a file that came back in
one piece, play triangle = it is media. Nothing else is in the picture, because at 16 px on a taskbar
everything else turns to mush.

| What | Path |
|---|---|
| Source of truth (geometry + palette) | `source/make.mjs` |
| Renderer (Chrome → PNG → `.ico`) | `source/render.mjs` |
| PNG reader used by the renderer | `source/pngread.mjs` |
| Pixel inspector for small sizes | `source/pngascii.mjs` |
| Rendered previews, contact sheet, zoom | `out/` |
| The file the build actually uses | `packaging/mediarescue.ico` |

## Palette

| Token | Hex | Where |
|---|---|---|
| ink | `#0B1220` | tile at 24 px and above; also the knocked-out triangle |
| tile (small) | `#16233A` | tile at 20 px and below, so the rounded square still separates from a dark taskbar |
| cream | `#F4EFE6` | the shield |
| amber | `#F2A03D` | brand accent — used in copy and the installer, deliberately not in the mark |
| teal | `#2FD4C4` | unused alternative accent, kept one constant away |

Two colours in the mark, separated by value rather than hue, is what survives a circular crop
(Telegram avatars) and a 16 px taskbar slot.

## Geometry (256-unit grid)

- tile: `rect 0,0 256×256`, `rx=56`
- shield: `M128 34 L206 62 V132 C206 172 172 202 128 220 C84 202 50 172 50 132 V62 Z`
- play cut-out: triangle scaled about its own centroid, growing by +10 % (≤24 px) / +18 % (≤20 px) /
  +24 % (≤16 px) so the notch stays more than two device pixels wide
- **16 px is a separate drawing**, `svg16()`, on a 16-unit grid with integer coordinates and
  `shape-rendering="crispEdges"`. Scaling the 256-unit art down puts the cut-out on half pixels and
  it reads as a smudge — measured, not assumed: the 16 px PNG has **0 partially transparent pixels**.

## Sizes in the `.ico`

`16, 20, 24, 32, 40, 48, 64, 128, 256` — 16–128 are BMP (`BITMAPINFOHEADER`) frames, 256 is PNG.

BMP for the small sizes is not cosmetic: GDI+ (`System.Drawing.Icon`, and therefore a number of
installer and shell paths) **cannot decode PNG-compressed ico entries** and throws
`Requested range extends past the end of the array`. Verify on Windows before shipping:

```powershell
Add-Type -AssemblyName System.Drawing
$p = "packaging\mediarescue.ico"
foreach ($s in 16,32,48,128) {
  $i = New-Object System.Drawing.Icon($p,$s,$s)
  "$s -> $($i.Width)x$($i.Height) $($i.ToBitmap().PixelFormat)"
}
```

## Rebuild

```bash
cd packaging/brand/source
node render.mjs                 # writes out/*.png, out/*.ico, contact sheets, zoom sheets
cp ../out/d-shield.ico ../../mediarescue.ico
python -m pytest tests/test_packaging.py -q
```

Inspect a small size pixel by pixel instead of squinting at a thumbnail:

```bash
node pngascii.mjs ../out/d-shield-16.png
```

## Where it shows up

- `mediarescue.exe` / `mediarescue-window.exe` — via `packaging/mediarescue.spec` (`icon=…`), which
  silently falls back to the Python icon when the file is missing, so the test asserts it exists.
- the installer window, `setup.exe` and Add/Remove Programs — via `SetupIconFile=mediarescue.ico`
  in `packaging/mediarescue.iss`.
- **not** in the winget manifest: that one is pinned to the bytes of a released installer. Changing
  the icon changes the SHA-256, so it ships with the next version, never as an edit to a shipped one.

## Not in the mark, on purpose

The shield reads "protected" more than "repair", and that is the accepted trade: a wrench or a
bandage at 16 px is a grey smudge. The wordmark carries the verb — *mediarescue* — and the product
name is always next to the icon in every surface we control.

## The window uses the same palette

The desktop window (`src/mediarescue/gui.py`) is built from these tokens, so the app, the installer
and the icon read as one product. Change a colour here and change it there:

| Token in `gui.py` | Value | Where it lands |
|---|---|---|
| `BG` / `MENU` | `#0B1220` | window, top bar, status bar |
| `PANEL` | `#121B2B` | the rounded cards |
| `FIELD` | `#182742` | entries, comboboxes, spinboxes |
| `BORDER` | `#243450` | card edges and hairlines |
| `FG` | `#F4EFE6` | primary type (the cream from the wordmark) |
| `ACCENT` | `#F2A03D` | the Repair button, the selected tab, the progress bar |
| `OK` | `#2FD4C4` | the status dot |

`node` is not involved: the window draws its own cards, pills and dashed drop target on a `Canvas`,
because tkinter has no rounded primitive. Screenshots of the result live in `export/` (see
`source/shot_window.ps1`, which photographs the live window by HWND).
