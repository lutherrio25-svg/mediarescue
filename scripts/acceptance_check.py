"""Acceptance run against an INSTALLED mediarescue, not the source tree.

The unit tests exercise functions; this exercises the shipped command: argument parsing, exit
codes, colour behaviour, batch mode, and whether repaired files actually decode. It catches
packaging mistakes that a green `pytest` cannot see.

    python scripts/acceptance_check.py                 # uses `mediarescue` from PATH
    python scripts/acceptance_check.py --cli path/to/mediarescue.exe
    python scripts/acceptance_check.py --keep          # keep the fixtures for inspection
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

results: list[tuple[str, bool, str]] = []


def run(cli: str, *args: str, env: dict | None = None, timeout: int = 600):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run([cli, *args], capture_output=True, text=True, env=e, timeout=timeout)


def check(name: str, fn) -> None:
    try:
        results.append((name, True, fn() or ""))
    except AssertionError as exc:
        results.append((name, False, str(exc)[:150]))
    except Exception as exc:  # noqa: BLE001 - any crash is a failed check, not a crashed run
        results.append((name, False, f"{type(exc).__name__}: {exc}"[:150]))


def decode_errors(path: Path) -> int:
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
                       capture_output=True, text=True)
    return len([ln for ln in p.stderr.splitlines() if not ln.startswith("[null @")])


def make_clip(path: Path, seconds=4, audio=True, repeat=True) -> None:
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    cmd += ["-t", str(seconds), "-c:v", "libx264", "-g", "25", "-pix_fmt", "yuv420p"]
    if repeat:
        cmd += ["-x264-params", "repeat-headers=1"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "64k"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True)


def corrupt_head(path: Path, fraction=0.35, seed=5) -> None:
    data = bytearray(path.read_bytes())
    i = data.find(b"mdat") + 4
    span = int((len(data) - i) * fraction)
    rng = random.Random(seed)
    data[i:i + span] = bytes(rng.randrange(256) for _ in range(span))
    path.write_bytes(bytes(data))


def lie_mdat(path: Path, extra=4 << 20) -> None:
    data = bytearray(path.read_bytes())
    i = data.find(b"mdat")
    struct.pack_into(">I", data, i - 4, struct.unpack_from(">I", data, i - 4)[0] + extra)
    path.write_bytes(bytes(data))


def fragment(src: Path, dst: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c", "copy",
                    "-movflags", "frag_keyframe+empty_moov+default_base_moof", str(dst)],
                   check=True, capture_output=True)
    data = dst.read_bytes()
    dst.write_bytes(data[data.find(b"moof") - 4:])


def bare_payload(src: Path, dst: Path) -> None:
    data = src.read_bytes()
    i = data.find(b"mdat")
    dst.write_bytes(data[i + 4:i - 4 + struct.unpack_from(">I", data, i - 4)[0]])


def build_fixtures(work: Path) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    good = work / "good.mp4"
    make_clip(good)
    corrupt = work / "corrupt_head.mp4"
    shutil.copyfile(good, corrupt)
    corrupt_head(corrupt)
    big = work / "big_mdat.mp4"
    shutil.copyfile(good, big)
    lie_mdat(big)
    fragment(good, work / "fragmented.mp4")
    (work / "empty.mp4").write_bytes(b"")
    video_only = work / "video_only.mp4"
    make_clip(video_only, audio=False)
    # Keep the bare-payload fixtures away from every other media file: with reference search
    # reaching one level up, a sibling would be auto-picked and carve would never run.
    carve_root = work.parent / "carve"
    (carve_root / "lonely").mkdir(parents=True, exist_ok=True)
    bare_payload(video_only, carve_root / "lonely" / "bare.mp4")
    (carve_root / "interleaved").mkdir(exist_ok=True)
    bare_payload(good, carve_root / "interleaved" / "bare.mp4")
    mp3 = work / "audio.mp3"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=44100", "-t", "3",
                    "-c:a", "libmp3lame", str(mp3)], check=True, capture_output=True)
    data = bytearray(mp3.read_bytes())
    lo = int(len(data) * 0.3)
    span = int(len(data) * 0.4)
    data[lo:lo + span] = bytes(random.Random(2).randrange(256) for _ in range(span))
    mp3.write_bytes(bytes(data))
    return good


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cli", default=shutil.which("mediarescue") or "mediarescue")
    ap.add_argument("--keep", action="store_true", help="keep fixtures and outputs")
    ap.add_argument("--recovered", help="also verify every *.mp4 in this folder decodes")
    args = ap.parse_args()

    cli = args.cli
    tmp = Path(tempfile.mkdtemp(prefix="mediarescue-acceptance-"))
    work = tmp / "files"
    good = build_fixtures(work)

    def jrun(*a, **kw):
        p = run(cli, *a, **kw)
        body = p.stdout[p.stdout.find("{"):] if "{" in p.stdout else "{}"
        return p, json.loads(body or "{}")

    check("version", lambda: (lambda p: p.stdout.strip() if p.returncode == 0
                              else (_ for _ in ()).throw(AssertionError(p.stderr)))(run(cli, "--version")))
    for sub in ("scan", "fix", "doctor"):
        check(f"help {sub}", lambda s=sub: "usage" if run(cli, s, "--help").returncode == 0
              else (_ for _ in ()).throw(AssertionError("help failed")))

    def doctor():
        p = run(cli, "doctor")
        assert p.returncode == 0, p.stdout + p.stderr
        assert "ready: everything" in p.stdout
        return "all checks ok"
    check("doctor", doctor)

    def scan_classes():
        out = run(cli, "scan", str(work)).stdout
        for name, sev in {"corrupt_head.mp4": "corrupt-payload", "big_mdat.mp4": "missing-trailer",
                          "fragmented.mp4": "fragmented", "empty.mp4": "empty",
                          "good.mp4": "healthy"}.items():
            line = next((ln for ln in out.splitlines() if ln.endswith(name)), None)
            assert line, f"{name} missing from scan"
            assert line.startswith(sev), f"{name}: {line[:40]}"
        return "5 classes correct"
    check("scan classification", scan_classes)

    check("scan --json", lambda: f"{len(json.loads(run(cli, 'scan', str(work), '--json').stdout))} rows")

    def deep():
        p = run(cli, "scan", str(work / "audio.mp3"), "--deep", "--json")
        sev = json.loads(p.stdout)[0]["severity"]
        assert sev == "corrupt-payload", sev
        return "damaged mp3 detected"
    check("scan --deep", deep)

    def fix_corrupt():
        out = work / "o_corrupt.mp4"
        p, data = jrun("fix", str(work / "corrupt_head.mp4"), "-o", str(out))
        assert p.returncode == 0, p.stdout[-300:]
        assert data["strategy"] == "salvage" and data["verify"]["decode_errors"] == 0
        assert decode_errors(out) == 0
        return f"{data['dropped_seconds']}s dropped"
    check("fix corrupt-payload", fix_corrupt)

    def fix_trim():
        out = work / "o_big.mp4"
        p, data = jrun("fix", str(work / "big_mdat.mp4"), "-o", str(out))
        assert p.returncode == 0 and data["verify"]["decode_errors"] == 0, p.stdout[-300:]
        return data["strategy"]
    check("fix missing-trailer", fix_trim)

    def fix_frag():
        p, data = jrun("fix", str(work / "fragmented.mp4"), "-o", str(work / "o_frag.mp4"))
        assert p.returncode == 0, p.stdout[-400:]
        assert data["strategy"] == "flatten" and data.get("reference")
        assert data["verify"]["decode_errors"] == 0
        return Path(data["reference"]).name
    check("fix fragmented (auto reference)", fix_frag)

    def fix_empty():
        p = run(cli, "fix", str(work / "empty.mp4"))
        assert p.returncode == 2 and "refusing" in p.stderr
        return "exit 2"
    check("fix empty refused", fix_empty)

    def fix_ghost():
        p = run(cli, "fix", str(work / "tidak-ada.mp4"))
        assert p.returncode == 2 and "Traceback" not in p.stderr
        return "exit 2, no traceback"
    check("fix missing path", fix_ghost)

    def out_on_batch():
        p = run(cli, "fix", str(work), "-o", str(work / "x.mp4"))
        assert p.returncode == 2 and "use --out-dir" in p.stderr
        return "exit 2"
    check("fix -o on a batch", out_on_batch)

    def batch():
        out_dir = work / "batch"
        before = (good.stat().st_size, good.stat().st_mtime_ns)
        p = run(cli, "fix", str(work), "--out-dir", str(out_dir))
        assert (out_dir / "corrupt_head.repaired.mp4").exists()
        assert not (out_dir / "good.repaired.mp4").exists()
        assert (good.stat().st_size, good.stat().st_mtime_ns) == before
        assert decode_errors(out_dir / "corrupt_head.repaired.mp4") == 0
        return f"exit {p.returncode}"
    check("fix batch", batch)

    def quiet():
        p = run(cli, "fix", str(work), "--quiet", "--out-dir", str(work / "q"))
        assert "files:" in p.stdout and "healthy, left untouched" not in p.stdout
        return "summary only"
    check("fix batch --quiet", quiet)

    def verbose():
        p = run(cli, "fix", str(work), "--verbose", "--out-dir", str(work / "v"))
        assert any(k in p.stdout for k in ("dropped the first", "NAL", "reference"))
        return "notes shown"
    check("fix batch --verbose", verbose)

    def carve_ok():
        out = work / "o_carve.mp4"
        p, data = jrun("fix", str(work.parent / "carve" / "lonely" / "bare.mp4"), "-o", str(out))
        assert p.returncode == 0, p.stdout[-400:]
        assert data["strategy"] == "carve" and data["verify"]["decode_errors"] == 0
        assert decode_errors(out) == 0
        return f"{data['nals']} NAL"
    check("carve succeeds", carve_ok)

    def carve_refuse():
        p = run(cli, "fix", str(work.parent / "carve" / "interleaved" / "bare.mp4"),
                "-o", str(work / "x2.mp4"))
        assert p.returncode == 1 and "reference" in p.stdout + p.stderr
        return "refused with a reason"
    check("carve refuses interleaved", carve_refuse)

    def piped():
        assert "\033[" not in run(cli, "scan", str(work)).stdout
        return "no escapes when piped"
    check("colour off when piped", piped)

    def forced():
        assert "\033[" in run(cli, "scan", str(work), env={"FORCE_COLOR": "1"}).stdout
        assert "\033[" not in run(cli, "scan", str(work), "--no-color",
                                 env={"FORCE_COLOR": "1"}).stdout
        return "FORCE_COLOR on, --no-color off"
    check("colour and --no-color", forced)

    def no_ffmpeg():
        p = run(cli, "doctor", env={"MEDIARESCUE_FFMPEG": "definitely-not-ffmpeg"})
        assert p.returncode == 1 and "FAIL" in p.stdout
        assert run(cli, "fix", str(work / "corrupt_head.mp4"),
                   env={"MEDIARESCUE_FFMPEG": "definitely-not-ffmpeg"}).returncode == 3
        return "doctor 1, fix 3"
    check("missing ffmpeg handled", no_ffmpeg)

    def bad_strategy():
        p = run(cli, "fix", str(work / "good.mp4"), "--strategy", "nonsense")
        assert p.returncode == 2 and "invalid choice" in p.stderr
        return "argparse refuses"
    check("invalid --strategy", bad_strategy)

    if args.recovered:
        def recovered():
            folder = Path(args.recovered)
            bad = [f.name for f in folder.glob("*.mp4") if decode_errors(f)]
            assert not bad, bad
            return f"{len(list(folder.glob('*.mp4')))} files clean"
        check("recovered files", recovered)

    width = max(len(n) for n, _, _ in results) + 2
    failed = 0
    for name, ok, detail in results:
        if not ok:
            failed += 1
        print(f"{'PASS' if ok else 'FAIL'}  {name.ljust(width)} {detail}")
    print(f"\n{len(results) - failed}/{len(results)} passed, {failed} failed")
    if args.keep:
        print(f"fixtures kept in {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
