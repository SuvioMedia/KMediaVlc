#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
readonly MINIMUM_IOS="16.2"

if [[ $# -ne 3 ]]; then
    echo "usage: $0 <vlc-source> <absolute-build-directory> <iphoneos|iphonesimulator>" >&2
    exit 2
fi

source_directory="$(cd "$1" && pwd -P)"
build_directory="$2"
sdk="$3"
repository_root="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)"
cmake="$source_directory/extras/tools/build/bin/cmake"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the iOS KMediaVlc bridge must be built on macOS" >&2
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
        expected_platform="IOS"
        ;;
    iphonesimulator)
        expected_platform="IOSSIMULATOR"
        ;;
    *)
        echo "SDK must be iphoneos or iphonesimulator" >&2
        exit 2
        ;;
esac
if [[ ! -x "$cmake" || -L "$cmake" ]]; then
    echo "the pinned VideoLAN CMake tool is missing or unsafe" >&2
    exit 1
fi
if [[ "$(git -C "$source_directory" rev-parse HEAD)" != "$PINNED_REVISION" ]]; then
    echo "VLC source revision does not match the bridge ABI pin" >&2
    exit 1
fi

"$cmake" \
    -S "$repository_root/native" \
    -B "$build_directory" \
    -DCMAKE_SYSTEM_NAME=iOS \
    -DCMAKE_OSX_SYSROOT="$sdk" \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$MINIMUM_IOS" \
    -DCMAKE_BUILD_TYPE=Release \
    -DKMEDIAVLC_VLC_SOURCE_DIR="$source_directory" \
    -DKMEDIAVLC_BUILD_TEST_FIXTURES=OFF
"$cmake" --build "$build_directory" --config Release --parallel

readonly bridge="$build_directory/libkmediavlc_bridge.dylib"
if [[ ! -f "$bridge" || -L "$bridge" ]]; then
    echo "the iOS bridge build did not produce a regular dylib" >&2
    exit 1
fi
if [[ "$(/usr/bin/lipo -archs "$bridge")" != "arm64" ]]; then
    echo "the iOS bridge must contain exactly the arm64 architecture" >&2
    exit 1
fi
build_version="$(/usr/bin/vtool -show-build "$bridge")"
if [[ "$build_version" != *"platform $expected_platform"* ]] ||
   [[ "$build_version" != *"minos $MINIMUM_IOS"* ]]; then
    echo "the iOS bridge platform or deployment target is invalid" >&2
    exit 1
fi

echo "Built pinned $sdk arm64 KMediaVlc bridge: $bridge"
