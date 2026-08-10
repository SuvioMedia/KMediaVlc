#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

set -euo pipefail

readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"

if [[ $# -lt 3 || $# -gt 4 ]]; then
    echo "usage: $0 <vlc-source> <absolute-build-directory> <iphoneos|iphonesimulator> [jobs]" >&2
    exit 2
fi

source_directory="$(cd "$1" && pwd -P)"
build_directory="$2"
sdk="$3"
jobs="${4:-8}"
repository_root="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)"
configuration="$repository_root/build-recipes/vlc-apple.conf"
source_patch="$repository_root/build-recipes/patches/vlc-ios-meson-native-compiler.patch"
fribidi_patch="$repository_root/build-recipes/patches/fribidi-meson-native-generator.patch"
utfcpp_rules="$repository_root/build-recipes/vlc-contrib-utfcpp-rules.mak"
meson_native_file="$repository_root/build-recipes/vlc-apple-native.ini"
meson_native_tmpdir="$build_directory/meson-native-tmp"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the iOS libVLC runtime must be built on macOS" >&2
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
case "$sdk" in
    iphoneos)
        contrib_directory="$source_directory/contrib/contrib-arm64-apple-iOS_16.2"
        ;;
    iphonesimulator)
        contrib_directory="$source_directory/contrib/contrib-arm64-apple-iOS-Simulator_16.2"
        ;;
    *)
        echo "SDK must be iphoneos or iphonesimulator" >&2
        exit 2
        ;;
esac
if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    echo "jobs must be a positive integer" >&2
    exit 2
fi
if [[ ! -f "$configuration" || -L "$configuration" ]]; then
    echo "pinned Apple build configuration is missing or unsafe" >&2
    exit 1
fi
if [[ ! -f "$source_patch" || -L "$source_patch" ]]; then
    echo "pinned iOS source patch is missing or unsafe" >&2
    exit 1
fi
if [[ ! -f "$fribidi_patch" || -L "$fribidi_patch" ]]; then
    echo "pinned FriBidi source patch is missing or unsafe" >&2
    exit 1
fi
if [[ ! -f "$utfcpp_rules" || -L "$utfcpp_rules" ]]; then
    echo "pinned utf8cpp contrib recipe is missing or unsafe" >&2
    exit 1
fi
if [[ ! -f "$meson_native_file" || -L "$meson_native_file" ]]; then
    echo "pinned Apple Meson native file is missing or unsafe" >&2
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

# VLC clears the compiler environment before Meson cross builds. Meson 1.11
# consequently has no build-machine compiler for native table generators used
# by FriBidi and HarfBuzz. Keep the target compilers in the generated cross
# file while supplying an explicit, host-only Meson native file and temp path.
git -C "$source_directory" apply --check "$source_patch"
git -C "$source_directory" apply "$source_patch"
cp "$fribidi_patch" \
    "$source_directory/contrib/src/fribidi/kmediavlc-meson-native-generator.patch"
if [[ -e "$source_directory/contrib/src/utfcpp" ]]; then
    echo "VLC source unexpectedly contains an utf8cpp contrib recipe" >&2
    exit 1
fi
mkdir "$source_directory/contrib/src/utfcpp"
cp "$utfcpp_rules" "$source_directory/contrib/src/utfcpp/rules.mak"
export KMEDIAVLC_MESON_NATIVE_FILE="$meson_native_file"
export KMEDIAVLC_MESON_NATIVE_TMPDIR="$meson_native_tmpdir"

readonly upstream_builder="$source_directory/extras/package/apple/build.sh"
if [[ ! -x "$upstream_builder" ]]; then
    echo "pinned upstream Apple build entry point is missing" >&2
    exit 1
fi

mkdir "$build_directory"
mkdir "$meson_native_tmpdir"
cd "$build_directory"
"$upstream_builder" \
    --arch=arm64 \
    --sdk="$sdk" \
    --enable-shared \
    --disable-debug \
    --config="$configuration" \
    -j"$jobs"

readonly install_directory="$build_directory/vlc-$sdk-arm64"
if [[ ! -f "$install_directory/lib/libvlc.dylib" ]] ||
   [[ ! -f "$install_directory/lib/libvlccore.dylib" ]] ||
   [[ -z "$(find "$install_directory/lib/vlc/plugins" -type f -name 'lib*_plugin.dylib' -print -quit)" ]]; then
    echo "VLC source build did not produce the expected shared iOS install" >&2
    exit 1
fi
if [[ ! -f "$contrib_directory/Makefile" ]]; then
    echo "VLC source build did not preserve its contrib closure" >&2
    exit 1
fi

make -C "$contrib_directory" list > "$build_directory/contrib-list.txt"
echo "Built pinned $sdk arm64 libVLC install: $install_directory"
