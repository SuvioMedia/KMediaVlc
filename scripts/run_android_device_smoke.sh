#!/usr/bin/env bash
# SPDX-License-Identifier: LGPL-2.1-or-later

set -euo pipefail

readonly TEST_PACKAGE="io.github.shusek.kmediavlc.runtime.android.test"
readonly TEST_CLASS="io.github.shusek.kmediavlc.runtime.android.VlcAndroidPlaybackInstrumentedTest"

if [[ $# -ne 5 ]]; then
    echo "usage: $0 <android-native-payload> <new-absolute-work-directory> <absolute-adb> <device-serial> <tested-commit>" >&2
    exit 2
fi

payload_input="$1"
work_directory="$2"
adb_input="$3"
device_serial="$4"
tested_commit="$5"
repository_root="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd -P)"
gradle_wrapper="$repository_root/gradlew"
results_verifier="$repository_root/scripts/verify_android_device_smoke_results.py"

if [[ "$payload_input" != /* || ! -d "$payload_input" || -L "$payload_input" ]]; then
    echo "the Android native payload must be a safe absolute directory" >&2
    exit 2
fi
payload="$(cd "$payload_input" && pwd -P)"
if [[ "$work_directory" != /* ]] || [[ -e "$work_directory" ]] ||
   [[ ! -d "$(dirname "$work_directory")" ]]; then
    echo "the Android device smoke work directory must be a new absolute path" >&2
    exit 2
fi
if [[ "$adb_input" != /* || ! -f "$adb_input" || ! -x "$adb_input" || -L "$adb_input" ]]; then
    echo "adb must be an absolute, executable, non-symbolic regular file" >&2
    exit 2
fi
adb="$(cd "$(dirname "$adb_input")" && pwd -P)/$(basename "$adb_input")"
if [[ ! "$device_serial" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{3,127}$ ]]; then
    echo "the physical Android device serial is invalid" >&2
    exit 2
fi
if [[ ! "$tested_commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "the tested KMediaVlc commit is invalid" >&2
    exit 2
fi
if [[ ! -f "$gradle_wrapper" || -L "$gradle_wrapper" ]] ||
   [[ ! -f "$results_verifier" || -L "$results_verifier" ]]; then
    echo "the checked-in Gradle wrapper or Android result verifier is missing or unsafe" >&2
    exit 1
fi
if [[ "$(/usr/bin/git -C "$repository_root" rev-parse HEAD)" != "$tested_commit" ]] ||
   [[ -n "$(/usr/bin/git -C "$repository_root" status --porcelain)" ]]; then
    echo "the physical Android smoke test must use the exact clean tested commit" >&2
    exit 1
fi
if ! command -v python3 >/dev/null; then
    echo "python3 is required to verify Android physical-device evidence" >&2
    exit 2
fi

adb_device() {
    "$adb" -s "$device_serial" "$@"
}

device_property() {
    adb_device shell getprop "$1" | /usr/bin/tr -d '\r\n'
}

device_state="$(adb_device get-state | /usr/bin/tr -d '\r\n')"
if [[ "$device_state" != "device" ]]; then
    echo "the selected Android target is not an authorized online device" >&2
    exit 1
fi
observed_serial="$(adb_device get-serialno | /usr/bin/tr -d '\r\n')"
if [[ "$observed_serial" != "$device_serial" ]]; then
    echo "adb did not bind the smoke test to the requested device serial" >&2
    exit 1
fi

kernel_qemu="$(device_property ro.kernel.qemu)"
boot_qemu="$(device_property ro.boot.qemu)"
hardware="$(device_property ro.hardware)"
hardware_lower="$(printf '%s' "$hardware" | /usr/bin/tr '[:upper:]' '[:lower:]')"
if [[ -z "$hardware" || "$kernel_qemu" == "1" || "$boot_qemu" == "1" ]] ||
   [[ "$hardware_lower" == *qemu* || "$hardware_lower" == *ranchu* ||
      "$hardware_lower" == *goldfish* || "$hardware_lower" == *cuttlefish* ||
      "$hardware_lower" == *vbox* ]]; then
    echo "an emulator cannot satisfy physical Android acceptance" >&2
    exit 1
fi

device_abi="$(device_property ro.product.cpu.abi)"
case "$device_abi" in
    arm64-v8a|armeabi-v7a) ;;
    *)
        echo "the physical Android device ABI is not in the bundled runtime matrix" >&2
        exit 1
        ;;
esac
api_level="$(device_property ro.build.version.sdk)"
if [[ ! "$api_level" =~ ^[0-9]+$ ]] || (( api_level < 28 )); then
    echo "the physical Android device is below minSdk 28" >&2
    exit 1
fi
manufacturer="$(device_property ro.product.manufacturer)"
model="$(device_property ro.product.model)"
build_fingerprint="$(device_property ro.build.fingerprint)"
if [[ -z "$manufacturer" || -z "$model" || -z "$build_fingerprint" ]]; then
    echo "the physical Android device identity is incomplete" >&2
    exit 1
fi

if adb_device shell pm path "$TEST_PACKAGE" 2>/dev/null | /usr/bin/grep -Fq 'package:'; then
    echo "the KMediaVlc Android test package already exists; remove it manually before acceptance" >&2
    exit 1
fi

/bin/mkdir "$work_directory"
cleanup_enabled=true
cleanup() {
    if [[ "$cleanup_enabled" == true ]] &&
       adb_device shell pm path "$TEST_PACKAGE" 2>/dev/null | /usr/bin/grep -Fq 'package:'; then
        adb_device uninstall "$TEST_PACKAGE" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

cd "$repository_root"
bash "$gradle_wrapper" :runtime-android:clean \
    --no-daemon --no-configuration-cache --console=plain
ANDROID_SERIAL="$device_serial" bash "$gradle_wrapper" \
    :runtime-android:connectedDebugAndroidTest \
    "-PkmediaVlcAndroidNativePayloadDirectory=$payload" \
    "-Pandroid.testInstrumentationRunnerArguments.class=$TEST_CLASS" \
    --no-daemon --no-configuration-cache --rerun-tasks --console=plain

results_root="$repository_root/runtime-android/build/outputs/androidTest-results/connected/debug"
result_files=()
while IFS= read -r result_file; do
    result_files+=("$result_file")
done < <(/usr/bin/find "$results_root" -type f -name 'TEST-*.xml' -print | /usr/bin/sort)
if [[ "${#result_files[@]}" -ne 1 ]]; then
    echo "the Android device smoke run did not produce one unique JUnit result" >&2
    exit 1
fi
result_copy="$work_directory/test-results.xml"
/bin/cp "${result_files[0]}" "$result_copy"

python3 "$results_verifier" \
    --payload "$payload" \
    --results "$result_copy" \
    --tested-commit "$tested_commit" \
    --device-abi "$device_abi" \
    --api-level "$api_level" \
    --manufacturer "$manufacturer" \
    --model "$model" \
    --build-fingerprint "$build_fingerprint" \
    --output "$work_directory/acceptance.json"

if adb_device shell pm path "$TEST_PACKAGE" 2>/dev/null | /usr/bin/grep -Fq 'package:'; then
    if ! adb_device uninstall "$TEST_PACKAGE" >/dev/null; then
        echo "the Android test package could not be removed after acceptance" >&2
        exit 1
    fi
fi
cleanup_enabled=false
trap - EXIT

echo "Physical Android KMediaVlc playback accepted: $work_directory/acceptance.json"
