#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly BUNDLE_ID="io.github.shusek.kmediavlc.smoke"
readonly PLAYBACK_FIXTURE_SHA256="f9cee3480b4619e2d94979a30b40f19cbb417289d3453e7bbb890a871c6f9718"
readonly VLC_REVISION="e439692079a75cacb5f07310d1ec2dc20bfd1fe0"

if [[ $# -ne 4 ]]; then
    echo "usage: $0 <signed-device-app> <new-absolute-work-directory> <device-identifier> <tested-commit>" >&2
    exit 2
fi

app_input="$1"
work_directory="$2"
device_identifier="$3"
tested_commit="$4"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "the iOS device smoke test must run on macOS" >&2
    exit 2
fi
if [[ "$app_input" != /* || ! -d "$app_input" || -L "$app_input" ]]; then
    echo "the signed iOS application must be a safe absolute bundle path" >&2
    exit 2
fi
app="$(cd "$app_input" && pwd -P)"
if [[ "$work_directory" != /* ]] || [[ -e "$work_directory" ]] ||
   [[ ! -d "$(dirname "$work_directory")" ]]; then
    echo "the device smoke work directory must be a new absolute path with an existing parent" >&2
    exit 2
fi
if [[ ! "$device_identifier" =~ ^[0-9A-Fa-f]{40}$ ]] &&
   [[ ! "$device_identifier" =~ ^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}$ ]] &&
   [[ ! "$device_identifier" =~ ^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$ ]]; then
    echo "the physical iOS device identifier is invalid" >&2
    exit 2
fi
if [[ ! "$tested_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "the tested KMediaVlc commit is invalid" >&2
    exit 2
fi
if [[ ! -f "$app/Info.plist" || -L "$app/Info.plist" ]] ||
   [[ ! -f "$app/KMediaVlcSmoke" || -L "$app/KMediaVlcSmoke" ]] ||
   [[ ! -f "$app/kmediavlc-playback.mkv" || -L "$app/kmediavlc-playback.mkv" ]] ||
   [[ ! -f "$app/embedded.mobileprovision" || -L "$app/embedded.mobileprovision" ]]; then
    echo "the signed iOS application bundle is incomplete or unsafe" >&2
    exit 1
fi
observed_bundle_id="$(/usr/bin/plutil -extract CFBundleIdentifier raw -o - "$app/Info.plist")"
observed_tested_commit="$(/usr/bin/plutil -extract KMediaVlcTestedCommit raw -o - "$app/Info.plist")"
observed_vlc_revision="$(/usr/bin/plutil -extract KMediaVlcVlcRevision raw -o - "$app/Info.plist")"
if [[ "$observed_bundle_id" != "$BUNDLE_ID" ]] ||
   [[ "$observed_tested_commit" != "$tested_commit" ]] ||
   [[ "$observed_vlc_revision" != "$VLC_REVISION" ]]; then
    echo "the signed iOS application has an unexpected source identity" >&2
    exit 1
fi
fixture_sha256="$(/usr/bin/shasum -a 256 "$app/kmediavlc-playback.mkv" | /usr/bin/awk '{print $1}')"
if [[ "$fixture_sha256" != "$PLAYBACK_FIXTURE_SHA256" ]]; then
    echo "the signed iOS application contains an unexpected playback fixture" >&2
    exit 1
fi
if [[ "$(/usr/bin/find "$app/Frameworks" -mindepth 1 -maxdepth 1 -print | /usr/bin/wc -l | /usr/bin/tr -d ' ')" != "87" ]] ||
   [[ "$(/usr/bin/find "$app/Frameworks" -mindepth 1 -maxdepth 1 -type d -name '*.framework' -print | /usr/bin/wc -l | /usr/bin/tr -d ' ')" != "87" ]]; then
    echo "the signed iOS application must embed exactly 87 frameworks" >&2
    exit 1
fi
for required_framework in KMediaVlc KMediaVlcLibVlc KMediaVlcCore \
                          libaudiounit_ios_plugin libvmem_plugin; do
    required="$app/Frameworks/$required_framework.framework/$required_framework"
    if [[ ! -f "$required" || -L "$required" ]]; then
        echo "required signed iOS framework is missing or unsafe: $required_framework" >&2
        exit 1
    fi
done
while IFS= read -r framework; do
    if [[ -L "$framework" ]]; then
        echo "the signed iOS application contains a symbolic framework" >&2
        exit 1
    fi
    framework_name="$(/usr/bin/basename "$framework" .framework)"
    binary="$framework/$framework_name"
    if [[ ! -f "$binary" || -L "$binary" ]] ||
       [[ "$(/usr/bin/lipo -archs "$binary")" != "arm64" ]]; then
        echo "the signed iOS framework is missing, symbolic, or not exactly arm64: $framework_name" >&2
        exit 1
    fi
    framework_build_version="$(/usr/bin/vtool -show-build "$binary")"
    if [[ "$framework_build_version" != *"platform IOS"* ]] ||
       [[ "$framework_build_version" != *"minos 16.2"* ]]; then
        echo "the signed iOS framework is not an iOS 16.2 device slice: $framework_name" >&2
        exit 1
    fi
    install_name="$(/usr/bin/otool -D "$binary" | /usr/bin/sed -n '2p')"
    if [[ "$install_name" != "@rpath/$framework_name.framework/$framework_name" ]]; then
        echo "the signed iOS framework install name is invalid: $framework_name" >&2
        exit 1
    fi
done < <(/usr/bin/find "$app/Frameworks" -mindepth 1 -maxdepth 1 -type d -name '*.framework' -print | /usr/bin/sort)
if [[ "$(/usr/bin/lipo -archs "$app/KMediaVlcSmoke")" != "arm64" ]]; then
    echo "the signed iOS application is not exactly arm64" >&2
    exit 1
fi
app_build_version="$(/usr/bin/vtool -show-build "$app/KMediaVlcSmoke")"
if [[ "$app_build_version" != *"platform IOS"* ]] ||
   [[ "$app_build_version" != *"minos 16.2"* ]]; then
    echo "the signed iOS application is not an iOS 16.2 device build" >&2
    exit 1
fi
if ! /usr/bin/codesign --verify --deep --strict "$app"; then
    echo "the iOS application or one of its embedded frameworks is not validly signed" >&2
    exit 1
fi
signature_details="$(/usr/bin/codesign -d --verbose=2 "$app" 2>&1)"
if [[ "$signature_details" == *"Signature=adhoc"* ]]; then
    echo "an ad-hoc signature cannot satisfy physical iOS device acceptance" >&2
    exit 1
fi

/bin/mkdir "$work_directory"
installed=false
cleanup() {
    if [[ "$installed" == true ]]; then
        /usr/bin/xcrun devicectl device uninstall app \
            --device "$device_identifier" \
            --timeout 60 \
            "$BUNDLE_ID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

/usr/bin/xcrun devicectl device install app \
    --device "$device_identifier" \
    --timeout 120 \
    --json-output "$work_directory/install.json" \
    "$app"
installed=true

set +e
/usr/bin/xcrun devicectl device process launch \
    --device "$device_identifier" \
    --timeout 90 \
    --terminate-existing \
    --console \
    "$BUNDLE_ID" 2>&1 | /usr/bin/tee "$work_directory/console.txt"
launch_status="${PIPESTATUS[0]}"
set -e

if [[ "$launch_status" -ne 0 ]] ||
   /usr/bin/grep -Fq 'KMEDIAVLC_SMOKE FAIL ' "$work_directory/console.txt" ||
   [[ "$(/usr/bin/grep -Fc 'KMEDIAVLC_SMOKE PASS ' "$work_directory/console.txt")" != "1" ]]; then
    echo "the physical iOS device smoke test failed" >&2
    exit 1
fi
/usr/bin/sed -n 's/^.*KMEDIAVLC_SMOKE //p' "$work_directory/console.txt" > "$work_directory/result.txt"
outcome="$(/usr/bin/sed -n '1p' "$work_directory/result.txt")"
echo "$outcome"
