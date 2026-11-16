# mediarescue

Diagnose and repair damaged audio/video files. **Originals are never modified** — every
repair writes a new file and then validates it with `ffprobe`.

Built for the failures that actually show up on a real disk: interrupted copies,
zero-filled heads, fragmented MP4s that lost their init segment, 0-byte downloads.

## Install

```bash
pip install -e .          # Python 3.10+, no runtime dependencies
```

`ffmpeg` + `ffprobe` must be on `PATH` for repair and validation
(override with `MEDIARESCUE_FFMPEG` / `MEDIARESCUE_FFPROBE`).

## Usage

```bash
# Classify everything under a directory (system/cache dirs are skipped)
mediarescue scan D:/file-rusak --out report.json

# One file, automatic strategy
mediarescue fix broken.mp4 -o fixed.mp4

# Missing moov / fragmented files need an intact file from the same device or encoder
mediarescue fix dash-segment.mp4 --reference intact-from-same-camera.mp4 -o fixed.mp4

# If the reconstructed audio/video durations disagree, the source used a single clock
mediarescue fix dash-segment.mp4 --reference intact.mp4 --timescale 90000 -o fixed.mp4

# Force a strategy
mediarescue fix broken.mp4 --strategy trim|remux|graft|flatten
```

## Damage classes

| Severity | Meaning | Strategy | Needs reference |
|---|---|---|---|
| `healthy` | boxes parse, index reachable | `remux` (optional cleanup) | no |
| `missing-trailer` | `mdat` runs past EOF, hiding a valid `moov`; truncated PNG/JPEG | `trim` | no |
| `fragmented` | starts at `moof`/`mdat`, init segment lost | `flatten` | yes |
| `missing-atoms` | `ftyp`+`mdat` present, `moov` gone | `graft` | yes |
| `headerless` | no parsable box, large zero regions | not repairable locally | — |
| `empty` | 0 bytes | not repairable | — |

## Strategies

- **remux** — `ffmpeg -c copy` container rewrite with corrupt-packet tolerance.
- **trim** — shrink an oversized `mdat` header to the real payload so the trailing
  `moov` becomes reachable again, then remux. No re-encode.
- **graft** — keep the damaged `mdat`, graft the reference `moov`, rewrite every
  `stco`/`co64` chunk offset by the position delta. Refuses on overflow or when the
  reference has no usable sample table.
- **flatten** — parse `tfhd`/`trun` from every fragment, rebuild `stts`/`stsc`/`stsz`/
  `stco`/`stss`, and emit a normal non-fragmented MP4 using the reference's codec
  description (`stsd`/`avcC`/`esds`). Samples past EOF from a truncated final fragment
  are dropped and counted, not fatal.

## Honest limits

- A reference must match the recording's codec configuration; a wrong one yields a file
  that probes fine but decodes wrong. Always eyeball the result.
- Zero-filled or overwritten `mdat` regions are gone for good. No tool can invent frames;
  re-download or restore from backup for those.
- Audio in partially recovered files may still contain decode errors where payload was lost.
- Not a carve-from-raw-disk tool (use PhotoRec for that); this works on existing files.

## Development

```bash
pip install -e .[dev]
python -m pytest        # fixtures are generated with ffmpeg; those tests skip without it
```

MIT licensed.
