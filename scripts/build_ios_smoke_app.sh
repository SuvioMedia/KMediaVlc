#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly PLAYBACK_FIXTURE_SHA256="f9cee3480b4619e2d94979a30b40f19cbb417289d3453e7bbb890a871c6f9718"
readonly VLC_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
readonly MINIMUM_IOS="16.2"

if [[ $# -ne 3 && $# -ne 5 ]]; then
    echo "usage: $0 <frameworks-directory> <new-absolute-work-directory> <iphonesimulator>" >&2
    echo "       $0 <frameworks-directory> <new-absolute-work-directory> <iphoneos> <tested-commit> <vlc-source>" >&2
    exit 2
fi

frameworks_input="$1"
work_directory="$2"
sdk="$3"
tested_commit="${4:-}"
vlc_source_input="${5:-}"
repository_root="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)"
bridge_builder="$repository_root/scripts/build_kmediavlc_ios_bridge.sh"
source_file="$repository_root/scripts/ios-smoke/KMediaVlcSmoke.m"
plist_file="$repository_root/scripts/ios-smoke/Info.plist"
playback_fixture="$repository_root/runtime-android/src/androidTest/assets/kmediavlc-android-playback.mkv"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the iOS smoke application must be built on macOS" >&2
    exit 2
fi
if [[ "$frameworks_input" != /* || ! -d "$frameworks_input" || -L "$frameworks_input" ]]; then
    echo "the framework directory must be a safe absolute directory" >&2
    exit 2
fi
frameworks="$(cd "$frameworks_input" && pwd -P)"
if [[ "$work_directory" != /* ]] || [[ -e "$work_directory" ]] ||
   [[ ! -d "$(dirname "$work_directory")" ]]; then
    echo "the smoke work directory must be a new absolute path with an existing parent" >&2
    exit 2
fi
case "$sdk" in
    iphoneos)
        if [[ $# -ne 5 ]] || [[ ! "$tested_commit" =~ ^[0-9a-f]{40}$ ]] ||
           [[ "$(/usr/bin/git -C "$repository_root" rev-parse HEAD)" != "$tested_commit" ]] ||
           [[ -n "$(/usr/bin/git -C "$repository_root" status --porcelain --untracked-files=no)" ]]; then
            echo "the physical-device smoke build must use the exact clean tested commit" >&2
            exit 1
        fi
        if [[ "$vlc_source_input" != /* || ! -d "$vlc_source_input" || -L "$vlc_source_input" ]] ||
           [[ ! -f "$bridge_builder" || -L "$bridge_builder" ]]; then
            echo "the pinned VLC source or checked-in bridge builder is missing or unsafe" >&2
            exit 2
        fi
        vlc_source="$(cd "$vlc_source_input" && pwd -P)"
        if [[ "$(/usr/bin/git -C "$vlc_source" rev-parse HEAD)" != "$VLC_REVISION" ]] ||
           [[ -n "$(/usr/bin/git -C "$vlc_source" status --porcelain --untracked-files=no)" ]]; then
            echo "the physical-device smoke build requires the exact clean VLC source" >&2
            exit 1
        fi
        target="arm64-apple-ios$MINIMUM_IOS"
        expected_platform="IOS"
        ;;
    iphonesimulator)
        if [[ $# -ne 3 ]]; then
            echo "the simulator smoke build does not accept device provenance arguments" >&2
            exit 2
        fi
        target="arm64-apple-ios$MINIMUM_IOS-simulator"
        expected_platform="IOSSIMULATOR"
        ;;
    *)
        echo "SDK must be iphoneos or iphonesimulator" >&2
        exit 2
        ;;
esac
if [[ ! -f "$source_file" || ! -f "$plist_file" || ! -f "$playback_fixture" ]] ||
   [[ -L "$source_file" || -L "$plist_file" || -L "$playback_fixture" ]]; then
    echo "the checked-in iOS smoke application sources are missing or unsafe" >&2
    exit 1
fi
playback_fixture_sha256="$(/usr/bin/shasum -a 256 "$playback_fixture" | /usr/bin/awk '{print $1}')"
if [[ "$playback_fixture_sha256" != "$PLAYBACK_FIXTURE_SHA256" ]]; then
    echo "the shared playback fixture differs from its pinned hash" >&2
    exit 1
fi
if [[ "$(/usr/bin/find "$frameworks" -mindepth 1 -maxdepth 1 -print | /usr/bin/wc -l | /usr/bin/tr -d ' ')" != "87" ]] ||
   [[ "$(/usr/bin/find "$frameworks" -mindepth 1 -maxdepth 1 -type d -name '*.framework' -print | /usr/bin/wc -l | /usr/bin/tr -d ' ')" != "87" ]]; then
    echo "the iOS framework graph must contain exactly 87 framework directories" >&2
    exit 1
fi
for required_framework in KMediaVlc KMediaVlcLibVlc KMediaVlcCore \
                          libaudiounit_ios_plugin libvmem_plugin; do
    required="$frameworks/$required_framework.framework/$required_framework"
    if [[ ! -f "$required" || -L "$required" ]]; then
        echo "required iOS framework is missing or unsafe: $required_framework" >&2
        exit 1
    fi
done
while IFS= read -r framework; do
    if [[ -L "$framework" ]]; then
        echo "the iOS framework graph contains a symbolic directory" >&2
        exit 1
    fi
    framework_name="$(/usr/bin/basename "$framework" .framework)"
    binary="$framework/$framework_name"
    if [[ ! -f "$binary" || -L "$binary" ]] ||
       [[ "$(/usr/bin/lipo -archs "$binary")" != "arm64" ]]; then
        echo "the iOS framework binary is missing, symbolic, or not exactly arm64: $framework_name" >&2
        exit 1
    fi
    build_version="$(/usr/bin/vtool -show-build "$binary")"
    if [[ "$build_version" != *"platform $expected_platform"* ]] ||
       [[ "$build_version" != *"minos $MINIMUM_IOS"* ]]; then
        echo "the iOS framework platform or deployment target is invalid: $framework_name" >&2
        exit 1
    fi
    install_name="$(/usr/bin/otool -D "$binary" | /usr/bin/sed -n '2p')"
    if [[ "$install_name" != "@rpath/$framework_name.framework/$framework_name" ]]; then
        echo "the iOS framework install name is not application-private: $framework_name" >&2
        exit 1
    fi
done < <(/usr/bin/find "$frameworks" -mindepth 1 -maxdepth 1 -type d -name '*.framework' -print | /usr/bin/sort)

/bin/mkdir "$work_directory"
if [[ "$sdk" == iphoneos ]]; then
    bash "$bridge_builder" "$vlc_source" "$work_directory/bridge-build" iphoneos
fi
app="$work_directory/KMediaVlcSmoke.app"
/bin/mkdir "$app"
/bin/mkdir "$app/Frameworks"
/bin/cp "$plist_file" "$app/Info.plist"
/bin/cp "$playback_fixture" "$app/kmediavlc-playback.mkv"
/bin/cp -R "$frameworks/." "$app/Frameworks/"
if [[ "$sdk" == iphoneos ]]; then
    /bin/cp "$work_directory/bridge-build/libkmediavlc_bridge.dylib" \
        "$app/Frameworks/KMediaVlc.framework/KMediaVlc"
    /usr/bin/install_name_tool -id @rpath/KMediaVlc.framework/KMediaVlc \
        "$app/Frameworks/KMediaVlc.framework/KMediaVlc"
    /usr/bin/plutil -insert KMediaVlcTestedCommit -string "$tested_commit" "$app/Info.plist"
    /usr/bin/plutil -insert KMediaVlcVlcRevision -string "$VLC_REVISION" "$app/Info.plist"
fi

sdk_root="$(/usr/bin/xcrun --sdk "$sdk" --show-sdk-path)"
/usr/bin/xcrun --sdk "$sdk" clang \
    -fobjc-arc \
    -Wall \
    -Wextra \
    -Werror \
    -target "$target" \
    -isysroot "$sdk_root" \
    -F "$app/Frameworks" \
    -framework Foundation \
    -framework UIKit \
    -framework KMediaVlc \
    -Wl,-rpath,@executable_path/Frameworks \
    -o "$app/KMediaVlcSmoke" \
    "$source_file"

if [[ "$(/usr/bin/lipo -archs "$app/KMediaVlcSmoke")" != "arm64" ]]; then
    echo "the iOS smoke executable must contain exactly arm64" >&2
    exit 1
fi
app_build_version="$(/usr/bin/vtool -show-build "$app/KMediaVlcSmoke")"
if [[ "$app_build_version" != *"platform $expected_platform"* ]] ||
   [[ "$app_build_version" != *"minos $MINIMUM_IOS"* ]]; then
    echo "the iOS smoke executable platform or deployment target is invalid" >&2
    exit 1
fi

echo "Built unsigned $sdk KMediaVlc smoke application: $app"
