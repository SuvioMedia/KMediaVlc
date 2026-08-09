#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

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
# -g a disables GPL and GNUv3 contribs. Prebuilt contribs are intentionally not
# requested; the release inventory still audits every resulting binary/plugin.
cd "$source_directory"
./extras/package/win32/build.sh \
    -r \
    -u \
    -z \
    -g a \
    -m \
    -a "$architecture" \
    -o "$output_directory"

# With Meson, upstream clears INSTALLER for a headless build and therefore
# does not execute its guarded install step even when -o is present. Install
# the already-built tree explicitly so a successful compile cannot yield an
# empty release payload.
case "$architecture" in
    x86_64) readonly meson_build_directory="$source_directory/win64-ucrt-meson" ;;
    aarch64) readonly meson_build_directory="$source_directory/winarm64-ucrt-meson" ;;
esac
if [[ ! -d "$output_directory" ]] ||
   [[ -z "$(find "$output_directory" -type f -print -quit 2>/dev/null)" ]]; then
    if [[ ! -d "$meson_build_directory" ]]; then
        echo "VLC Meson build directory is missing: $meson_build_directory" >&2
        exit 1
    fi
    meson install -C "$meson_build_directory" --destdir "$output_directory"
fi
if [[ ! -d "$output_directory" ]] ||
   [[ -z "$(find "$output_directory" -type f -print -quit 2>/dev/null)" ]]; then
    echo "VLC source build produced an empty install payload" >&2
    exit 1
fi
