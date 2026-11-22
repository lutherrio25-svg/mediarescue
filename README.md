# mediarescue

[![container](https://img.shields.io/badge/ghcr.io-mediarescue-2496ed?logo=docker&logoColor=white)](https://github.com/lutherrio25-svg/mediarescue/pkgs/container/mediarescue)
[![release](https://img.shields.io/github/v/release/lutherrio25-svg/mediarescue?label=release)](https://github.com/lutherrio25-svg/mediarescue/releases)
[![tests](https://github.com/lutherrio25-svg/mediarescue/actions/workflows/tests.yml/badge.svg)](https://github.com/lutherrio25-svg/mediarescue/actions/workflows/tests.yml)
[![license](https://img.shields.io/github/license/lutherrio25-svg/mediarescue)](LICENSE)

Diagnose and repair damaged audio/video files from the command line. It tells you what is
wrong with a file, repairs it into a **new** file, and reports exactly what it had to give up.

**Originals are never modified.** Nothing is overwritten, nothing is deleted.

Built for the failures that actually show up on a real disk: interrupted copies, zero-filled
heads, fragmented MP4s that lost their init segment, downloads that never finished. When the index
is gone and no intact copy exists anywhere, it can search the web for the original upload and
repair with that.

![mediarescue in a terminal: scan a folder, then repair an AVI index and a cut-off AVI](docs/terminal.png?v=0.10.0)

The screenshot is a real session: `scan` on a folder holding one healthy MP4, one healthy MP3, one
AVI whose `idx1` points at the wrong chunks, and one AVI whose index was cut off. Every line below a
prompt is the CLI's own output, unedited; the window frame is drawn for the README and the prompt is
a placeholder (`PS C:\media>` is not a real machine).

## Install

```bash
pip install "git+https://github.com/lutherrio25-svg/mediarescue.git"
```

Requires Python 3.10+ and `ffmpeg` + `ffprobe` on your `PATH`:

| Platform | Command |
|---|---|
| Windows | `winget install --id Gyan.FFmpeg -e` |
| macOS | `brew install ffmpeg` |
| Debian/Ubuntu | `sudo apt install ffmpeg` |

`winget` only updates `PATH` for **new** shells, so close the terminal and open a fresh one.
Confirm with `ffmpeg -version` and `mediarescue --version`. If ffmpeg lives somewhere unusual,
point at it with `MEDIARESCUE_FFMPEG` / `MEDIARESCUE_FFPROBE`.

`pip: command not found` means Python itself is missing (on Windows the `python` in `WindowsApps`
is only a Microsoft Store stub). Install it first, then reopen the terminal:

| Platform | Command |
|---|---|
| Windows | `winget install -e --id Python.Python.3.12` |
| macOS | `brew install python` |
| Debian/Ubuntu | `sudo apt install python3 python3-pip python3-venv` |

A virtual environment keeps the tool out of the system Python, and puts a `mediarescue` command in
its `Scripts` (Windows) or `bin` (macOS/Linux) folder:

```bash
python -m venv ~/mediarescue-cli
~/mediarescue-cli/Scripts/pip install "git+https://github.com/lutherrio25-svg/mediarescue.git"   # Windows
~/mediarescue-cli/bin/pip    install "git+https://github.com/lutherrio25-svg/mediarescue.git"   # macOS/Linux
```

No Python on the machine? The published container already has ffmpeg inside:

```bash
docker run --rm -v "D:/broken:/media" ghcr.io/lutherrio25-svg/mediarescue:latest scan /media
```

Optional extra: put [`mp3val`](https://mp3val.sourceforge.net) on `PATH` (or set
`MEDIARESCUE_MP3VAL`) and `fix` will rebuild damaged MP3 frame streams instead of only
rewriting the container. Without it the tool still runs and says the damage was left alone.

Or just ask the tool itself:

```bash
mediarescue doctor
```

```
mediarescue 0.10.0 — doctor

  ok    python        3.14.9
  ok    ffmpeg        ffmpeg version N-127043-g5a54fcf75e-20260930
  ok    ffprobe       ffprobe version N-127043-g5a54fcf75e-20260930
  opt   mp3val        not installed — damaged MP3 frame streams can be rebuilt
                      -> download mp3val, or set MEDIARESCUE_MP3VAL to its path
  ok    write access  C:\Users\you\Documents\mediarescue
  ok    disk space    49.8 GB free

ready: everything this tool needs is present
```

`opt` marks an optional helper: the tool is ready without it, it just repairs fewer formats.
Anything *required* that is missing is printed as `FAIL` together with the command that fixes
it, and the exit code is 1.

The `write access` line checks the folder you are standing in, and a read-only one is not a
problem — Git Bash opened from the Start menu sits in `C:\Program Files\Git`, and the tool still
writes every repaired copy beside the original. Pass `--workdir D:/fixed` only when you want the
check to test a specific folder.

## All-in-one menu

No flags to remember: run `mediarescue` with no arguments in a terminal and you get a menu.

![mediarescue all-in-one menu: a highlighted row, plain-language hints, and arrow-key controls](docs/menu.png?v=0.10.0)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ mediarescue 0.10.0                                                           │
│ repair damaged video and audio files; originals are never touched            │
└──────────────────────────────────────────────────────────────────────────────┘

     1  Check this machine can repair files    ffmpeg, ffprobe, disk space  
     2  Look at a file or folder               what's wrong, no changes     
  ▸  3  Repair one file                        leaves the original alone        
     4  Repair every damaged file in a folder  skips the healthy ones       
     5  Show the damage classes                one line per class           
     6  Find a file's original online          searches the web first       
     7  Check a file really decodes            every frame, not just headers
     0  Quit                                   close the menu               

  ↑/↓ move  ·  Enter choose  ·  0 quits
```

Use the arrow keys and press Enter, or just type the number and press Enter — whichever you
prefer. Every row says what it does in plain words, so you never have to guess what "scan" or
"fix" means. Each choice asks for the path it needs and then runs the same command you would have
typed: the menu calls the CLI, it does not re-implement it, so the two can never disagree.

`mediarescue menu` opens it explicitly. From a script (stdin or stdout not a terminal) a bare
`mediarescue` prints the help and exits 2 instead of waiting for input, so nothing ever hangs.
On a legacy console code page the frame falls back to `+ - |` characters instead of crashing.

## Quick start

Three commands, in order.

**1. Find out what is broken.** Point it at a file or a whole folder:

```bash
mediarescue scan "D:/broken-file"
```

```
severity                bytes  fix  file
corrupt-index         313,248  yes  D:\broken-file\clip.avi
healthy                61,678  yes  D:\broken-file\holiday.mp4
corrupt-index         266,260  yes  D:\broken-file\webcam.avi

3 scanned, 2 damaged, 2 repairable, 0 need a reference file
next: mediarescue fix "D:\broken-file" --out-dir "D:\broken-file\recovered"
```

Read it left to right: what is wrong, how big, whether it can be rebuilt (`fix` = yes/no, coloured
green or red in a terminal), and which file. The last line tells you the exact command to run next,
with your own path already filled in. Add `--out report.json` to keep the full details.

![mediarescue scan output: a header row, one coloured line per file, and the next command to run](docs/scan.png?v=0.10.0)

`C:\media` in that screenshot is a placeholder folder, not a real machine; the paths are otherwise
exactly what the tool printed.

That reads headers only — for Matroska it also confirms the EBML header, `Info` and `Tracks`
parse. Add `--deep` to decode every file as well — slower, but it is the only way to catch damage
inside MP3/AVI, or a junk payload hiding behind a perfect index or behind intact Matroska headers:

```bash
mediarescue scan "D:/broken-file" --deep
mediarescue scan "D:/broken-file" --deep --deep-seconds 30   # quick pass, opening 30s each
```

**2. Repair the damaged file.** Leave the strategy on `auto` — it is chosen from the scan
result:
```bash
mediarescue fix "D:/broken-file/clip.mp4" -o "D:/fixed/clip.mp4"
```

You get one diagnosis line, then JSON saying what it did:

```
diagnosis: missing-trailer — mdat declares 26,383,400 bytes but file is 18,530,556;
           moov sits inside that dead range at 18,407,240 — trim mdat to expose it
{
  "ok": true,
  "output": "D:/fixed/clip.mp4",
  "notes": [
    "mdat header trimmed: 7,976,192 declared bytes were never written to disk",
    "dropped the first 14.000s: 339 unreadable samples before the first clean IDR at sample #350"
  ],
  "dropped_seconds": 14.0,
  "strategy": "salvage"
}
```

Read `notes` and `dropped_seconds` — that is the honest part. If it reports 14 seconds dropped,
your repaired video starts 14 seconds in, because that head was unreadable garbage.

The JSON also carries `verify`: the repaired file is decoded and `decode_errors` must be `0`. A
repair that writes a file but leaves broken payload exits with `1`, because a playable container
full of silence is not a fix. Add `--deep` to decode the *input* first (same check as
`scan --deep`), or `--no-verify` to skip the check on the output.

`-o` names the exact output file. `--out-dir` does the same thing for one file as for a folder: it
collects the repaired file into that folder under `<name>.repaired.<ext>`, so a batch of one and a
batch of fifty land in the same place.

**Repairing a whole folder** works the same way — point `fix` at it. Damaged files are repaired,
healthy ones are left untouched, and the exit code is `1` if anything failed:

```bash
mediarescue fix "D:/broken-file" --out-dir "D:/fixed"
```

```
  fixed   big_mdat.mp4                     salvage
  ok      holiday.mp4                      healthy, left untouched
  skip    empty.mp4                        empty: nothing to rebuild from
  FAILED  fragment.mp4                     the stream carries no SPS/PPS (NAL 7/8) …

4 files: 1 repaired, 1 failed, 2 skipped
```

**More than one at a time.** `--jobs N` (`-j N`) repairs N files at once, and the table still comes
out in folder order whatever order they finish in:

```bash
mediarescue fix "D:/broken-file" --out-dir "D:/fixed" --jobs 4
```

Measured here, not promised: eight damaged 30-second MKVs went from **9.0s to 2.6s** with
`--jobs 6` on an 8-core machine. Four files of 90 seconds barely moved (7.0s → 6.4s), because
ffmpeg already feeds every core from one file — so the honest advice is 2–4 for a folder of
ordinary files, 1 on a machine you are still using, and more only when each file is small. `--jobs`
steps aside for `--find-source`, which keeps one search cache and one "candidate in use" slot.

**3. Prove the repair.** `ffprobe` reads headers, so a file can look perfect and still be noise
inside. A full decode is the real test — **silence means it worked**:

```bash
mediarescue verify "D:/fixed/clip.mp4"
```

```
clean       12.08s  clip.mp4  every frame decoded
```

`verify` runs the same decode-and-count check `fix` applies to its own output, so you can put a
file repaired by anything — this tool, another program, a friend's laptop — through the same gate.
Exit code `0` means every frame decoded, `1` means decode errors (it prints how many and the first
one), `2` a missing file, `3` no ffmpeg. `--seconds 30` checks only the opening half-minute,
`--json` gives the same result as data. The raw command behind it is:

```bash
ffmpeg -v error -i "D:/fixed/clip.mp4" -f null -
```

Nothing printed means the frames really decode. Then open the file and look at it.

## Which command do I need?

| What you see | Run this |
|---|---|
| Not sure what is wrong | `mediarescue scan <file-or-folder>` |
| File will not open or play at all | `mediarescue fix broken.mp4 -o fixed.mp4` |
| Plays, but freezes or shows coloured blocks | `mediarescue fix broken.mp4 -o fixed.mp4` |
| Starts mid-scene, first seconds are garbage | `mediarescue fix broken.mp4 -o fixed.mp4` (`auto` picks `salvage`) |
| Video plays, audio clicks or drops out | run the decode check first; audio payload that is gone cannot be invented |
| File is 0 bytes | nothing to recover — re-download or restore a backup |
| MKV/WebM will not open at all, `scan` says `mkv-headless` | `mediarescue fix broken.mkv --reference intact.mkv -o fixed.mkv` |
| Everything already looks fine | `mediarescue fix good.mp4 -o copy.mp4` rewrites the container cleanly |
| Damaged file, and you have no intact copy anywhere | `mediarescue source bad.mp4` searches the web for the original and repairs with it |

## The one thing that trips everyone up: `--reference`

Three damage classes — `fragmented`, `missing-atoms` and `mkv-headless` — mean the file lost the
index or header that says *where each frame starts* and *what each track is*. That cannot be
guessed back, so the tool needs a second, undamaged file to borrow the layout from:

```bash
mediarescue fix dash-segment.mp4 --reference intact.mp4 -o fixed.mp4
```

What counts as a good reference:

- an **intact file from the same app, camera or recorder** — same settings, ideally the same
  session
- the **video codec must match**. A fragmented AV1 file can borrow structure from an H.264
  file, but a source that is itself H.264 needs an H.264 reference, or it is refused instead
  of producing garbage
- it does not have to be the same video — anything that opens and plays normally will do

No reference anywhere? The tool looks for one itself: a healthy file **in the same folder** whose
video codec matches is picked automatically, and the choice is reported in the output
(`"reference": "…"`). Only when that fails does it fall back to `--strategy carve`.

## A Matroska file whose header is gone

An MKV, MKA or WebM starts with an EBML header, then `Info` (timescale, title) and `Tracks` (one
entry per stream: codec, resolution, sample rate). Cut off the front of the file — an interrupted
copy, a resume that landed at the wrong offset, a camera that lost power — and every cluster after
the damage is still intact, but nothing says which track number carries video and which carries
audio. `ffmpeg` will not even open the file: *EBML header parsing failed*.

`scan` names it `mkv-headless`, with the byte offset of the first intact cluster:

```
severity                bytes  fix  file
mkv-headless          590,643  yes  D:\broken\lecture.mkv
healthy               908,681  yes  D:\broken\lecture-full.mkv

2 scanned, 1 damaged: 0 repairable now, 1 need a reference file
next: mediarescue fix "D:\broken" --out-dir "D:\broken\recovered"
those need --reference: an intact file recorded the same way, see --help
```

A reference is required, and this is the one case where that is not a limitation of the tool: the
track table existed in the header and nowhere else, so there is nothing in the damaged bytes to
rebuild it from. What the repair does instead is a transplant:

- copy the reference's EBML header, `Info` and `Tracks` verbatim;
- write a new `Segment` of unknown size and append the damaged file's clusters, starting at the
  first intact one;
- rewrite each cluster's `Timestamp` so playback starts at zero;
- drop the old `Cues` (they described the clusters that are gone) and remux, so `ffmpeg` writes a
  fresh index.

Nothing is re-encoded, and the damaged file is only ever read:

```bash
mediarescue fix "D:/broken/lecture.mkv" --reference "D:/broken/lecture-full.mkv" -o "D:/fixed/lecture.mkv"
```

```
working: salvage-mkv → lecture.repaired.mkv
diagnosis: mkv-headless — the EBML header is gone, but an intact Cluster starts at byte 42,752:
           the clusters after it carry everything except the track table, which any healthy file
           of the same source can supply
{
  "ok": true,
  "output": "D:/fixed/lecture.mkv",
  "notes": [
    "rebuilt Info and Tracks for 2 track(s) from the reference: the first 7.941s were cut away with the original header",
    "the old Cues were dropped with the header; the remux writes a fresh index",
    "reference picked automatically: lecture-full.mkv"
  ],
  "dropped_seconds": 7.941,
  "clusters": 6,
  "reference": "D:/broken/lecture-full.mkv",
  "verify": { "ok": true, "decode_errors": 0 },
  "strategy": "salvage-mkv",
  "source_untouched": true
}
```

`ok` in that object is the verdict, not "the strategy ran": an output file that `ffmpeg` cannot
open or cannot decode comes back `false` with a `reason` naming the failure, and `fix` exits `1`.

When that happens the file is not left behind. It is named in `discarded`, `output` is empty, and
`--brief` ends with `· removed damaged.repaired.mkv` — a `.repaired.` file that does not play is
worse than none, because `scan` skips this tool's own output names and would walk straight past it.
The damaged file itself is never touched, and `-o` refuses to name it, since writing a repair over
the only copy of the original is the one way this could lose your file.

stdout carries that verdict and nothing else: the `diagnosis:` line, the progress and `working:`
lines all go to stderr, so `mediarescue fix clip.mkv --no-color | jq .ok` reads a real answer. A
file that needs no repair answers in the same shape instead of printing an empty pipe —
`"strategy": "none"` with a `reason`, which `--brief` writes as `none: nothing to repair …`.

Leave out `--reference` and it looks for a healthy Matroska beside the damaged file (same folder,
sub-folders, one level up), ranked by shared name words then by size, and it will try each
candidate in turn — a reference can match the name and the size and still be a different encode,
so every attempt is decoded in full before it is accepted. `--find-source` goes online for the
original when nothing local fits.

What it cannot give back: the seconds before the first intact cluster (`dropped_seconds`), the
original `Cues`, and tags, chapters and attachments that lived in the header. Those bytes are
gone, so the output simply does not have them rather than pretending to.

## When the intact file is online instead of on your disk

The twin of a damaged download often still lives in a free cloud drive. `--reference` accepts a
**share link** as well as a path, downloads it to a temporary folder, uses it, and deletes it when
the run ends:

```bash
mediarescue fix "D:/broken-file" --out-dir "D:/fixed" \
  --reference "https://terabox.com/s/1AbCdEfGhIjKl"
```

Supported links:

| Host | What to paste |
|---|---|
| **TeraBox** (and its mirror domains) | the normal share link, `.../s/<id>` or `.../sharing/<id>` |
| MediaFire, pixeldrain, GoFile | the share link as it is |
| Google Drive | `Share → copy link`; the file can stay private-if-link |
| Dropbox / OneDrive | the share link; `?dl=0`/`?download=0` is rewritten to a download |
| anything else | a direct `http(s)` link to the file itself |

Notes that save a support round-trip:

- **Mega is refused** — its links are decrypted inside the browser, so download it yourself and
  pass the local file instead.
- TeraBox shares that are folders are fine: the **largest video** in the share is used, since a
  reference says nothing about *which* video it is, only how it was encoded.
- If TeraBox asks for a logged-in session, copy that page's `Cookie` header into the environment
  variable `MEDIARESCUE_TERABOX_COOKIE` and run again. Cookies expire; refresh when it complains.
- A share link that returns a sign-in page instead of a file is reported as exactly that, never
  saved as a fake reference.
- Downloads are capped at 4 GB: anything larger is not a reference, it is a mistake.

## When you have no twin at all: let it search

Most damaged downloads have an intact twin somewhere on the internet — the same upload on a file
host or on the site it came from. Instead of hunting for it yourself:

```bash
mediarescue source "D:/broken-file/clip.mp4"
```

![mediarescue source: the file is diagnosed, the original is found online, the repair decodes cleanly](docs/source.png?v=0.10.0)

That screenshot is the output of a real run (a 30-second Big Buck Bunny clip whose `moov`
box was cut off); the window frame around it is drawn for the README, and the shell prompt — user
name, machine name and folder — is blanked. The lines the program prints are untouched.

The command reads the filename, strips the download-site noise (`[1080p]`, `@channel`, `- SOMESITE`),
searches the web for the original, downloads the best match into a temporary folder, repairs with
it, verifies with a full decode, and deletes the download. The repaired file lands next to the
original with `.repaired` in the name.

```text
diagnosis: missing-atoms — no reachable moov anywhere: sample table lost
working: graft → clip.repaired.mp4
looking online for the original of clip.mp4
  original found: candidate.mp4 (9.4 MB) — matches size/shape of the damaged file
graft → clip.repaired.mp4  30.04s  h264+aac  decodes cleanly  source untouched
```

For a whole folder, add the flag to the normal batch command. Only the files that need a reference
trigger a search:

```bash
mediarescue fix "D:/broken-file" --out-dir "D:/fixed" --find-source
```

What it will and will not do:

- **It will not guess.** A candidate's size is checked before it is downloaded; a 41 MB copy of a
  ten-minute video is never grafted onto a 30 second clip. If nothing matches, you get
  "every result was a different file" instead of a file full of noise.
- **The same encoding matters, not the same video.** A re-uploaded or re-encoded copy has different
  encoder settings, so its index does not describe your payload. The decode check rejects it — try
  the site you originally downloaded from.
- **Nothing is kept but the repair.** Search results are not saved, downloads are deleted.
- Search backends, in order: the Tavily API when `TAVILY_API_KEY` is set, the `tvly` command when
  it is installed and signed in, DuckDuckGo, then Bing. No key is required.
- `--max-tries N` caps how many downloads one search may cost (default 8); `--verbose` shows every
  candidate and why it was skipped.

## When it cannot be fixed

This is a repair tool, not a magic wand:

- overwritten or zero-filled data is **gone**. No tool can invent frames that were never
  written; re-download or restore from backup
- a dropped head stays dropped — `salvage` reports the seconds instead of faking them
- audio packets lost in the middle cannot be reconstructed; the file plays with gaps
- it works on existing files only. Carving files off a raw disk is a different job (PhotoRec)

## Reference

### Damage classes

| Severity | Meaning | Strategy | Needs reference |
|---|---|---|---|
| `healthy` | boxes parse, index reachable | `remux` (optional cleanup) | no |
| `corrupt-payload` | index is perfect but the first video sample does not parse (head overwritten) | `salvage` | no |
| `missing-trailer` | `mdat` runs past EOF, hiding a valid `moov`; truncated PNG/JPEG | `salvage` (trim, then cut a junk head) | no |
| `fragmented` | starts at `moof`/`mdat`, init segment lost | `flatten` (or `carve` without a reference) | yes |
| `missing-atoms` | `ftyp`+`mdat` present, `moov` gone | `graft` (or `carve` without a reference) | yes |
| `corrupt-index` | AVI: `idx1` missing, or its entries point past the chunk they name | `remux` with `+ignidx` | no |
| `mkv-headless` | Matroska/WebM: EBML header cut, `Info`+`Tracks` gone, clusters intact | `salvage-mkv` (transplant the head) | yes |
| `headerless` | no parsable box or cluster at offset 0 | not repairable locally | — |
| `empty` | 0 bytes | not repairable | — |

### Strategies

- **remux** — `ffmpeg -c copy` container rewrite with corrupt-packet tolerance.
- **trim** — shrink an oversized `mdat` header to the real payload so the trailing `moov`
  becomes reachable again, then remux. No re-encode.
- **graft** — keep the damaged `mdat`, graft the reference `moov`, rewrite every
  `stco`/`co64` chunk offset by the position delta. Refuses on overflow or when the reference
  has no usable sample table.
- **flatten** — parse `tfhd`/`trun` from every fragment, rebuild `stts`/`ctts`/`stsc`/`stsz`/
  `stco`/`stss`, and emit a normal non-fragmented MP4. The codec is sniffed from the sample
  bytes: **AV1** gets an `av01`/`av1C` entry built from the stream's own sequence header (no
  reference codec is borrowed); **H.264** reuses the reference's `avcC`, and a reference with a
  different video codec is refused instead of producing garbage. Audio timescale is inferred
  from sample durations. Samples past EOF from a truncated final fragment are dropped and
  counted, not fatal.
- **salvage** — for a file whose *head* was overwritten (a resume that landed at the wrong
  offset, a copy that mixed two files). Trims an oversized `mdat`, then walks the sample table
  to find the first video sample that really starts a picture — an H.264 IDR, or an AV1
  `KEY_FRAME` — and stream-copies from that timestamp. The cut is reported, so you know exactly
  how much was lost and how much is bit-identical. Nothing is re-encoded, and the corrupt head is
  dropped rather than concealed — a frozen frame over dead data helps nobody.
- **salvage-mkv** — for a Matroska whose EBML header, `Info` and `Tracks` were cut away. Copies
  the reference's header, `Info` and `Tracks`, writes a `Segment` of unknown size, appends the
  damaged file's clusters from the first intact one, rebases every cluster timestamp to zero and
  drops the stale `Cues`, then remuxes so a fresh index is written. No re-encode; it refuses when
  the reference has no `Info`/`Tracks`, when the damaged file has no cluster left, or when the
  damaged clusters use a track number the reference does not define.
- **carve** — the last resort when the index is gone *and* there is no reference file: walk the
  raw `mdat` for length-prefixed NAL units, rebuild an Annex-B stream and let ffmpeg write a
  fresh index. It needs two things the file may not have: **SPS/PPS in-band** (encoders usually
  keep them in the lost `avcC`, so streams made with `repeat-headers=1` are the ones that work)
  and **contiguous video samples** (samples interleaved one-per-chunk cannot be delimited without
  the index). It recovers **video only**, and the frame rate is assumed — pass `--fps` to correct
  it. When either condition fails it says so instead of writing a file that looks fine.

Force one explicitly with
`--strategy trim|remux|graft|flatten|salvage|salvage-mkv|carve`. If audio and video durations still
disagree after a `flatten`, give every track one clock with `--timescale 90000`.

```bash
# No index and no reference file: rebuild what the payload still holds
mediarescue fix damaged.mp4 -o rebuilt.mp4 --strategy carve --fps 30
```

### Honest limits

- A reference must match the recording's codec configuration; a wrong one yields a file that
  probes fine but decodes wrong. Always eyeball the result.
- `ffprobe` reads headers, not pictures. Prove a repair with the full decode from step 3 and
  require **zero** output lines.
- `corrupt-payload` is decided by parsing the first video sample: H.264 must tile as
  length-prefixed NALs, AV1 must frame as OBUs. Anything else counts as junk.
- Damage inside **MP3, WAV, FLAC, OGG, AAC, GIF and WEBP** is not inspected by the header scan:
  those containers are reported as `healthy` as soon as they are readable, so a file with a
  mangled middle still scans clean. AVI gets its index checked, Matroska is checked one level
  deeper — the EBML header, `Info` and `Tracks` must parse — but neither payload is walked.
  `scan --deep` catches all of it by decoding, and `fix --deep` applies the same check to the
  input.
- `mkv-headless` needs a reference that describes the same tracks: the transplant copies the
  reference's codec configuration, so a 1080p reference for a 720p recording produces a file that
  opens and plays at the wrong geometry instead of failing. The automatic pick is tried and
  decoded in full before it is accepted; a hand-picked `--reference` is trusted, so match the
  source.
- AV1 is cut at its own KEY_FRAME now, but a damaged AV1 head is only recoverable when a later
  keyframe exists **and** the sequence header travels in-band; otherwise nothing can decode it.
- `carve` works only when SPS/PPS travel in-band and video samples are contiguous; it recovers
  video without audio and assumes the frame rate. Everything else it refuses, with the reason.
- Automatic reference selection looks at the **same folder, its sub-folders and one level up**
  (capped at 40 candidates). Point `--reference` explicitly when the intact file lives further
  away.

### Sources

Format specs and prior art this tool is built on:

- **ISO Base Media File Format** (ISO/IEC 14496-12) and **CMAF** (ISO/IEC 23000-19) — box layout, `moof`/`traf`/`trun` fragments. Paid standards, no free link.
- [AOMedia AV1 Bitstream & Decoding Process Specification](https://aomediacodec.github.io/av1-spec/) — OBU framing (section 5.5), sequence-header parsing and the `av1C` fields `flatten` writes.
- [ITU-T H.264 (free copy)](https://www.itu.int/rec/T-REC-H.264) — NAL unit types, IDR frames, SPS/PPS, and the length-prefixed sample layout `carve` walks.
- **ISO/IEC 14496-3** — AAC `AudioSpecificConfig` carried inside `esds`. Paid standard, no free link.
- **ISO/IEC 11172-3** — MPEG-1 Audio Layer III frame header and the Xing/Info tag that `mp3val` rebuilds. Paid standard, no free link.
- [Apple QuickTime File Format](https://developer.apple.com/documentation/quicktime-file-format) — free reference for `moov`, `stbl`, `stco`/`co64`, `stsz`, `stts`, `edts`.
- [MP4 Registration Authority](https://mp4ra.org/) — registered box, codec and `ftyp` brand identifiers.
- [FFmpeg formats docs](https://ffmpeg.org/ffmpeg-formats.html), [`libavformat/mov.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/mov.c) and [`libavformat/avidec.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/avidec.c) — how a real demuxer reads these boxes and how it resolves `idx1` offsets (the two bases `avi.check` tries), and why it rejects the files here.
- [Matroska / EBML specification](https://www.matroska.org/technical/specs/index.html) — what `scan --deep` can and cannot conclude about an MKV.
- [RFC 8216 (HLS)](https://datatracker.ietf.org/doc/html/rfc8216) — why fragmented MP4 segments arrive without `ftyp`/`moov`, the `fragmented` class.
- [PNG specification (W3C)](https://www.w3.org/TR/png/) — signature and `IEND` check.
- [JPEG, ITU-T T.81 (W3C copy)](https://www.w3.org/Graphics/JPEG/itu-t81.pdf) — SOI/EOI markers.
- [Microsoft AVI RIFF File Reference](https://learn.microsoft.com/en-us/windows/win32/directshow/avi-riff-file-reference) — `LIST movi` chunk chain, the `idx1` entry layout and what its offsets are relative to; the basis of `avi.check`.
- [mp3val](https://mp3val.sourceforge.net/) — the MPEG frame/Xing-header validator that `fix` drives for damaged MP3s.
- [MKVToolNix](https://mkvtoolnix.download/) — `mkvmerge`'s remux behaviour, the reference for what container rewriting can and cannot repair.
- [DivFix++](https://www.divfix.org/) — the long-standing AVI index rebuilder; its approach is what `corrupt-index` now does internally.
- [untrunc](https://github.com/anthwlock/untrunc) (fork of [ponchio/untrunc](https://github.com/ponchio/untrunc)) — the established reference-based MP4 repair tool, and the build that was measured against `flatten`; it handles more codecs than the original.
- [PhotoRec](https://www.cgsecurity.org/wiki/PhotoRec) — for carving files from a raw disk, which this tool does not do.

### Development

```bash
pip install -e .[dev]
ruff check .                        # the rule set is pinned in pyproject.toml
python -m pytest                    # fixtures are generated with ffmpeg; those tests skip without it
python scripts/acceptance_check.py  # drives an installed CLI end to end, exit codes included
python scripts/fuzz_check.py        # damages 7 file types 10 ways each; any traceback or false "fixed" fails
```

See [CHANGELOG.md](CHANGELOG.md) for what changed between versions, and
[docs/related-tools.md](docs/related-tools.md) for the other repair tools that were measured
against this one — including the gaps they cover better.

MIT licensed.