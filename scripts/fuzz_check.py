"""Bug hunt: hammer the CLI with damaged files and report anything that is not a clean verdict.

Run it against an installed CLI or a source checkout:  python scripts/fuzz_check.py
A crash (traceback, exit code outside 0..3) is a bug. So is a fix that exits 0 while the file it
wrote does not decode. Exits 1 when it finds anything, so CI can gate on it.
"""
import json
import random
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

OUT = Path(tempfile.mkdtemp(prefix="mediarescue-fuzz-"))
OUT.mkdir(parents=True, exist_ok=True)
CLI = [sys.executable, "-m", "mediarescue.cli"]
OK_CODES = {0, 1, 2, 3}


def make_fixtures(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    specs = [
        ("h264.mp4", ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=15", "-f", "lavfi",
                      "-i", "sine=frequency=440", "-t", "3", "-c:v", "libx264", "-c:a", "aac"]),
        ("av1.mp4", ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=15", "-t", "3",
                     "-c:v", "libaom-av1", "-cpu-used", "8", "-crf", "45"]),
        ("audio.mp3", ["-f", "lavfi", "-i", "sine=frequency=440", "-t", "3", "-c:a", "libmp3lame"]),
        ("clip.mkv", ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=15", "-t", "3",
                      "-c:v", "libx264"]),
        ("clip.avi", ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=15", "-f", "lavfi",
                      "-i", "sine=frequency=440", "-t", "3", "-c:v", "mpeg4", "-c:a", "libmp3lame"]),
        ("still.png", ["-f", "lavfi", "-i", "testsrc=size=160x120", "-frames:v", "1"]),
        ("still.jpg", ["-f", "lavfi", "-i", "testsrc=size=160x120", "-frames:v", "1"]),
    ]
    for name, args in specs:
        target = d / name
        if target.exists():
            continue
        p = subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(target)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            print(f"  fixture {name} skipped: {p.stderr.strip()[:90]}")


def mutate(src: Path, dst: Path, kind: str, rng: random.Random):
    data = bytearray(src.read_bytes())
    n = len(data)
    if kind == "truncate-30":
        data = data[: int(n * 0.7)]
    elif kind == "truncate-5":
        data = data[: n - max(1, n // 20)]
    elif kind == "zero-head":
        data[: max(16, n // 50)] = b"\x00" * max(16, n // 50)
    elif kind == "zero-middle":
        lo = n // 3
        data[lo: lo + n // 4] = b"\x00" * (n // 4)
    elif kind == "random-middle":
        lo = n // 3
        span = n // 4
        data[lo: lo + span] = bytes(rng.randrange(256) for _ in range(span))
    elif kind == "garbage-prefix":
        data = bytearray(bytes(rng.randrange(256) for _ in range(4096))) + data
    elif kind == "empty":
        data = bytearray()
    elif kind == "one-byte":
        data = data[:1]
    elif kind == "zero-moov":
        at = data.find(b"moov")
        if at > 0:
            span = min(64, n - at)
            data[at: at + span] = b"\x00" * span
    elif kind == "huge-count":
        at = data.find(b"stsz")
        if at > 0:
            struct.pack_into(">I", data, at + 12, 0xFFFFFFF0)   # sample_count = 4 billion
    dst.write_bytes(bytes(data))


def run(args, timeout=180):
    return subprocess.run(CLI + args, capture_output=True, text=True, timeout=timeout,
                          errors="replace")


def main():
    fixtures = OUT / "fixtures"
    make_fixtures(fixtures)
    work = OUT / "cases"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)

    kinds = ["truncate-30", "truncate-5", "zero-head", "zero-middle", "random-middle",
             "garbage-prefix", "empty", "one-byte", "zero-moov", "huge-count"]
    rng = random.Random(7)
    findings, checked = [], 0
    outcomes = {"fix-ok": 0, "fix-refused": 0, "fix-failed": 0, "scan-healthy": 0,
                "scan-damaged": 0}

    for src in sorted(fixtures.iterdir()):
        for kind in kinds:
            case = work / f"{src.stem}__{kind}{src.suffix}"
            mutate(src, case, kind, rng)
            checked += 1

            p = run(["scan", str(case)])
            blob = p.stdout + p.stderr
            outcomes["scan-healthy" if "healthy" in blob else "scan-damaged"] += 1
            if p.returncode not in OK_CODES:
                findings.append(("scan exit", case.name, p.returncode, blob[-300:]))
            elif "Traceback" in blob:
                findings.append(("scan crash", case.name, p.returncode, blob[-400:]))

            out = work / f"fixed__{case.stem}{case.suffix}"
            q = run(["fix", str(case), "-o", str(out), "--brief"])
            blob = q.stdout + q.stderr
            outcomes["fix-ok" if q.returncode == 0 else
                     ("fix-refused" if q.returncode == 2 else "fix-failed")] += 1
            if q.returncode not in OK_CODES:
                findings.append(("fix exit", case.name, q.returncode, blob[-300:]))
            elif "Traceback" in blob:
                findings.append(("fix crash", case.name, q.returncode, blob[-400:]))
            elif q.returncode == 0 and out.exists():
                # exit 0 must mean the file it produced really decodes
                v = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "null", "-"],
                                   capture_output=True, text=True)
                errs = [ln for ln in v.stderr.splitlines() if ln.strip()]
                if errs:
                    findings.append(("false success", case.name, 0,
                                     f"{len(errs)} decode errors, first: {errs[0][:120]}"))
            elif q.returncode == 0 and not out.exists():
                findings.append(("missing output", case.name, 0, blob[-200:]))

    print(f"\n{checked} cases, {len(findings)} finding(s)")
    print("outcomes:", ", ".join(f"{k}={v}" for k, v in outcomes.items()))
    for kind, name, code, detail in findings:
        print(f"- [{kind}] {name} (exit {code}): {detail.strip()[:220]}")
    (OUT / "findings.json").write_text(json.dumps(findings, indent=1), encoding="utf-8")
    shutil.rmtree(OUT, ignore_errors=True)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
