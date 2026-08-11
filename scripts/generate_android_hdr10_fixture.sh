#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

repository_root="$(git rev-parse --show-toplevel)"
output="$repository_root/runtime-android/src/androidTest/assets/kmediavlc-android-hdr10.mp4"

mkdir -p "$(dirname "$output")"

ffmpeg \
  -hide_banner \
  -loglevel error \
  -y \
  -fflags +bitexact \
  -f lavfi \
  -i "testsrc2=size=320x180:rate=24:duration=8" \
  -map 0:v:0 \
  -map_metadata -1 \
  -map_chapters -1 \
  -vf "format=yuv420p10le" \
  -c:v libx265 \
  -preset medium \
  -crf 24 \
  -pix_fmt yuv420p10le \
  -profile:v main10 \
  -color_range tv \
  -colorspace bt2020nc \
  -color_primaries bt2020 \
  -color_trc smpte2084 \
  -x265-params \
    "log-level=error:pools=1:frame-threads=1:keyint=24:min-keyint=24:scenecut=0:repeat-headers=1:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc:hdr10=1:hdr10-opt=1:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,50):max-cll=1000,400" \
  -flags:v +bitexact \
  -tag:v hvc1 \
  -movflags +faststart \
  -disposition:v:0 default \
  -metadata creation_time=1970-01-01T00:00:00Z \
  -metadata encoder= \
  -fflags +bitexact \
  "$output"
