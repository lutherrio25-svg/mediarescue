# SignPath Foundation application — copy-paste answers

Everything below is filled in with this repository's real values. The application form is short and
in English; paste the value from the right column, adjust nothing except the two fields only you can
answer (`Your name`, `Email`, `GitHub account`).

Application page: <https://signpath.org/> → **Apply for Free Code Signing**.

## The form, field by field

| Form field | What to put |
|---|---|
| **Project name** | `mediarescue` |
| **Repository URL** | `https://github.com/lutherrio25-svg/mediarescue` |
| **Download page** | `https://github.com/lutherrio25-svg/mediarescue/releases/latest` |
| **License** | `MIT` (OSI-approved; `LICENSE` at the repository root) |
| **Primary language** | `Python` |
| **Website / homepage** | leave empty (the repository is the homepage) |
| **Your name** | your own name |
| **Email** | the address you want SignPath to reply to |
| **GitHub account** | the account that owns the repository (`lutherrio25-svg`) |

### Short description (the one-line field)

```
mediarescue is a command-line tool that diagnoses and repairs damaged MP4, AVI, Matroska and MP3
files, and ships a Windows installer built from this repository.
```

### What does the software do? (the long field)

```
mediarescue repairs broken media files without ever writing to the original. It reads the damaged
file, decides what is actually wrong (a missing index, a truncated tail, a broken header), and
rewrites a separate repaired copy: it can remux, graft a working head from a healthy reference file,
flatten, trim, salvage, or carve frames out of a container whose index is gone. Every repair is
checked by decoding the output end to end, and a copy that fails that check is deleted instead of
being left behind looking like it worked. It also has a `verify` command for files that are already
in hand and a `doctor` command that reports what the machine is missing.

The project is MIT licensed, has no proprietary or binary-only components, and is written in Python.
The Windows artefacts (a command-line exe, a windowed exe, and an Inno Setup installer) are built
entirely from the source in this repository by the GitHub Actions workflow in
`.github/workflows/build-exe.yml` on every tag, with a static ffmpeg fetched at build time and
bundled inside the installer so end users need nothing else. The wheel, the sdist and the installer
are published on the repository's GitHub Releases page, each with a sha256 digest listed by GitHub.
```

### How are the artefacts built? (or: "build process")

```
GitHub Actions, workflow "windows exe" (.github/workflows/build-exe.yml), triggered by tags matching
v* and by manual dispatch. PyInstaller builds two executables from the checked-in spec
(packaging/mediarescue.spec) in one onedir folder, ffmpeg/ffprobe are downloaded from gyan.dev during
the build and bundled, Inno Setup (packaging/mediarescue.iss) compiles the setup.exe from that folder,
and the workflow smoke-tests the result with PATH stripped to System32 before uploading it. The same
workflow is the only source of the published release assets.
```

### Anything else we should know? (optional field)

```
The Windows build is unsigned today, which is exactly why this application exists: SmartScreen shows
"Unknown publisher" for the setup.exe on every download. The repository already carries the signing
hook (packaging/sign.ps1) wired into the build workflow, so once a certificate is available the signed
artefacts come out of the same pipeline with no code change. English-language UI and CLI output;
maintained continuously since November 2026.
```

## What actually went into the form

These two are the submitted strings, kept here so a follow-up question from the reviewers can be
answered with the same words that were sent.

**Description** (`A short paragraph describing your project and its purpose. Avoid listing
version-specific features or dependencies.`)

```
mediarescue is a command-line tool for damaged video and audio: the files a player refuses to open because the index is gone, the tail was cut off, or the header is corrupt. It reads the broken file, works out what is actually wrong, and writes a separate repaired copy -- keeping what is intact, taking a working header from a healthy reference file when one exists, and rebuilding the index from the media payload when nothing else is left. The original is never modified, and every repair is proven by decoding the entire output; a copy that fails that check is thrown away instead of being offered as a fix. It exists for the case where the only other option is a full re-encode that still loses the recording.
```

**Reputation** (`Provide links or information showing that your project is widely used or trusted.`)

```
mediarescue is a young project -- first commit 2026-11-16 -- so it has no press coverage, package adoption or download figures yet. The evidence that can be checked today is in the repository and its releases:
- 22 tagged releases, v0.2.0 to v0.13.0, each built and smoke-tested by GitHub Actions: https://github.com/lutherrio25-svg/mediarescue/releases
- the test suite (924 tests) runs green on every push: https://github.com/lutherrio25-svg/mediarescue/actions
- every release asset is published with a sha256 digest, and each release is built from the tagged source by CI, not by hand
- the public installer has been installed, used to repair a damaged file, and verified end to end on Windows 11
- the documentation states which damage classes are repairable and which are not: https://github.com/lutherrio25-svg/mediarescue#readme
```

The rule both follow: Description sells the *problem and the method*, never the feature list;
Reputation never argues, it points at things a reviewer can open in a browser and check.

## Checklist before you submit (all verified in this repository today)

- [x] Public repository — <https://github.com/lutherrio25-svg/mediarescue>
- [x] OSI-approved licence file at the root — `LICENSE`, MIT, `Copyright (c) 2026 Star Corps bks`
- [x] No proprietary, binary-only, or dual-licensed components in the build
- [x] Actively maintained — commits and tagged releases (latest `v0.13.0`, 2026-11-22)
- [x] Released, downloadable artefacts — `.whl` and `mediarescue-<version>-setup.exe` on Releases
- [x] Artefacts produced by CI from this repository — `.github/workflows/build-exe.yml`
- [x] Reproducible by a stranger — `pyproject.toml`, `packaging/`, tests under `tests/`, `docs/`

## What happens after approval

SignPath emails you, you install their **GitHub App** on this repository, then create in their web UI:

1. an **artifact configuration** for `dist-setup/*.exe` and `dist-exe/mediarescue/*.exe`,
2. a **signing policy** (slug, for example `release-signing`),

and you end up with four values: `SIGNPATH_API_TOKEN` (a secret), plus organization id, project slug
and policy slug (repository variables). Send me the three ids/slugs and put the token into the
repository secret yourself — then `docs/signpath-job.yml` gets moved into `.github/workflows/` and the
release upload switches to the signed artefacts.

Expect the publisher shown to users to read **SignPath Foundation**, not your own name: the certificate
belongs to the foundation, which vouches that the binary was built from this repository.

## If they ask why the project has no stars or users yet

Answer plainly — the programme asks for a maintained, released open-source project, not for
popularity: the repository was created on 2026-11-16, has had a tagged release for every change since
(v0.2.0 through v0.13.0, 22 tagged releases), and the build is green in CI. A young project with a working installer and a
signed release pipeline is exactly the case the foundation's free tier is meant to cover.
