#!/usr/bin/env bash
# Install ffmpeg on a GitHub Linux runner, or fail with a reason.
#
# Every network call here is bounded and retried. Two things went wrong without that:
#   * an unbounded apt/curl held two jobs for 40+ minutes instead of failing;
#   * apt mirrors drop connections mid-download ("Could not wait for server fd"), which made a
#     one-shot install fail the job over infrastructure that has nothing to do with this project.
#
# Deliberately no `set -e`: a failed attempt has to fall through to the next one. The final check
# is what fails the job, and it fails loudly, because a run without ffmpeg skips every media test
# and would otherwise go green while testing nothing.
set -uo pipefail

have_ffmpeg() { command -v ffmpeg >/dev/null 2>&1; }

if have_ffmpeg; then
  ffmpeg -version | head -1
  exit 0
fi

sudo timeout 120 add-apt-repository -y universe || true
sudo timeout 300 apt-get update || true

for attempt in 1 2 3; do
  echo "apt-get install ffmpeg, attempt $attempt"
  if sudo timeout 600 apt-get install -y --fix-missing ffmpeg; then
    break
  fi
  sleep 5
  sudo timeout 120 apt-get install -y -f || true
done

if ! have_ffmpeg; then
  echo "apt has no usable ffmpeg; falling back to a static build"
  url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
  if curl -fsSL --retry 3 --retry-all-errors --retry-delay 5 \
           --connect-timeout 20 --max-time 600 -o /tmp/ffmpeg.tar.xz "$url"; then
    mkdir -p /tmp/ffmpeg-static
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp/ffmpeg-static --strip-components=1
    sudo install -m 0755 /tmp/ffmpeg-static/ffmpeg /tmp/ffmpeg-static/ffprobe /usr/local/bin/
  fi
fi

if ! have_ffmpeg; then
  echo "::error::no ffmpeg on this runner: every media test would skip, so this run proves nothing"
  exit 1
fi

ffmpeg -version | head -1
