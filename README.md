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
mediarescue scan D:/broken-file --out report.json

# One file, automatic strategy
mediarescue fix broken.mp4 -o fixed.mp4

# Missing moov / fragmented files need an intact file from the same device or encoder
mediarescue fix dash-segment.mp4 --reference intact-from-same-camera.mp4 -o fixed.mp4

# If audio and video durations still disagree, force one clock for every track
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
- **flatten** — parse `tfhd`/`trun` from every fragment, rebuild `stts`/`ctts`/`stsc`/`stsz`/
  `stco`/`stss`, and emit a normal non-fragmented MP4. The codec is sniffed from the sample
  bytes: **AV1** gets an `av01`/`av1C` entry built from the stream's own sequence header (no
  reference codec is borrowed); **H.264** reuses the reference's `avcC`, and a reference with
  a different video codec is refused instead of producing garbage. Audio timescale is inferred
  from sample durations. Samples past EOF from a truncated final fragment are dropped and
  counted, not fatal.

## Honest limits

- A reference must match the recording's codec configuration; a wrong one yields a file
  that probes fine but decodes wrong. Always eyeball the result.
- Zero-filled or overwritten `mdat` regions are gone for good. No tool can invent frames;
  re-download or restore from backup for those.
- Audio in partially recovered files may still contain decode errors where payload was lost.
- Not a carve-from-raw-disk tool (use PhotoRec for that); this works on existing files.

## Sources

Format specs and prior art this tool is built on:

- **ISO Base Media File Format** (ISO/IEC 14496-12) and **CMAF** (ISO/IEC 23000-19) — box layout, `moof`/`traf`/`trun` fragments. Paid standards, no free link.
- [Apple QuickTime File Format](https://developer.apple.com/documentation/quicktime-file-format) — free reference for `moov`, `stbl`, `stco`/`co64`, `stsz`, `stts`, `edts`.
- [MP4 Registration Authority](https://mp4ra.org/) — registered box and codec identifiers.
- [FFmpeg formats docs](https://ffmpeg.org/ffmpeg-formats.html) and [`libavformat/mov.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/mov.c) — how a real demuxer reads these boxes (and why it rejects the files here).
- [PNG specification (W3C)](https://www.w3.org/TR/png/) — signature and `IEND` check.
- [JPEG, ITU-T T.81 (W3C copy)](https://www.w3.org/Graphics/JPEG/itu-t81.pdf) — SOI/EOI markers.
- [untrunc](https://github.com/ponchio/untrunc) — the established reference-based MP4 repair tool; `graft` follows the same idea.
- [PhotoRec](https://www.cgsecurity.org/wiki/PhotoRec) — for carving files from a raw disk, which this tool does not do.

## Development

```bash
pip install -e .[dev]
python -m pytest        # fixtures are generated with ffmpeg; those tests skip without it
```

MIT licensed.
