# Changelog

## 0.5.0

Everything here came from running the tool on real damaged files, one command at a time, and
reading the output as a beginner would.

**Menu**
- Arrow-key navigation: the selected row is a solid highlighted bar, with `↑/↓ move · Enter choose`
  printed underneath. Typing a number still works, so both habits are fine.
- Every row now carries a plain-language hint (`writes a new file, original untouched`), and the
  rows are worded as actions rather than tool names.
- The menu is framed with a title and one-line explanation of what the program does.
- **Fixed:** the highlight was a reverse span wrapped around coloured spans, and the first inner
  reset cancelled it — on a real terminal the selected row was never actually highlighted. It is
  now built from uncoloured text and wrapped once.
- **Fixed:** on a legacy console code page the box-drawing characters raised `UnicodeEncodeError`
  and killed the menu. The frame now falls back to `+ - |`.

**Scan**
- A header row (`severity / bytes / fix / file`) so the table explains itself; severity is
  coloured by damage class and the `fix` column is green or red.
- A `next:` line with the exact command to run against what was just scanned.
- A folder with nothing to scan says so in plain words instead of printing an empty table.

**Repair**
- A file that scans healthy is no longer rewritten: it prints `nothing to repair: this file
  already reads cleanly (use --force to rewrite it anyway)` and exits 0. Copying a healthy
  multi-gigabyte file byte for byte took minutes and achieved nothing.
- `--force` keeps the deliberate case, described as *repair files that scan calls healthy instead
  of skipping them*.

## 0.4.0

**All-in-one menu**
- `mediarescue` with no arguments in a terminal opens a numbered menu: doctor, scan, repair one
  file, repair a whole folder, or read the damage-class table. `mediarescue menu` opens it too.
- The menu calls the real CLI rather than re-implementing it, so an action cannot drift from the
  command it stands for.
- From a script (stdin or stdout not a terminal) a bare `mediarescue` prints help and exits 2, so
  nothing ever blocks waiting for input.

**Fixed on the way**
- `main()` ignored `sys.argv` when called with no explicit argument list, so the new no-argument
  shortcut would have swallowed every real command: `mediarescue scan x` opened the menu instead
  of scanning. Caught by running the installed wheel, not by unit tests, which always pass argv
  explicitly. There is now a test that drives `main()` through `sys.argv`.

## 0.3.2

Found by running the *published* package on the real damaged files rather than on fixtures — the
thing 765 green tests did not catch.

**Fixed**
- An AV1 file with no index and no `--reference` used to fail with *"the stream carries no SPS/PPS
  (NAL 7/8)"*. AV1 has no SPS/PPS at all: `carve` understands only H.264 and was being handed AV1
  bytes. The codec is now checked first, and the message says what is actually missing: the audio
  configuration lived in the lost index, so a reference file from the same encoder is needed — and
  it does not have to be AV1.
- `fix --brief` truncated a failure from the end, cutting off the sentence that names the cause.
  A stated reason is now shown in full; only a raw ffmpeg log is trimmed to its last line.

**Verified on real files** (installed from GitHub, outputs checked with ffmpeg, not by this tool)
- `torntomcat` (18.5 MB, `missing-trailer`) → `salvage`, 126.20 s, h264+aac, 0 errors.
- `excomunicado` (199 MB, `fragmented`, AV1) + `--reference` → 2167.75 s, av1+aac, 0 errors.
- `BIG Trouble` (306 MB, `fragmented`, AV1) + `--reference` → 2197.33 s, av1+aac, 0 errors.
- Three healthy real-world files on the same machine stayed `healthy`; all three sources kept their
  original size and mtime.

## 0.3.1

Found by fuzzing — damaging seven file types ten ways each and feeding random bytes to every
parser (8,820 parser calls) — rather than by waiting for a user to hit it.

**Fixed**
- A file whose `stsz` claimed 4 billion samples made `scan` and `fix` crash with a traceback
  (`struct.error`), or would have tried to allocate a multi-gigabyte list. Every count in a sample
  table (`stsz`, `stsc`, `stco`, `stts`, and `trun` in fragments, `stco`/`co64` in `graft`) is now
  checked against the bytes its box really holds. Such a file is reported as unreadable instead of
  crashing the run. A truncated or half-written index is exactly what this tool is pointed at.
- `sniff_video_codec` raised `TypeError` when handed one sample instead of a list.

**Added**
- `doctor` lists `mp3val` as an *optional* helper (`opt`): reported, never fatal.
- `scripts/fuzz_check.py`, run in CI against the installed wheel: any traceback, any exit code
  outside 0–3, or any `fix` that exits 0 with a file that does not decode, fails the build.
- 700+ parameterised robustness tests; one of them was verified to fail with the guard removed.
- The container image now ships `mp3val`, so MP3 repair works inside Docker.

**Packaging**
- Licence declared as SPDX (`License-Expression: MIT`) and `LICENSE` is shipped in the wheel.
  Project URLs (homepage, source, issues, changelog) added to the metadata.

## 0.3.0

Two gaps closed, and both were found by measuring rather than by reading homepages.

**AVI can now be told apart from an AVI that merely plays**
- New damage class `corrupt-index`: `idx1` is missing, or its entries do not point at the chunk
  they name. `scan` used to call these files `healthy` — AVI needs no central index for playback,
  because libavformat rescans `movi`, so nothing looked wrong. Measured on a fixture with every
  entry shifted 64 bytes: 382 of 382 caught.
- `fix` repairs it with `-fflags +ignidx`, letting the demuxer rescan and write a fresh index,
  then verifies the result by decode *and* by re-checking every entry against its chunk. A decode
  check alone cannot prove an AVI index is sound: sequential decoding never consults it.

**Damaged MP3s are repaired, not just detected**
- `mp3val` is used when installed (`MEDIARESCUE_MP3VAL` overrides the lookup): the file is copied,
  `mp3val -f -nb` rebuilds the frame stream, and the copy is decode-verified. Measured on an MP3
  with 40% of its middle overwritten: 2 decode errors before, 0 after. Without the binary the run
  says the frames were left alone instead of implying success.

**Smaller**
- `salvage` now decides the codec per track by majority across the file. Deciding per sample let
  junk bytes pose as an AV1 keyframe and block the cut on a damaged H.264 file.
- Automatic reference search reaches sub-folders and one level up (40 candidates max).
- `fix --brief`: one line per file instead of the JSON report.
- The container image is tagged with the release version as well as `latest`.
- `docs/related-tools.md`: what `untrunc`, `mp3val`, `DivFix++`, `mkvmerge` and PhotoRec were
  measured to do here — including where they beat this tool and where they lose to it.

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
