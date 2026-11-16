# Related tools, and what they actually do for the gaps here

`mediarescue` covers ISO-BMFF damage and H.264/AV1 payloads. Some damage classes are better
served by other tools, so this page records what was **measured on this machine** rather than
what the homepages claim. Every number below comes from

```bash
ffmpeg -v error -i <file> -f null -    # count the lines; zero means the pictures decode
```

on a fixture with a known, injected defect.

## Reference-based MP4 rebuild — `untrunc` (anthwlock fork)

Rebuilds `moov` from a working file of the same recording. Closest cousin to `graft`/`flatten`.

```bash
untrunc working.mp4 broken.mp4      # writes broken.mp4_fixed.mp4
```

**Measured** on a fragmented fixture (`ftyp`+`moov` cut off, reference available):

| | duration | decode errors |
|---|---|---|
| `untrunc` | 4.040 s | **3** (`missing picture in access unit`, `no frame!`) |
| `mediarescue flatten` | 4.097 s | **0** |

Verdict: for the damage classes this tool understands, `flatten` is cleaner. Reach for `untrunc`
when `mediarescue` *refuses* — HEVC/GoPro/Sony XAVC recordings, or files where SPS/PPS only ever
existed in the lost index. Download: <https://github.com/anthwlock/untrunc/releases>.

## MP3 structure repair — `mp3val`

Validates MPEG frame headers, rebuilds the Xing/VBR header and drops unreadable frames.

```bash
mp3val -f -nb damaged.mp3          # rewrites in place; keep a copy
```

**Measured** on an MP3 whose middle 40% was overwritten:

```
before:  2 decode error lines
         WARNING: Wrong number of MPEG frames specified in Xing header (116 instead of 71)
after:   0 decode error lines, file rebuilt
```

Verdict: this is the one tool that closes a gap `mediarescue` openly has — damage inside MP3 is
detected by `scan --deep` but not repaired. Integration is a small, well-defined job: run
`mp3val` on a copy, then decode-verify the result. Not wired in yet.

## AVI index rebuild — `DivFix++`

Rebuilds a broken AVI index (`idx1`) and strips dead sections. Windows-only, last release 2010.

Verdict: **not verified here** — <https://www.divfix.org> serves no direct binary link any more,
and the mirrors were unavailable. Treat it as a manual fallback for AVI files that will not seek;
`mediarescue` will at least tell you the file is damaged (`scan --deep`).

## MKV — `mkvmerge` (MKVToolNix)

`mkvmerge -o fixed.mkv damaged.mkv` re-muxes the file, which rebuilds cues and the segment index.
It cannot repair damaged payload, only the container bookkeeping.

Verdict: **not measured** — the portable build is a 40 MB 7z and nothing on this machine can
extract it. Worth trying for MKV files that play but refuse to seek.

## Raw-disk carving — PhotoRec

Recovers whole files from a device without a filesystem, which `mediarescue` deliberately does
not do. Use it when the file no longer exists, not when it is damaged.

## ffmpeg flags already used internally

- `-fflags +discardcorrupt` — drop packets the demuxer can see are broken
- `-err_detect ignore_err` — keep going instead of aborting on the first bad packet
- `-f h264` + `-c copy` — the rebuild path `carve` uses

## Summary

| Gap | Best tool | Status |
|---|---|---|
| MP4 index lost, reference exists | `flatten` / `graft` here, `untrunc` for exotic codecs | covered, cross-checked |
| MP3 damage | `mp3val` | **gap — integration pending** |
| AVI index | `DivFix++` | unverified, manual fallback |
| MKV bookkeeping | `mkvmerge` | unmeasured, manual fallback |
| AV1 head damage | `salvage` here (needs a later keyframe) | covered |
| File deleted entirely | PhotoRec | out of scope |
