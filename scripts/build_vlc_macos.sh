#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"

if [[ $# -lt 2 || $# -gt 3 ]]; then
    echo "usage: $0 <vlc-source> <absolute-build-directory> [jobs]" >&2
    exit 2
fi

source_directory="$(cd "$1" && pwd -P)"
build_directory="$2"
jobs="${3:-8}"
repository_root="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)"
configuration="$repository_root/build-recipes/vlc-apple.conf"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the macOS libVLC runtime must be built on macOS" >&2
    exit 2
fi
if [[ "$build_directory" != /* ]]; then
    echo "build directory must be absolute" >&2
    exit 2
fi
if [[ -e "$build_directory" ]]; then
    echo "build directory must not already exist" >&2
    exit 2
fi
if [[ ! -d "$(dirname "$build_directory")" ]]; then
    echo "build directory parent is missing" >&2
    exit 2
fi
if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    echo "jobs must be a positive integer" >&2
    exit 2
fi
if [[ ! -f "$configuration" || -L "$configuration" ]]; then
    echo "pinned Apple build configuration is missing or unsafe" >&2
    exit 1
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

readonly upstream_builder="$source_directory/extras/package/apple/build.sh"
if [[ ! -x "$upstream_builder" ]]; then
    echo "pinned upstream Apple build entry point is missing" >&2
    exit 1
fi

mkdir "$build_directory"
cd "$build_directory"
"$upstream_builder" \
    --arch=arm64 \
    --sdk=macosx \
    --enable-shared \
    --disable-debug \
    --config="$configuration" \
    -j"$jobs"

readonly install_directory="$build_directory/vlc-macosx-arm64"
readonly contrib_directory="$source_directory/contrib/contrib-arm64-apple-macOS_14.0"
if [[ ! -f "$install_directory/lib/libvlc.dylib" ]] ||
   [[ ! -f "$install_directory/lib/libvlccore.dylib" ]] ||
   [[ -z "$(find "$install_directory/lib/vlc/plugins" -type f -name 'lib*_plugin.dylib' -print -quit)" ]]; then
    echo "VLC source build did not produce the expected shared macOS install" >&2
    exit 1
fi
if [[ ! -f "$contrib_directory/Makefile" ]]; then
    echo "VLC source build did not preserve its contrib closure" >&2
    exit 1
fi

make -C "$contrib_directory" list > "$build_directory/contrib-list.txt"
echo "Built pinned macOS arm64 libVLC install: $install_directory"
