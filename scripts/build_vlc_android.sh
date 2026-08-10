#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly expected_vlc_revision=b5536cdea24b313ba9215eacfbd7fa3295d7f3ee
readonly expected_libvlcjni_revision=a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21
readonly expected_ndk_revision=29.0.14206865

fail() {
    echo "KMediaVlc Android build: $1" >&2
    exit 1
}

if [[ $# -ne 6 ]]; then
    fail "usage: build_vlc_android.sh VLC_SOURCE LIBVLCJNI_SOURCE NDK CMAKE WORK_DIR EMPTY_OUTPUT_DIR"
fi

for input in "$1" "$2" "$3"; do
    [[ -d "$input" && ! -L "$input" ]] || fail "source/toolchain inputs must be real directories"
done
[[ -x "$4" && ! -L "$4" ]] || fail "CMake must be a real executable"

vlc_source="$(cd "$1" && pwd -P)"
libvlcjni_source="$(cd "$2" && pwd -P)"
ndk_directory="$(cd "$3" && pwd -P)"
cmake_executable="$(cd "$(dirname "$4")" && pwd -P)/$(basename "$4")"

mkdir -p "$5" "$6"
[[ ! -L "$5" && ! -L "$6" ]] || fail "work and output directories must not be symbolic"
work_directory="$(cd "$5" && pwd -P)"
output_directory="$(cd "$6" && pwd -P)"
if [[ -n "$(find "$output_directory" -mindepth 1 -print -quit)" ]]; then
    fail "output directory must be empty"
fi

[[ "$(git -C "$vlc_source" rev-parse HEAD)" == "$expected_vlc_revision" ]] ||
    fail "VLC checkout differs from the pinned revision"
[[ "$(git -C "$libvlcjni_source" rev-parse HEAD)" == "$expected_libvlcjni_revision" ]] ||
    fail "libvlcjni checkout differs from the pinned revision"
[[ -z "$(git -C "$vlc_source" status --porcelain --untracked-files=no)" ]] ||
    fail "VLC checkout has tracked modifications"
[[ -z "$(git -C "$libvlcjni_source" status --porcelain --untracked-files=no)" ]] ||
    fail "libvlcjni checkout has tracked modifications"

actual_ndk_revision="$(sed -n 's/^Pkg.Revision = //p' "$ndk_directory/source.properties")"
[[ "$actual_ndk_revision" == "$expected_ndk_revision" ]] ||
    fail "Android NDK differs from $expected_ndk_revision"

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readelf_executable="$ndk_directory/toolchains/llvm/prebuilt/darwin-x86_64/bin/llvm-readelf"
strip_executable="$ndk_directory/toolchains/llvm/prebuilt/darwin-x86_64/bin/llvm-strip"
if [[ "$(uname -s)" == Linux ]]; then
    readelf_executable="$ndk_directory/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf"
    strip_executable="$ndk_directory/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip"
fi
[[ -x "$readelf_executable" && -x "$strip_executable" ]] ||
    fail "NDK ELF tools are missing for this host"

for abi in arm64-v8a armeabi-v7a; do
    (
        cd "$vlc_source"
        ANDROID_NDK="$ndk_directory" \
            "$libvlcjni_source/buildsystem/compile-libvlc.sh" \
            -a "$abi" --release --static-cpp --license a --no-jni
    )

    libvlc_library="$libvlcjni_source/libvlc/jni/libs/$abi/libvlc.so"
    [[ -s "$libvlc_library" && ! -L "$libvlc_library" ]] ||
        fail "source build did not produce libvlc.so for $abi"

    bridge_build="$work_directory/bridge-$abi"
    "$cmake_executable" \
        -S "$project_root/native/android" \
        -B "$bridge_build" \
        -DCMAKE_TOOLCHAIN_FILE="$ndk_directory/build/cmake/android.toolchain.cmake" \
        -DANDROID_ABI="$abi" \
        -DANDROID_PLATFORM=28 \
        -DANDROID_STL=c++_static \
        -DKMEDIAVLC_VLC_SOURCE_DIR="$vlc_source" \
        -DKMEDIAVLC_ANDROID_LIBVLC="$libvlc_library" \
        -DCMAKE_BUILD_TYPE=RelWithDebInfo
    "$cmake_executable" --build "$bridge_build" --parallel
    [[ -s "$bridge_build/libkmediavlc_android.so" ]] ||
        fail "client bridge build is missing for $abi"

    destination="$output_directory/jni/$abi"
    mkdir -p "$destination"
    cp "$libvlc_library" "$destination/libvlc.so"
    cp "$bridge_build/libkmediavlc_android.so" "$destination/libkmediavlc_android.so"
    "$strip_executable" --strip-unneeded "$destination/libvlc.so"
    "$strip_executable" --strip-unneeded "$destination/libkmediavlc_android.so"

    "$readelf_executable" -d "$destination/libkmediavlc_android.so" |
        grep -F 'Shared library: [libvlc.so]' >/dev/null ||
        fail "client bridge is not dynamically linked to libvlc.so for $abi"
    if "$readelf_executable" -d "$destination/libvlc.so" "$destination/libkmediavlc_android.so" |
        grep -E 'libc\+\+_shared|libvlcjni' >/dev/null; then
        fail "Android payload contains a forbidden shared runtime dependency"
    fi
    if "$readelf_executable" -l "$destination/libvlc.so" "$destination/libkmediavlc_android.so" |
        grep -E '^  LOAD' | grep -v '0x4000$' >/dev/null; then
        fail "Android payload is not aligned for 16 KiB pages"
    fi
done

cat > "$output_directory/android-runtime.properties" <<EOF
schemaVersion=1
vlcRevision=$expected_vlc_revision
libvlcjniRevision=$expected_libvlcjni_revision
bridgeAbi=1
renderEngine=ANATIVEWINDOW
minSdk=28
abis=arm64-v8a,armeabi-v7a
libraries=libkmediavlc_android.so,libvlc.so
staticCpp=true
releaseEligible=false
EOF

echo "Android source-build candidate staged at $output_directory (releaseEligible=false)."
