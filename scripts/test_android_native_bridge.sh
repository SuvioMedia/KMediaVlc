#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

if [[ $# -ne 4 ]]; then
    echo "usage: test_android_native_bridge.sh VLC_SOURCE NDK CMAKE WORK_DIR" >&2
    exit 1
fi
for input in "$1" "$2"; do
    [[ -d "$input" && ! -L "$input" ]] || exit 1
done
[[ -x "$3" && ! -L "$3" ]] || exit 1
mkdir -p "$4"
[[ ! -L "$4" ]] || exit 1

vlc_source="$(cd "$1" && pwd -P)"
ndk_directory="$(cd "$2" && pwd -P)"
cmake_executable="$(cd "$(dirname "$3")" && pwd -P)/$(basename "$3")"
work_directory="$(cd "$4" && pwd -P)"
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

[[ "$(git -C "$vlc_source" rev-parse HEAD)" == b5536cdea24b313ba9215eacfbd7fa3295d7f3ee ]] || exit 1
[[ "$(sed -n 's/^Pkg.Revision = //p' "$ndk_directory/source.properties")" == 29.0.14206865 ]] || exit 1

host_tag=darwin-x86_64
[[ "$(uname -s)" == Linux ]] && host_tag=linux-x86_64
readelf_executable="$ndk_directory/toolchains/llvm/prebuilt/$host_tag/bin/llvm-readelf"
nm_executable="$ndk_directory/toolchains/llvm/prebuilt/$host_tag/bin/llvm-nm"

for abi in arm64-v8a armeabi-v7a; do
    build_directory="$work_directory/$abi"
    "$cmake_executable" \
        -S "$project_root/native/android" \
        -B "$build_directory" \
        -DCMAKE_TOOLCHAIN_FILE="$ndk_directory/build/cmake/android.toolchain.cmake" \
        -DANDROID_ABI="$abi" \
        -DANDROID_PLATFORM=28 \
        -DANDROID_STL=c++_static \
        -DKMEDIAVLC_VLC_SOURCE_DIR="$vlc_source" \
        -DKMEDIAVLC_ANDROID_FAKE_LIBVLC=ON \
        -DCMAKE_BUILD_TYPE=RelWithDebInfo
    "$cmake_executable" --build "$build_directory" --parallel

    for library in libvlc.so libkmediavlc_android.so; do
        [[ -s "$build_directory/$library" ]] || exit 1
        "$readelf_executable" -l "$build_directory/$library" |
            grep -E '^  LOAD' | grep -v '0x4000$' >/dev/null && exit 1
    done
    "$readelf_executable" -d "$build_directory/libkmediavlc_android.so" |
        grep -F 'Shared library: [libvlc.so]' >/dev/null
    if "$readelf_executable" -d "$build_directory/libkmediavlc_android.so" |
        grep -E 'libc\+\+_shared|libvlcjni' >/dev/null; then
        exit 1
    fi
    "$nm_executable" -D --defined-only "$build_directory/libkmediavlc_android.so" |
        grep -F 'Java_io_github_shusek_kmediavlc_runtime_android_NativeBridge_setSurfaces' >/dev/null
    "$nm_executable" -D --defined-only "$build_directory/libvlc.so" |
        grep -F 'JNI_OnLoad' >/dev/null
done

echo "Android JNI bridge passed both pinned-header NDK ABI gates."
