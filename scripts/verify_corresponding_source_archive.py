#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Verify the immutable corresponding-source asset without extracting it."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath


PINNED_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
PINNED_TOOLCHAIN = "registry.videolan.org/vlc-debian-llvm-ucrt:20260611225331"
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
ROOT = PurePosixPath("corresponding-source")
MAX_MEMBERS = 200_000
MAX_FILE_SIZE = 900 * 1024 * 1024


def fail(message: str) -> None:
    raise ValueError(message)


def digest_stream(stream: object) -> str:
    value = hashlib.sha256()
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            return value.hexdigest()
        value.update(block)


def safe_member(member: tarfile.TarInfo) -> PurePosixPath:
    name = member.name.rstrip("/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or not path.is_relative_to(ROOT)
    ):
        fail(f"Unsafe corresponding-source member: {member.name!r}")
    if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
        fail(f"Corresponding source contains a link or special file: {member.name!r}")
    if member.size < 0 or member.size > MAX_FILE_SIZE:
        fail(f"Corresponding-source member is oversized: {member.name!r}")
    return path


def read_member(source: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> bytes:
    if not member.isfile() or member.size > limit:
        fail(f"Corresponding-source metadata is invalid: {member.name}")
    stream = source.extractfile(member)
    if stream is None:
        fail(f"Could not read corresponding-source metadata: {member.name}")
    with stream:
        data = stream.read(limit + 1)
    if len(data) != member.size or len(data) > limit:
        fail(f"Corresponding-source metadata is truncated or oversized: {member.name}")
    return data


def load_policy(root: Path) -> dict:
    path = root / "compliance/policy/windows-x86_64-binary-components.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    if (
        policy.get("schemaVersion") != 1
        or policy.get("target") != "windows-x86_64"
        or policy.get("vlcRevision") != PINNED_REVISION
        or policy.get("toolchainImage") != PINNED_TOOLCHAIN
        or policy.get("reviewStatus") != "approved"
        or not isinstance(policy.get("components"), dict)
        or not policy["components"]
    ):
        fail("Windows binary component policy is not approved for release.")
    return policy


def parse_checksum_file(data: bytes) -> dict[str, str]:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("Selected contrib checksum file is not ASCII.") from error
    values: dict[str, str] = {}
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            fail("Selected contrib checksum line is malformed.")
        checksum, name = line[:64], line[66:]
        path = PurePosixPath(name)
        if (
            not SHA256.fullmatch(checksum)
            or name in values
            or path.name != name
            or name in {"", ".", ".."}
        ):
            fail("Selected contrib checksum entry is invalid or duplicated.")
        values[name] = checksum
    return values


def verify(root: Path, archive: Path, version: str, tested_commit: str) -> str:
    root = root.resolve(strict=True)
    archive = archive.resolve(strict=True)
    if archive.is_symlink() or not archive.is_file():
        fail("Corresponding-source archive must be a real file.")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Corresponding source requires immutable non-SNAPSHOT SemVer.")
    if not COMMIT.fullmatch(tested_commit):
        fail("Corresponding source requires an exact tested KMediaVlc commit.")
    policy = load_policy(root)
    selected_archives = sorted(
        {component.get("sourceArchive") for component in policy["components"].values()}
    )
    if any(
        not isinstance(name, str) or PurePosixPath(name).name != name
        for name in selected_archives
    ):
        fail("Windows binary component policy contains an unsafe source archive.")

    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            fail("Corresponding-source archive member count is invalid.")
        by_path: dict[PurePosixPath, tarfile.TarInfo] = {}
        for member in members:
            path = safe_member(member)
            if path in by_path:
                fail(f"Duplicate corresponding-source member: {path}")
            by_path[path] = member

        manifest_path = ROOT / "SOURCE-MANIFEST.json"
        checksum_path = ROOT / "SELECTED-CONTRIB-SHA256SUMS"
        required = (
            manifest_path,
            checksum_path,
            ROOT / "BUILD-TOOLCHAIN.txt",
            ROOT / "TOOLCHAIN-STATIC-ARCHIVES-SHA256SUMS",
            ROOT / "kmediavlc" / "build.gradle.kts",
            ROOT / "vlc" / "meson.build",
        )
        for path in required:
            if path not in by_path or not by_path[path].isfile():
                fail(f"Corresponding source omits required input: {path}")
        toolchain_license_paths = {
            path
            for path, member in by_path.items()
            if member.isfile() and path.is_relative_to(ROOT / "toolchain-licenses")
        }
        if not toolchain_license_paths:
            fail("Corresponding source omits pinned toolchain licenses.")
        toolchain_sums = read_member(
            source,
            by_path[ROOT / "TOOLCHAIN-STATIC-ARCHIVES-SHA256SUMS"],
            16 * 1024 * 1024,
        )
        try:
            toolchain_lines = toolchain_sums.decode("ascii").splitlines()
        except UnicodeDecodeError as error:
            raise ValueError("Toolchain archive checksums are not ASCII.") from error
        if not toolchain_lines or any(
            len(line) < 67
            or line[64:66] != "  "
            or not SHA256.fullmatch(line[:64])
            or not line[66:].startswith("/opt/llvm-mingw/")
            for line in toolchain_lines
        ):
            fail("Toolchain static archive checksum inventory is invalid.")

        manifest = json.loads(read_member(source, by_path[manifest_path], 16 * 1024 * 1024))
        expected_identity = {
            "schemaVersion": 1,
            "target": policy["target"],
            "releaseVersion": version,
            "testedCommit": tested_commit,
            "vlcRevision": policy["vlcRevision"],
            "toolchainImage": policy["toolchainImage"],
            "componentReviewStatus": "approved",
            "components": policy["components"],
        }
        for key, expected in expected_identity.items():
            if manifest.get(key) != expected:
                fail(f"Corresponding-source manifest does not match approved {key}.")

        manifest_hashes = manifest.get("selectedContribSha256")
        if not isinstance(manifest_hashes, dict) or set(manifest_hashes) != set(selected_archives):
            fail("Corresponding-source manifest has an incomplete contrib inventory.")
        if any(not isinstance(value, str) or not SHA256.fullmatch(value) for value in manifest_hashes.values()):
            fail("Corresponding-source manifest has an invalid contrib digest.")
        checksum_hashes = parse_checksum_file(read_member(source, by_path[checksum_path], 16 * 1024 * 1024))
        if checksum_hashes != manifest_hashes:
            fail("Corresponding-source contrib checksum inventories disagree.")

        expected_contrib_paths = {
            ROOT / "contrib-tarballs" / name for name in selected_archives
        }
        actual_contrib_paths = {
            path
            for path, member in by_path.items()
            if member.isfile()
            and path.parent == ROOT / "contrib-tarballs"
        }
        if actual_contrib_paths != expected_contrib_paths:
            fail("Corresponding-source contrib payload differs from the approved inventory.")
        for path in sorted(expected_contrib_paths):
            stream = source.extractfile(by_path[path])
            if stream is None:
                fail(f"Could not read selected contrib source: {path.name}")
            with stream:
                actual = digest_stream(stream)
            if actual != manifest_hashes[path.name]:
                fail(f"Selected contrib source digest mismatch: {path.name}")

    with archive.open("rb") as packaged:
        return digest_stream(packaged)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tested-commit", required=True)
    arguments = parser.parse_args()
    sha256 = verify(arguments.root, arguments.archive, arguments.version, arguments.tested_commit)
    print(f"{sha256}  {arguments.archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
