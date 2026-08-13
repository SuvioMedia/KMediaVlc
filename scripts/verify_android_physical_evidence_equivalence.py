#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from verify_android_device_smoke_results import (
    PINNED_LIBVLCJNI_REVISION,
    PINNED_VLC_REVISION,
    REQUIRED_LIBRARIES,
    TEST_CASES,
    TEST_CLASS,
    parse_closed_properties,
    payload_tree,
    sha256,
    verify_junit,
)


POLICY_PATH = Path("compliance/policy/android-retained-physical-evidence.json")
EXPECTED_POLICY_KEYS = {
    "schemaVersion",
    "executionCommit",
    "vlcRevision",
    "evidence",
    "runtimeLibraries",
    "behaviorPaths",
}
EXPECTED_EVIDENCE_KEYS = {
    "acceptancePath",
    "acceptanceSha256",
    "junitBase64Path",
    "junitBase64Sha256",
    "junitDecodedSha256",
}
EXPECTED_ACCEPTANCE_KEYS = {
    "schemaVersion",
    "kmediaVlcCommit",
    "vlcRevision",
    "libvlcjniRevision",
    "physicalDevice",
    "payload",
    "testClass",
    "testCases",
    "testResultsSha256",
}
EXPECTED_DEVICE_KEYS = {
    "apiLevel",
    "buildFingerprint",
    "gradleDescription",
    "manufacturer",
    "model",
    "primaryAbi",
    "qemuRejected",
}
EXPECTED_PAYLOAD_KEYS = {"fileCount", "treeSha256", "runtimeLibraries"}
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


def fail(message: str) -> None:
    raise ValueError(message)


def read_json(path: Path, description: str) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size <= 0:
        fail(f"{description} must be a real non-empty file.")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        fail(f"{description} is unreadable: {error}")
    if not isinstance(value, dict):
        fail(f"{description} must be a JSON object.")
    return value


def safe_repo_file(root: Path, relative: str, description: str) -> Path:
    try:
        parsed = PurePosixPath(relative)
    except TypeError:
        fail(f"{description} path is invalid.")
    if (
        parsed.is_absolute()
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != relative
    ):
        fail(f"{description} path is unsafe.")
    candidate = root.joinpath(*parsed.parts)
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size <= 0:
        fail(f"{description} must be a real non-empty repository file.")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError:
        fail(f"{description} escaped the repository.")
    return resolved


def git(root: Path, arguments: list[str], *, allow_failure: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and not allow_failure:
        fail("The Android evidence verifier could not inspect repository history.")
    return result


def validate_history(root: Path, execution_commit: str, release_commit: str, paths: list[str]) -> None:
    if not COMMIT.fullmatch(execution_commit) or not COMMIT.fullmatch(release_commit):
        fail("Android execution and release commits must be exact lowercase commits.")
    head = git(root, ["rev-parse", "HEAD"]).stdout.strip()
    if head != release_commit:
        fail("Android equivalence must verify the exact checked-out release commit.")
    git(root, ["cat-file", "-e", f"{execution_commit}^{{commit}}"])
    if git(root, ["merge-base", "--is-ancestor", execution_commit, release_commit], allow_failure=True).returncode != 0:
        fail("The physical Android execution commit is not an ancestor of the release commit.")
    if not isinstance(paths, list) or paths != sorted(set(paths)) or not paths:
        fail("Android behavior paths must be a non-empty canonical list.")
    for value in paths:
        parsed = PurePosixPath(value) if isinstance(value, str) else PurePosixPath()
        if (
            not value
            or parsed.is_absolute()
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or parsed.as_posix() != value
        ):
            fail("Android behavior policy contains an unsafe path.")
    diff = git(
        root,
        ["diff", "--quiet", execution_commit, release_commit, "--", *paths],
        allow_failure=True,
    )
    if diff.returncode not in {0, 1}:
        fail("The Android behavior-path comparison could not complete.")
    if diff.returncode == 1:
        fail("Android playback behavior changed after the physical-device execution.")


def decode_junit(source: Path) -> bytes:
    try:
        encoded = source.read_bytes()
        if not encoded.endswith(b"\n") or b"\n" in encoded[:-1] or b"\r" in encoded:
            fail("Retained Android JUnit base64 must be one canonical line.")
        decoded = base64.b64decode(encoded[:-1], validate=True)
    except (OSError, binascii.Error) as error:
        fail(f"Retained Android JUnit evidence is not valid base64: {error}")
    if not decoded:
        fail("Retained Android JUnit evidence is empty.")
    return decoded


def verify(
    *, root: Path, payload: Path, release_commit: str, output: Path
) -> dict[str, object]:
    root = root.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        fail("KMediaVlc root must be a real directory.")
    policy_path = safe_repo_file(root, POLICY_PATH.as_posix(), "Android evidence policy")
    policy = read_json(policy_path, "Android evidence policy")
    if set(policy) != EXPECTED_POLICY_KEYS or policy.get("schemaVersion") != 1:
        fail("Android evidence policy schema is not closed.")

    evidence = policy.get("evidence")
    libraries = policy.get("runtimeLibraries")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != EXPECTED_EVIDENCE_KEYS
        or not isinstance(libraries, dict)
        or tuple(libraries) != REQUIRED_LIBRARIES
        or any(not SHA256.fullmatch(str(value)) for value in libraries.values())
    ):
        fail("Android retained evidence identity is invalid.")
    if any(not SHA256.fullmatch(str(evidence[key])) for key in (
        "acceptanceSha256", "junitBase64Sha256", "junitDecodedSha256"
    )):
        fail("Android retained evidence has an invalid digest.")

    execution_commit = policy.get("executionCommit")
    evidence_vlc_revision = policy.get("vlcRevision")
    behavior_paths = policy.get("behaviorPaths")
    if not COMMIT.fullmatch(str(evidence_vlc_revision)):
        fail("Android retained evidence VLC revision is invalid.")
    validate_history(root, execution_commit, release_commit, behavior_paths)

    acceptance_path = safe_repo_file(root, evidence["acceptancePath"], "Android acceptance evidence")
    junit_path = safe_repo_file(root, evidence["junitBase64Path"], "Android JUnit evidence")
    if sha256(acceptance_path) != evidence["acceptanceSha256"]:
        fail("Retained Android acceptance digest changed.")
    if sha256(junit_path) != evidence["junitBase64Sha256"]:
        fail("Retained Android JUnit base64 digest changed.")
    junit = decode_junit(junit_path)
    junit_sha256 = hashlib.sha256(junit).hexdigest()
    if junit_sha256 != evidence["junitDecodedSha256"]:
        fail("Retained Android JUnit decoded digest changed.")
    with tempfile.TemporaryDirectory(prefix="kmediavlc-android-evidence-") as temporary:
        decoded_results = Path(temporary) / "test-results.xml"
        decoded_results.write_bytes(junit)
        device_description, test_cases = verify_junit(decoded_results)

    acceptance = read_json(acceptance_path, "Android acceptance evidence")
    device = acceptance.get("physicalDevice")
    accepted_payload = acceptance.get("payload")
    if (
        set(acceptance) != EXPECTED_ACCEPTANCE_KEYS
        or acceptance.get("schemaVersion") != 1
        or acceptance.get("kmediaVlcCommit") != execution_commit
        or acceptance.get("vlcRevision") != evidence_vlc_revision
        or acceptance.get("libvlcjniRevision") != PINNED_LIBVLCJNI_REVISION
        or acceptance.get("testClass") != TEST_CLASS
        or acceptance.get("testCases") != list(TEST_CASES)
        or acceptance.get("testResultsSha256") != junit_sha256
        or not isinstance(device, dict)
        or set(device) != EXPECTED_DEVICE_KEYS
        or device.get("gradleDescription") != device_description
        or device.get("qemuRejected") is not True
        or not isinstance(accepted_payload, dict)
        or set(accepted_payload) != EXPECTED_PAYLOAD_KEYS
        or accepted_payload.get("runtimeLibraries") != libraries
    ):
        fail("Retained physical Android acceptance is inconsistent or incomplete.")
    if test_cases != list(TEST_CASES):
        fail("Retained Android JUnit test order is not canonical.")

    entries, current_tree_sha256 = payload_tree(payload)
    current_by_path = {str(entry["path"]): str(entry["sha256"]) for entry in entries}
    current_libraries = {relative: current_by_path.get(relative) for relative in REQUIRED_LIBRARIES}
    if current_libraries != libraries:
        fail("Release Android runtime libraries differ from the physically tested binaries.")
    runtime_manifest = parse_closed_properties(payload / "android-runtime.properties")
    release_eligible = runtime_manifest.get("releaseEligible") == "true"
    legal = read_json(payload / "legal/android-static-legal.json", "Android legal manifest")
    legal_status = legal.get("reviewStatus")
    effective_license = legal.get("effectiveLicenseSpdx")
    approved_legal = (
        legal_status == "approved"
        and isinstance(effective_license, str)
        and bool(effective_license.strip())
    )
    candidate_legal = (
        legal_status == "candidate-linked-member-review-pending"
        and effective_license is None
    )
    if not (approved_legal or candidate_legal) or release_eligible != approved_legal:
        fail("Android payload release eligibility and legal review state disagree.")

    report: dict[str, object] = {
        "schemaVersion": 1,
        "executionCommit": execution_commit,
        "releaseCommit": release_commit,
        "behaviorPathsUnchanged": True,
        "acceptanceSha256": evidence["acceptanceSha256"],
        "testResultsSha256": junit_sha256,
        "physicalDevice": device,
        "runtimeLibraries": current_libraries,
        "releasePayloadTreeSha256": current_tree_sha256,
        "releaseEligible": release_eligible,
        "legalReviewStatus": legal_status,
    }
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        fail("Android equivalence output must be a new file under an existing directory.")
    with output.open("x", encoding="utf-8", newline="\n") as destination:
        json.dump(report, destination, indent=2, sort_keys=True)
        destination.write("\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bind fresh retained physical Android evidence to release binaries."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify(
            root=args.root,
            payload=args.payload,
            release_commit=args.release_commit,
            output=args.output,
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(
        "Verified retained physical Android evidence against release commit "
        f"{report['releaseCommit']}."
    )


if __name__ == "__main__":
    main()
