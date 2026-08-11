#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"

if [[ $# -ne 3 ]]; then
    echo "usage: $0 <vlc-source> <x86_64|aarch64> <absolute-output-directory>" >&2
    exit 2
fi

source_directory="$(cd "$1" && pwd -P)"
architecture="$2"
output_directory="$3"

if [[ "$architecture" != "x86_64" && "$architecture" != "aarch64" ]]; then
    echo "unsupported Windows architecture: $architecture" >&2
    exit 2
fi
if [[ "$output_directory" != /* ]]; then
    echo "output directory must be absolute" >&2
    exit 2
fi
if [[ -e "$output_directory" ]]; then
    echo "output directory must not already exist" >&2
    exit 2
fi

actual_revision="$(git -C "$source_directory" rev-parse HEAD)"
if [[ "$actual_revision" != "$PINNED_REVISION" ]]; then
    echo "VLC source revision mismatch: $actual_revision" >&2
    exit 1
fi
if [[ -n "$(git -C "$source_directory" status --porcelain --untracked-files=no)" ]]; then
    echo "VLC source checkout contains tracked modifications" >&2
    exit 1
fi

# This is VideoLAN's exact pinned Windows recipe in release/UCRT/headless mode.
# -g l disables GPL contribs while permitting LGPLv3 dependencies required by
# the HTTPS/TLS module. Stream-output encoders are not used by the closed
# playback plugin set, so disable them in both the contrib and Meson graphs.
# Keeping only the contrib flag is insufficient: Meson would still define
# ENABLE_SOUT and compile encoder branches against decoder-only dependencies.
# libshout remains explicitly available because the pinned upstream Meson
# recipe enables its playback-relevant module even in a headless build.
# Prebuilt contribs are intentionally not requested; the release inventory
# still audits every resulting binary.
export CONTRIBFLAGS="--disable-sout --enable-shout"
export MCONFIGFLAGS="-Dstream_outputs=false -Dvideolan_manager=false"
cd "$source_directory"
./extras/package/win32/build.sh \
    -r \
    -u \
    -z \
    -g l \
    -m \
    -a "$architecture"

# A headless build intentionally leaves installation to this closed packaging
# step. Install only runtime-tagged files and strip targets with the pinned
# cross toolchain; headers, import libraries, and build-only executables must
# never enter the native payload candidate.
case "$architecture" in
    x86_64) readonly meson_build_directory="$source_directory/win64-ucrt-meson" ;;
    aarch64) readonly meson_build_directory="$source_directory/winarm64-ucrt-meson" ;;
esac
readonly meson_executable="$source_directory/extras/tools/build/bin/meson"
if [[ ! -d "$meson_build_directory" ]]; then
    echo "VLC Meson build directory is missing: $meson_build_directory" >&2
    exit 1
fi
if [[ ! -x "$meson_executable" ]]; then
    echo "VLC bundled Meson is missing: $meson_executable" >&2
    exit 1
fi
"$meson_executable" install \
    -C "$meson_build_directory" \
    --destdir "$output_directory" \
    --tags runtime \
    --strip
if [[ ! -d "$output_directory" ]] ||
   [[ -z "$(find "$output_directory" -type f -print -quit 2>/dev/null)" ]]; then
    echo "VLC source build produced an empty install payload" >&2
    exit 1
fi
