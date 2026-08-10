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
builder="$repository_root/scripts/build_ios_smoke_app.sh"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the iOS simulator smoke test must run on macOS" >&2
    exit 2
fi
if [[ ! "$simulator_udid" =~ ^[0-9A-Fa-f-]{36}$ ]]; then
    echo "the simulator UDID is invalid" >&2
    exit 2
fi
if [[ ! -f "$builder" || -L "$builder" ]]; then
    echo "the checked-in iOS smoke application builder is missing or unsafe" >&2
    exit 1
fi
if ! /usr/bin/xcrun simctl list devices booted | /usr/bin/grep -Fq "$simulator_udid"; then
    echo "the requested simulator must already be booted" >&2
    exit 2
fi

bash "$builder" "$frameworks" "$work_directory" iphonesimulator
app="$work_directory/KMediaVlcSmoke.app"
while IFS= read -r framework; do
    /usr/bin/codesign --force --sign - --timestamp=none "$framework"
done < <(/usr/bin/find "$app/Frameworks" -mindepth 1 -maxdepth 1 -type d -name '*.framework' -print | /usr/bin/sort)
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
