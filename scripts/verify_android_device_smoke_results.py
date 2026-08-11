#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


PINNED_VLC_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
PINNED_LIBVLCJNI_REVISION = "a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21"
TEST_CLASS = (
    "io.github.shusek.kmediavlc.runtime.android."
    "VlcAndroidPlaybackInstrumentedTest"
)
TEST_CASES = (
    "automaticDecodePreservesHdr10SurfaceSignal",
    "automaticDecodeUsesMediaCodecAndSurvivesSurfaceLifecycle",
    "softwareDecodeAvoidsMediaCodecAndSurvivesSurfaceLifecycle",
)
REQUIRED_LIBRARIES = (
    "jni/arm64-v8a/libkmediavlc_android.so",
    "jni/arm64-v8a/libvlc.so",
    "jni/armeabi-v7a/libkmediavlc_android.so",
    "jni/armeabi-v7a/libvlc.so",
)
SAFE_DEVICE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._+()/:-]{0,191}")
SAFE_FINGERPRINT = re.compile(r"[\x21-\x7e]{1,512}")


def fail(message: str) -> None:
    raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def parse_closed_properties(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        fail(f"Android runtime manifest cannot be read as ASCII: {error}")
    values: dict[str, str] = {}
    for line in lines:
        if line.count("=") != 1:
            fail("Android runtime manifest contains a malformed line.")
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key) or not value or key in values:
            fail("Android runtime manifest contains an invalid or duplicate key.")
        values[key] = value
    return values


def payload_tree(payload: Path) -> tuple[list[dict[str, object]], str]:
    if not payload.is_absolute() or not payload.is_dir() or payload.is_symlink():
        fail("Android payload must be a safe absolute directory.")
    entries: list[dict[str, object]] = []
    for path in sorted(payload.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            fail("Android payload contains a symbolic path.")
        if path.is_dir():
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            fail("Android payload contains an empty or non-regular file.")
        relative = path.relative_to(payload).as_posix()
        entries.append(
            {
                "path": relative,
                "sha256": sha256(path),
                "size": path.stat().st_size,
            }
        )
    paths = {entry["path"] for entry in entries}
    required = set(REQUIRED_LIBRARIES) | {
        "android-runtime.properties",
        "legal/android-static-legal.json",
    }
    if not required.issubset(paths):
        fail("Android payload omits a required runtime, manifest, or legal-evidence file.")

    manifest = parse_closed_properties(payload / "android-runtime.properties")
    expected = {
        "schemaVersion": "1",
        "vlcRevision": PINNED_VLC_REVISION,
        "libvlcjniRevision": PINNED_LIBVLCJNI_REVISION,
        "bridgeAbi": "1",
        "renderEngine": "ANATIVEWINDOW",
        "minSdk": "28",
        "abis": "arm64-v8a,armeabi-v7a",
        "libraries": "libkmediavlc_android.so,libvlc.so",
        "staticCpp": "true",
        "releaseEligible": manifest.get("releaseEligible", ""),
    }
    if manifest.get("releaseEligible") not in {"true", "false"} or manifest != expected:
        fail("Android payload manifest differs from the pinned runtime contract.")

    tree_digest = hashlib.sha256()
    for entry in entries:
        record = (
            f"{entry['sha256']} {entry['size']} {entry['path']}\n".encode("utf-8")
        )
        tree_digest.update(record)
    return entries, tree_digest.hexdigest()


def nonnegative_count(node: ET.Element, name: str) -> int:
    value = node.get(name)
    if value is None or not value.isdigit():
        fail(f"Android JUnit results have no valid {name} count.")
    return int(value)


def verify_junit(results: Path) -> tuple[str, list[str]]:
    if not results.is_absolute() or not results.is_file() or results.is_symlink():
        fail("Android JUnit result must be a safe absolute regular file.")
    try:
        root = ET.parse(results).getroot()
    except (ET.ParseError, OSError) as error:
        fail(f"Android JUnit result cannot be parsed: {error}")
    if root.tag != "testsuites":
        fail("Android JUnit result has an unexpected root element.")
    expected_count = len(TEST_CASES)
    if (
        nonnegative_count(root, "tests") != expected_count
        or nonnegative_count(root, "failures") != 0
        or nonnegative_count(root, "errors") != 0
        or nonnegative_count(root, "skipped") != 0
    ):
        fail("Android physical-device result is not an exact three-test pass.")
    suites = root.findall("testsuite")
    if len(suites) != 1:
        fail("Android physical-device result must contain exactly one suite.")
    suite = suites[0]
    if (
        suite.get("name") != TEST_CLASS
        or nonnegative_count(suite, "tests") != expected_count
        or nonnegative_count(suite, "failures") != 0
        or nonnegative_count(suite, "errors") != 0
        or nonnegative_count(suite, "skipped") != 0
    ):
        fail("Android physical-device suite identity or counts are invalid.")
    cases = suite.findall("testcase")
    observed = sorted(case.get("name", "") for case in cases)
    if observed != sorted(TEST_CASES) or any(case.get("classname") != TEST_CLASS for case in cases):
        fail("Android physical-device suite did not execute the exact required cases.")
    if any(case.find("failure") is not None for case in cases):
        fail("Android physical-device suite contains a failed case.")
    if any(case.find("error") is not None for case in cases):
        fail("Android physical-device suite contains an errored case.")
    if any(case.find("skipped") is not None for case in cases):
        fail("Android physical-device suite contains a skipped case.")
    device_properties = [
        node.get("value", "")
        for node in suite.findall("./properties/property")
        if node.get("name") == "device"
    ]
    if len(device_properties) != 1 or not device_properties[0]:
        fail("Android physical-device result has no unique device property.")
    device_description = device_properties[0]
    if not SAFE_DEVICE_TEXT.fullmatch(device_description):
        fail("Android physical-device result has an unsafe device property.")
    lowered = device_description.lower()
    if "(avd)" in lowered or "emulator" in lowered:
        fail("Android physical-device evidence was produced by an emulator.")
    return device_description, observed


def verify_acceptance(
    *,
    payload: Path,
    results: Path,
    tested_commit: str,
    device_abi: str,
    api_level: int,
    manufacturer: str,
    model: str,
    build_fingerprint: str,
) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{40}", tested_commit):
        fail("Tested KMediaVlc commit must be an exact lowercase commit.")
    if device_abi not in {"arm64-v8a", "armeabi-v7a"}:
        fail("Physical Android device ABI is not a publication target.")
    if api_level < 28:
        fail("Physical Android device is below minSdk 28.")
    if not SAFE_DEVICE_TEXT.fullmatch(manufacturer):
        fail("Physical Android manufacturer is missing or unsafe.")
    if not SAFE_DEVICE_TEXT.fullmatch(model):
        fail("Physical Android model is missing or unsafe.")
    if not SAFE_FINGERPRINT.fullmatch(build_fingerprint):
        fail("Physical Android build fingerprint is missing or unsafe.")

    entries, tree_sha256 = payload_tree(payload)
    device_description, cases = verify_junit(results)
    return {
        "schemaVersion": 1,
        "kmediaVlcCommit": tested_commit,
        "vlcRevision": PINNED_VLC_REVISION,
        "libvlcjniRevision": PINNED_LIBVLCJNI_REVISION,
        "physicalDevice": {
            "apiLevel": api_level,
            "buildFingerprint": build_fingerprint,
            "gradleDescription": device_description,
            "manufacturer": manufacturer,
            "model": model,
            "primaryAbi": device_abi,
            "qemuRejected": True,
        },
        "payload": {
            "fileCount": len(entries),
            "treeSha256": tree_sha256,
            "runtimeLibraries": {
                relative: next(
                    entry["sha256"] for entry in entries if entry["path"] == relative
                )
                for relative in REQUIRED_LIBRARIES
            },
        },
        "testClass": TEST_CLASS,
        "testCases": cases,
        "testResultsSha256": sha256(results),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify exact KMediaVlc physical Android playback evidence."
    )
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--tested-commit", required=True)
    parser.add_argument("--device-abi", required=True)
    parser.add_argument("--api-level", type=int, required=True)
    parser.add_argument("--manufacturer", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--build-fingerprint", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.output.is_absolute() or args.output.exists() or not args.output.parent.is_dir():
        parser.error("output must be a new absolute file below an existing directory")
    try:
        report = verify_acceptance(
            payload=args.payload,
            results=args.results,
            tested_commit=args.tested_commit,
            device_abi=args.device_abi,
            api_level=args.api_level,
            manufacturer=args.manufacturer,
            model=args.model,
            build_fingerprint=args.build_fingerprint,
        )
    except ValueError as error:
        parser.error(str(error))
    with args.output.open("x", encoding="utf-8", newline="\n") as destination:
        json.dump(report, destination, indent=2, sort_keys=True)
        destination.write("\n")
    print(f"Verified physical Android playback evidence: {args.output}")


if __name__ == "__main__":
    main()
