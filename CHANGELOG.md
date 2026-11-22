# Changelog

## 0.11.0

`--jobs` reaches the other two commands that walk a folder.

- **`verify --jobs N`**, and `verify` now takes folders: after a batch repair the natural question
  is "did all of these actually come out?", and the answer was one file at a time. The report stays
  in the order you listed the files. Measured: six 90-second files **7.73s → 5.30s** at `--jobs 4`.
- **`scan --jobs N`** classifies several files at once and still prints them in folder order. It
  earns its keep with `--deep` (six 90s files **3.83s → 2.13s** at `--jobs 4`); on a header-only
  scan the work is cheap enough that it barely shows (25 files 0.27s → 0.20s, reported as measured
  rather than as a promise).
- `verify <folder>` with nothing decodable in it says so and exits `2`, instead of printing an
  empty report.
- Default is still one at a time in both commands; `--jobs 0`, `-2` and `two` stay errors.
- **Windows build, for people with no Python:** `packaging/mediarescue.spec` (PyInstaller, one
  folder) plus `packaging/mediarescue.iss` (Inno Setup) produce a single per-user
  `mediarescue-<version>-setup.exe` with ffmpeg and ffprobe inside it, a Start Menu entry and an
  uninstaller. CI builds it on `windows-latest` for every `v*` tag and attaches it to the release.
  The exe finds its own ffmpeg (`ffmpeg._bundled`), so nothing has to be on `PATH`; `MEDIARESCUE_FFMPEG`
  still overrides, and a source checkout behaves exactly as before.
- `packaging/entry.py` launches the frozen build: absolute import (PyInstaller's script context has
  no parent package), UTF-8 stdout/stderr (the frozen interpreter otherwise printed the em dash as
  `0x97` where the wheel printed UTF-8), and the CLI's return code is the process exit code.
- `tests/test_packaging.py` fails if `packaging/version_info.txt` drifts from `__version__`, if the
  installer stops taking its version from the build, or if the launcher forgets the exit code or UTF-8.
- `pytest -q` is 898 passed and `ruff check .` is clean.

## 0.10.0

A folder is no longer one file at a time.

- **`--jobs N` / `-j N`** repairs N files concurrently. The per-file table is still printed in folder
  order — the pool hands results back in submission order — so a batch log compares line-for-line
  with a one-at-a-time run. Default stays `1`. Measured on an 8-core box: 8 damaged 30s MKVs
  **9.0s → 2.6s** at `--jobs 6`; 4 files of 90s went 7.0s → 6.4s, because ffmpeg already saturates
  the cores from a single file. `--jobs` is ignored (with a note on stderr) when `--find-source` is
  on, since the search keeps shared state.
- **`--jobs` rejects `0`, `-2` and `two`** with exit `2` instead of doing something surprising.
- **A batch creates the `--out-dir` it was given.** One file already did; a folder did not, so
  `fix <folder> --out-dir <new folder>` failed every file with a message about a staging file the
  caller never named. Both now land in the same place.
- `pytest -q` is 895 passed and `ruff check .` is clean.

## 0.9.3

`fix` prints JSON by default, and that JSON was not JSON.

- **The `diagnosis:` line goes to stderr.** `mediarescue fix clip.mkv` promises a verdict object on
  stdout, but a prose line was printed in front of it, so `mediarescue fix clip.mkv | jq .ok` died on
  line 1 of otherwise valid JSON. Prose — diagnosis, progress, `working: …` — now goes to stderr;
  stdout carries the verdict and nothing else, which is also why `--brief` output is now exactly one
  line per file. Nothing changes on a terminal, which merges both streams.
- **A file that needs no repair answers instead of going quiet.** `fix` on a `healthy` file printed
  a sentence to stdout and no JSON at all, so a script saw exit `0` and an empty pipe. It now prints
  the same shape as every other run — `"strategy": "none"`, `"output": ""`, a `reason` — and
  `--brief` writes `none: nothing to repair — this file already reads cleanly`.
- `scan --json` was already clean; verified rather than assumed.
- `pytest -q` is 888 passed and `ruff check .` is clean.

## 0.9.2

What happens after a repair has been proven wrong. 0.9.1 stopped calling such a file `ok`; it was
still left on disk, under the name `fix` would have used for a successful repair.

- **A failed repair no longer leaves its output behind.** The file that failed its probe or decode
  gate is deleted, `discarded` names it, `output` comes back empty, and `--brief` ends with
  `· removed damaged.repaired.mkv`. This matters because `scan` deliberately skips names the tool
  owns (`*.repaired.*`, `*.stage.*`) — a leftover that does not play would sit next to the original
  looking like a repair that worked, and the next scan would walk past it. Only the path the
  strategy itself reported writing is touched, never the damaged file.
- **`-o` may not name the file being repaired.** `fix broken.mkv -o broken.mkv` used to hand the
  source to a strategy as its own output, which is the one way this tool could destroy the copy the
  user is trying to save. It now exits `2` with `refusing: -o names the file being repaired`, and
  the original is byte-identical afterwards.
- `pytest -q` is 886 passed and `ruff check .` is clean.

## 0.9.1

Two places where the tool said more than it had measured. Both surfaced by auditing 0.9.0 against
a head-cut fixture that looks like a real recording — 30 seconds with half-second clusters —
instead of a 6-second clip holding two.

- **`fix` no longer reports `"ok": true` for an output that does not decode.** A WebM whose
  surviving cluster carried no keyframe was transplanted into a file `ffmpeg` could not decode a
  single frame of, and the JSON said `ok: true` while the same object said
  `"verify": {"ok": false, "decode_errors": 7}`. The exit code was already 1, but `ok` is what a
  script reads. The probe and decode gates now decide `ok` itself, with a `reason` naming the
  failure, so `--brief` prints `salvage-mkv: failed — the output does not decode cleanly (7 error
  line(s))` instead of naming a file that does not play.
- **`scan` counts a reference-needing file once.** `mkv-headless` is recoverable *and* needs a
  reference, so one damaged file printed as `1 damaged, 1 repairable, 1 need a reference file` —
  three numbers that read as three files. When such a class is present the summary is a breakdown
  (`1 damaged: 0 repairable now, 1 need a reference file`); the wording for scans with no
  reference-needing file is unchanged, so existing screenshots stay accurate.
- The Matroska paths themselves hold up on that fixture: `.mkv` (H.264+AAC), `.webm` (VP9+Opus)
  and `.mka` (audio only) each recover every intact second — 19.4 to 19.5 s of the 19.5 s that
  survived — with 0 decode errors and the damaged file's checksum unchanged.
- `pytest -q` is 883 passed and `ruff check .` is clean.

## 0.9.0

Matroska files that lost their head used to be a dead end: `ffmpeg` refuses them outright, the
scan called them healthy as soon as the bytes were readable, and no flag could help. They are now
a named damage class with a repair that keeps every intact frame.

- **New damage class: `mkv-headless`.** An MKV, MKA or WebM whose EBML header was cut away —
  interrupted copy, a resume at the wrong offset, a camera that died mid-write — still has intact
  clusters after the cut, but nothing left that says which track is video, which is audio, or what
  codec either uses. `scan` now finds the first intact cluster, reports its byte offset, and names
  the file `mkv-headless` instead of `healthy`. `.webm` is scanned at all for the first time.
- **New strategy: `salvage-mkv`.** It transplants the missing head: the reference's EBML header,
  `Info` and `Tracks` are copied verbatim, a `Segment` of unknown size is written, the damaged
  file's clusters are appended from the first intact one, every cluster `Timestamp` is rebased so
  playback starts at zero, and the stale `Cues` are dropped. The result is remuxed so `ffmpeg`
  writes a fresh index. Nothing is re-encoded and the damaged file is never written to.
- **It refuses rather than guesses.** No `Info`/`Tracks` in the reference, no cluster left in the
  damaged file, or a track number in the damaged clusters that the reference does not define —
  each ends the attempt with a reason. With no `--reference`, a healthy Matroska next to the file
  is tried (same folder, sub-folders, one level up), best name match first, and every candidate is
  decoded in full before its output is kept; `--find-source` falls back to the web.
- **New command: `mediarescue verify`.** Decodes one or more files in full and says whether every
  frame came out: exit `0` clean, `1` decode errors, `2` missing file, `3` no ffmpeg. `--seconds`
  limits the check, `--json` makes it scriptable. It is the same gate `fix` applies to its own
  output, now available for files another tool repaired.
- **The menu gained a row**, `7  Check a file really decodes`, and the damage-class list includes
  `mkv-headless`. The frame still fits 80 columns.
- **`scan`'s `next:` hint now keeps `--deep`.** A scan that needed a decode to see the damage used
  to suggest a plain `fix`, which re-diagnoses each file and would have called those files
  healthy — the one line that is supposed to save you from guessing sent you the wrong way.
- **The EBML work is a real parser, not a byte hunt.** `ebml.py` reads IDs, sizes (including the
  unknown-size form), children and unsigned integers; `mkv.py` uses it to decide health, rank
  references and write the transplant.
- `pytest -q` is 879 passed, `ruff check .` is clean, and the rule set in `pyproject.toml` is
  unchanged.

## 0.8.0

Small things that made the tool feel unfinished, plus two rules that were quietly not kept.

- **The menu's arrow keys work.** `cmd_menu` called `run_menu()` without a key reader, so the
  highlighted row, `↑/↓` and `Enter` never appeared on a real terminal: the reader existed and was
  tested, but nothing handed it over. The bare `mediarescue` command now opens the live menu.
- **`--out-dir` is honoured for a single file.** `mediarescue fix clip.avi --out-dir out` used to
  ignore the folder and write `clip.repaired.avi` beside the source; `-o` names one file, and
  `--out-dir` now means the same thing whether one file or fifty go through it.
- **A malformed base64 blob can no longer crash the web fallback.** The Bing parser caught
  `binascii.Error` without ever importing `binascii`, so the handler itself raised `NameError`
  inside the one code path it was meant to protect.
- **`python -m mediarescue` runs the tool**, `--version` prints `mediarescue 0.8.0` instead of a bare
  number, and `--help` ends with the two commands a beginner needs first.
- **The menu fits an 80-column terminal, and its rows line up.** The frame was as wide as its
  longest hint — 111 columns — so a stock terminal wrapped it; the highlighted row also pushed its
  own text two columns right, which made the one row you look at the ragged one. Both are now
  covered by tests that measure the frame's width and the hint column.
- A second download of the same name prints `another candidate:` instead of a second
  `original found:`, and the unused imports the linter kept finding are gone.
- **`ruff check .` passes, and CI now runs it.** The rule set lives in `pyproject.toml` next to a
  pinned ruff, so an upgrade cannot turn `main` red without a commit that says so: unused imports,
  a duplicated `binascii` handler, `not x in y`, dead `try/except` around `ignore_errors=True`, and
  regex flags spelled `re.I` are all gone. Lint runs first in CI, without ffmpeg, because a style
  regression should not cost six pytest runs to surface.
- **The Linux ffmpeg install retries instead of trusting one download.** A dropped apt connection
  used to fail a job for reasons that had nothing to do with this project.

## 0.7.2

- **A downloaded candidate that grafts into noise no longer counts as the answer.** 0.7.1 tried the
  next candidate when the graft *failed*; a graft that produced a file full of decode errors still
  ended the search, because "it made a file" was mistaken for "it made a playable file". Every
  candidate is now decoded to the end before it is accepted or dropped.
- **One broken link on a page no longer kills the search.** Pages carry junk hrefs such as
  `//[www.terabox.com/...` that `urlsplit` refuses outright; the whole run used to die with
  `ValueError: Invalid IPv6 URL`. Such a link is now skipped.
- **The README shows a placeholder shell prompt** (`alex@laptop`, `~/Videos`) instead of paths from
  the machine the screenshot was taken on, and the disclosure says so.

## 0.7.1

Two gaps that made a repair give up while a usable reference was still within reach.

- **A twin beside the file is used even when the damaged file cannot name its codec.** Picking a
  reference used to require sniffing the video codec out of the damaged bytes; when the index is
  gone *and* the payload has no anchored H.264 stream, that failed and the tool went straight to
  the internet while an identical copy sat in the same folder. Now a healthy neighbour with the
  same name and a near-identical size is tried, and an almost identical size alone is the last
  resort — the repair is verified by a full decode either way.
- **A downloaded candidate that does not decode costs the next candidate, not the repair.** The
  size sieve decides what to *try*, never what to trust. Before this, the first plausible download
  that grafted into a file with thousands of decode errors ended the search — with an honest
  "not usable", but with no attempt at candidate two, which was often the right one.
- **The program never borrows its own output as a reference.** A `.repaired.mp4` left beside the
  damaged file is this tool's work, so it is skipped when looking for a twin.

## 0.7.0

A damaged file usually has an intact twin somewhere: the same upload on a file host, on the site it
was downloaded from, or on a mirror. `mediarescue` can now go and find it.

- **New command:** `mediarescue source "damaged.mp4"` searches the web for the original of that
  file, downloads the best match, repairs with it, and verifies the result with a full decode.
- **New flag:** `fix --find-source` does the same for a whole folder — files that need a reference
  get one looked up, files that do not are untouched.
- **How the title is chosen:** the filename is stripped of download-site noise (`[1080p]`,
  `@channel`, `- SITENAME`, `_4K`) and the rest becomes the query.
- **Search backends, in order:** the Tavily HTTP API when `TAVILY_API_KEY` is set, the `tvly` CLI
  when it is installed and signed in, then DuckDuckGo (HTML and lite), then Bing. Results that are
  search-engine furniture (stylesheets, ad redirects, the engine's own pages) are dropped.
- **Cheap rejection before download:** every candidate's size is checked with a HEAD request and
  must fall within ±25–35% of the damaged file's size. This is what stops a 41 MB ten-minute copy
  from being grafted onto a 30 second recording — the decode check would notice, but only after
  paying for the download and producing a file full of noise.
- **Candidate ranking:** URLs whose filename contains the title (compared with spaces and
  punctuation squashed) win; `github.com/.../blob/...` links are rewritten to
  `raw.githubusercontent.com` because the former serves a page, not the video.
- Downloads go to a temporary folder and are **deleted when the run ends** — nothing is kept
  except the repaired file. `--max-tries` (default 8) caps how many downloads a search may cost.
- Menu choice `6`: "Find a file's original online and repair it".

Verified against the real internet: a 30 second Big Buck Bunny clip with its index removed was
repaired by a downloaded twin — `graft → .repaired.mp4  30.04s  h264+aac  decodes cleanly`.

Honest limit: a search finds *files with the same name*, not *the same encoding*. A re-uploaded or
re-encoded copy cannot serve as a reference (different encoder settings mean its index does not
describe your payload), and the verification step is what catches that.

## 0.6.0

The intact twin of a damaged download almost never sits on the same disk — it sits in a free cloud
drive. `--reference` now accepts a **share link** as well as a path.

- **New:** links are resolved, downloaded to a temporary folder, used for the repair, and deleted
  when the run ends. Menu choices 3 and 4 ask for "a file or link".
- **New:** supported hosts — TeraBox (including `1024terabox`, `terabox.app`, `terasharelink` and
  the other mirror domains), MediaFire, pixeldrain, GoFile, Google Drive, Dropbox, OneDrive, and
  any direct `http(s)` URL. A TeraBox folder share uses its largest video.
- **Refused on purpose:** Mega (links are decrypted in the browser) and any link that answers with
  a web page instead of a file — an HTML page is never saved as a "reference".
- Downloads are capped at 4 GB, and `MEDIARESCUE_TERABOX_COOKIE` lets a TeraBox share that wants a
  signed-in session work without reimplementing their login.

## 0.5.3

Found by running a 20-file batch on a real folder of damaged downloads: 10 red `FAILED` lines
that were all the same, solvable situation.

- **Fixed:** a batch now separates "needs a reference file" from real failures. Those files were
  not unrepairable — their encoder stored the SPS/PPS in the index, which is gone, so they need
  one intact file from the same source. They now print as yellow `need-ref`, the summary says
  `10 need a reference file` instead of `10 failed`, and a closing block explains what a reference
  is (same phone, app or download site — not the same video) with a copy-pasteable command.
- **Fixed:** `scan` and batch `fix` now skip this tool's own output files (`*.repaired.*`,
  `*.stage.*`), and a batch never re-processes files that live inside its `--out-dir`.
- Menu choice `4` now asks for one intact file (blank = none) and passes it as `--reference`.
- **Fixed:** "needs a reference" is only claimed when the file still has a `mdat` to graft onto. A
  file whose boxes do not parse at offset 0 (bad-block damage) cannot be helped by any reference,
  so it stays a red `FAILED` with its own reason instead of sending the user hunting for a file
  that would not have worked.
- **Fixed:** `salvage` leaked its multi-hundred-megabyte `.stage.mp4` working copy into the output
  folder whenever the run failed — a real batch on this developer's drive left a 93 MB one, which
  the next scan then classified as a damaged file. Every exit path now cleans it up.

## 0.5.2

Found by pressing `1` in the menu on a machine where Git Bash had opened in `C:\Program Files\Git`:
the tool declared itself "not ready" when nothing was wrong.

- **Fixed:** `doctor` treated a read-only working folder as a failure. Repairs are written beside
  each source file, so the folder you happen to be standing in decides nothing; it is now reported
  as `ok` with `C:\Program Files\Git is read-only (normal); repairs are written beside your files`.
  A failure is still raised when the folder *you named* with `--workdir` cannot be written, or when
  the machine has nowhere at all to write.
- **Fixed:** a `--workdir` that does not exist now says `no such folder: ...` instead of implying a
  permission problem, and the disk-space check walks up to the nearest existing parent rather than
  failing as well.
- Exit code follows the same rule: ready means a repair could run right now.

## 0.5.1

Found by pointing 0.5.0 at a real damaged folder and piping it into `head`, the way anyone reading
a long listing would.

- **Fixed:** `mediarescue scan big-folder | head` (or `| less`, or Ctrl+C in a pager) buried the
  report under `OSError: [Errno 22] Invalid argument`. Windows reports a closed pipe as EINVAL, not
  `BrokenPipeError`, and the write escaped as a traceback. Output now goes quiet and the command
  exits 0.
- **Fixed:** one filename the console cannot encode could abort the whole scan mid-table. NTFS
  stores raw UTF-16 units, so a damaged directory entry can hold a lone surrogate; such a name is
  now shown with `�` and the report still finishes. JSON output stays parseable too.
- Both cases are handled in one place (`ui.ForgivingStream`), so `scan`, `fix`, `doctor` and the
  menu all benefit, and a legacy code page degrades to backslash escapes instead of crashing.

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
