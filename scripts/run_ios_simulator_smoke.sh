#!/usr/bin/env bash
# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

set -euo pipefail

readonly BUNDLE_ID="io.github.shusek.kmediavlc.smoke"

if [[ $# -ne 3 ]]; then
    echo "usage: $0 <simulator-frameworks-directory> <new-absolute-work-directory> <booted-simulator-udid>" >&2
    exit 2
fi

frameworks="$(cd "$1" && pwd -P)"
work_directory="$2"
simulator_udid="$3"
repository_root="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)"
source_file="$repository_root/scripts/ios-smoke/KMediaVlcSmoke.m"
plist_file="$repository_root/scripts/ios-smoke/Info.plist"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the iOS simulator smoke test must run on macOS" >&2
    exit 2
fi
if [[ "$work_directory" != /* ]] || [[ -e "$work_directory" ]] ||
   [[ ! -d "$(dirname "$work_directory")" ]]; then
    echo "the smoke work directory must be a new absolute path with an existing parent" >&2
    exit 2
fi
if [[ ! "$simulator_udid" =~ ^[0-9A-Fa-f-]{36}$ ]]; then
    echo "the simulator UDID is invalid" >&2
    exit 2
fi
if [[ ! -f "$source_file" || ! -f "$plist_file" ]] ||
   [[ -L "$source_file" || -L "$plist_file" ]]; then
    echo "the checked-in iOS smoke application sources are missing or unsafe" >&2
    exit 1
fi
for framework in KMediaVlc KMediaVlcLibVlc KMediaVlcCore libvmem_plugin; do
    if [[ ! -f "$frameworks/$framework.framework/$framework" ]]; then
        echo "required simulator framework is missing: $framework" >&2
        exit 1
    fi
done
if [[ "$(find "$frameworks" -mindepth 1 -maxdepth 1 -type d -name '*.framework' | wc -l | tr -d ' ')" != "87" ]]; then
    echo "the simulator framework graph must contain exactly 87 frameworks" >&2
    exit 1
fi
if ! /usr/bin/xcrun simctl list devices booted | /usr/bin/grep -Fq "$simulator_udid"; then
    echo "the requested simulator must already be booted" >&2
    exit 2
fi

mkdir "$work_directory"
app="$work_directory/KMediaVlcSmoke.app"
mkdir "$app"
mkdir "$app/Frameworks"
/bin/cp "$plist_file" "$app/Info.plist"
/bin/cp -R "$frameworks/." "$app/Frameworks/"

sdk_root="$(/usr/bin/xcrun --sdk iphonesimulator --show-sdk-path)"
/usr/bin/xcrun --sdk iphonesimulator clang \
    -fobjc-arc \
    -target arm64-apple-ios16.2-simulator \
    -isysroot "$sdk_root" \
    -F "$frameworks" \
    -framework Foundation \
    -framework UIKit \
    -framework KMediaVlc \
    -Wl,-rpath,@executable_path/Frameworks \
    -o "$app/KMediaVlcSmoke" \
    "$source_file"

while IFS= read -r framework; do
    /usr/bin/codesign --force --sign - --timestamp=none "$framework"
done < <(find "$app/Frameworks" -mindepth 1 -maxdepth 1 -type d -name '*.framework' -print | sort)
/usr/bin/codesign --force --sign - --timestamp=none "$app"

/usr/bin/xcrun simctl install "$simulator_udid" "$app"
data_container="$(/usr/bin/xcrun simctl get_app_container "$simulator_udid" "$BUNDLE_ID" data)"
result="$data_container/Documents/kmediavlc-smoke-result.txt"
if [[ -e "$result" ]]; then
    /bin/mv "$result" "$work_directory/previous-result.txt"
fi
/usr/bin/xcrun simctl launch --terminate-running-process "$simulator_udid" "$BUNDLE_ID"

for _attempt in {1..45}; do
    if [[ -f "$result" ]]; then
        outcome="$(/usr/bin/sed -n '1p' "$result")"
        if [[ "$outcome" == PASS\ * ]]; then
            /bin/cp "$result" "$work_directory/result.txt"
            echo "$outcome"
            exit 0
        fi
        echo "$outcome" >&2
        exit 1
    fi
    sleep 1
done

/usr/bin/xcrun simctl terminate "$simulator_udid" "$BUNDLE_ID" 2>/dev/null || true
echo "the iOS simulator smoke application did not publish a result" >&2
exit 1
