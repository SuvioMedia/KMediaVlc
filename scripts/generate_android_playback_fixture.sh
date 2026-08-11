#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

set -euo pipefail

repository_root="$(git rev-parse --show-toplevel)"
subtitles="$repository_root/scripts/fixtures/android-playback-subtitles.srt"
output="$repository_root/runtime-android/src/androidTest/assets/kmediavlc-android-playback.mkv"

mkdir -p "$(dirname "$output")"

ffmpeg \
  -hide_banner \
  -loglevel error \
  -y \
  -fflags +bitexact \
  -f lavfi \
  -i "testsrc2=size=320x180:rate=24:duration=12" \
  -i "$subtitles" \
  -map 0:v:0 \
  -map 1:0 \
  -map_metadata -1 \
  -map_chapters -1 \
  -vf "hue=s=0,eq=contrast=0.65:brightness=-0.25" \
  -c:v libx264 \
  -preset medium \
  -crf 28 \
  -pix_fmt yuv420p \
  -profile:v baseline \
  -level:v 3.0 \
  -x264-params "threads=1:keyint=24:min-keyint=24:scenecut=0" \
  -flags:v +bitexact \
  -disposition:v:0 default \
  -c:s srt \
  -metadata:s:s:0 language=eng \
  -disposition:s:0 default+forced \
  -metadata creation_time=1970-01-01T00:00:00Z \
  -metadata encoder= \
  -fflags +bitexact \
  -f matroska \
  "$output"
