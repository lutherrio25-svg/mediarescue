# Changelog

## 0.2.0

Everything below was added while repairing real damaged files; each item is covered by a test.

**Diagnosis**
- New damage class `corrupt-payload`: a perfect index over junk pictures. Detected by parsing the
  first video sample (H.264 must tile as length-prefixed NALs, AV1 must frame as OBUs), which is
  what a header-only check — and `ffprobe` — cannot see.
- `scan --deep` / `--deep-seconds`: decode files instead of trusting headers. This is the only
  check that works outside ISO-BMFF, so damage inside MP3/MKV/AVI is finally visible.
- Files whose payload is never inspected now say so in their notes instead of looking clean.

**Repair**
- `salvage`: cut an overwritten head away at the first clean keyframe — an H.264 IDR or an AV1
  `KEY_FRAME` — and report the seconds lost, instead of concealing dead data with silence and
  frozen frames.
- `carve`: rebuild video from a bare H.264 payload when the index is gone *and* no reference
  exists. Refuses — with the reason — when SPS/PPS lived only in the lost `avcC`, or when
  samples are interleaved one-per-chunk.
- Automatic reference selection: a healthy sibling with the same video codec is picked by codec,
  not by name, and the chosen path is reported.
- `flatten`: `trun.data_offset` now resolves against the traf base (multi-`trun` fragments were
  decoded from wrong offsets); AV1 gets an `av01`/`av1C` entry built from the stream's own
  sequence header; `ftyp` brands are rewritten to `av01` so players stop choosing the H.264
  decoder; a reference with a different video codec is refused; audio timescale is inferred.
- Leading `Lavc…` encoder-tag blobs parked as audio sample 0 are dropped instead of decoded.
- `fix` decodes its own output and exits `1` when the payload is still broken — a playable
  container full of silence is not a repair.
- MP3 damage is repaired with `mp3val` when it is installed (optional backend, `MEDIARESCUE_MP3VAL`
  overrides the lookup); without it the run reports that the frames were left alone.
- `corrupt-index` (AVI): `idx1` missing, or its entries point at the wrong chunk. `scan` used to
  call these files `healthy` — AVI needs no central index for playback, so nothing looked wrong.
  `fix` now remuxes with `-fflags +ignidx`, letting the demuxer rescan `movi` and write a fresh
  index; verified by decode, and by re-checking every entry against its chunk.
- `docs/related-tools.md` records what `untrunc`, `mp3val`, `DivFix++`, `mkvmerge` and PhotoRec
  were measured to do against this tool's gaps.

**Interface**
- `fix` accepts folders and several paths: batch mode repairs damaged files, leaves healthy ones
  untouched, writes into `--out-dir`, and exits `1` if anything failed.
- `doctor`: one command that checks Python, ffmpeg, ffprobe, write access and free space, and
  prints the fix for anything missing.
- `fix --deep` / `--no-verify`, `--fps` for `carve`, `--force` for batch.
- Colour on a terminal, plain text when piped (`NO_COLOR` / `FORCE_COLOR` / `--no-color`),
  `--quiet` and `--verbose` for batch, and a per-file `[n/total]` counter.
- README rewritten as a three-step quick start with real output, a symptom-to-command table and a
  plain-language `--reference` section.

**CI**
- ffmpeg is installed with a static-build fallback and a check that fails when it is missing:
  a suite that skips every ffmpeg test is not a green run.

## 0.1.0

- `scan` and `fix` with `trim`, `remux` and `graft`.
- Damage classes `healthy`, `missing-trailer`, `missing-atoms`, `headerless`, `empty`,
  `unsupported`; PNG/JPEG truncation checks; JSON reports.
