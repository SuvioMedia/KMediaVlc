#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath


PINNED_REVISION = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
ARCHIVE_ROOT = PurePosixPath("corresponding-source")
TARGETS = ["linux-aarch64", "linux-x86_64", "macos-aarch64", "windows-x86_64"]
POLICIES = {
    "linux": "compliance/policy/linux-binary-components.json",
    "macos-aarch64": "compliance/policy/macos-aarch64-binary-components.json",
    "windows-x86_64": "compliance/policy/windows-x86_64-binary-components.json",
}
MAX_MEMBERS = 200_000
MAX_FILE_SIZE = 900 * 1024 * 1024


def fail(message: str) -> None:
    raise ValueError(message)


def digest_stream(stream: object) -> str:
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def safe_member(member: tarfile.TarInfo) -> PurePosixPath:
    name = member.name.rstrip("/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or not path.is_relative_to(ARCHIVE_ROOT)
    ):
        fail(f"Unsafe desktop corresponding-source member: {member.name!r}")
    if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
        fail(f"Desktop corresponding source contains a link or special file: {member.name!r}")
    if member.size < 0 or member.size > MAX_FILE_SIZE:
        fail(f"Desktop corresponding-source member is oversized: {member.name!r}")
    return path


def read_member(source: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> bytes:
    if not member.isfile() or member.size > limit:
        fail(f"Desktop corresponding-source metadata is invalid: {member.name}")
    stream = source.extractfile(member)
    if stream is None:
        fail(f"Cannot read desktop corresponding-source metadata: {member.name}")
    with stream:
        value = stream.read(limit + 1)
    if len(value) != member.size or len(value) > limit:
        fail(f"Desktop corresponding-source metadata is truncated: {member.name}")
    return value


def load_policies(root: Path) -> tuple[dict[str, str], dict[str, dict], list[str]]:
    statuses: dict[str, str] = {}
    components: dict[str, dict] = {}
    archives: set[str] = set()
    for platform, relative in POLICIES.items():
        policy = json.loads((root / relative).read_text(encoding="utf-8"))
        declared = policy.get("targets") if platform == "linux" else [policy.get("target")]
        expected = ["linux-x86_64", "linux-aarch64"] if platform == "linux" else [platform]
        platform_components = policy.get("components")
        if (
            policy.get("schemaVersion") != 1
            or policy.get("vlcRevision") != PINNED_REVISION
            or declared != expected
            or policy.get("reviewStatus") != "approved"
            or not isinstance(platform_components, dict)
            or not platform_components
        ):
            fail(f"Desktop source policy is not approved: {relative}")
        statuses[platform] = policy["reviewStatus"]
        for component_id, component in platform_components.items():
            previous = components.setdefault(component_id, component)
            if previous != component:
                fail(f"Desktop component terms disagree across platforms: {component_id}")
            archive = component.get("sourceArchive")
            if not isinstance(archive, str) or PurePosixPath(archive).name != archive:
                fail(f"Desktop component source archive is unsafe: {component_id}")
            archives.add(archive)
    return statuses, components, sorted(archives)


def parse_checksums(value: bytes) -> dict[str, str]:
    try:
        lines = value.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("Desktop contrib checksums are not ASCII.") from error
    result: dict[str, str] = {}
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            fail("Desktop contrib checksum line is malformed.")
        checksum, name = line[:64], line[66:]
        if (
            not SHA256.fullmatch(checksum)
            or PurePosixPath(name).name != name
            or name in result
        ):
            fail("Desktop contrib checksum entry is invalid or duplicated.")
        result[name] = checksum
    return result


def verify(root: Path, archive: Path, version: str, tested_commit: str) -> str:
    root = root.resolve(strict=True)
    archive = archive.resolve(strict=True)
    if archive.is_symlink() or not archive.is_file():
        fail("Desktop corresponding-source archive must be a real file.")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Desktop corresponding source requires immutable non-SNAPSHOT SemVer.")
    if not COMMIT.fullmatch(tested_commit):
        fail("Desktop corresponding source requires an exact tested commit.")
    statuses, components, required_archives = load_policies(root)

    with tarfile.open(archive, "r:gz") as source:
        raw_members = source.getmembers()
        if not raw_members or len(raw_members) > MAX_MEMBERS:
            fail("Desktop corresponding-source member count is invalid.")
        members: dict[PurePosixPath, tarfile.TarInfo] = {}
        for member in raw_members:
            path = safe_member(member)
            if path in members:
                fail(f"Desktop corresponding source contains a duplicate member: {path}")
            members[path] = member
        manifest_path = ARCHIVE_ROOT / "SOURCE-MANIFEST.json"
        checksum_path = ARCHIVE_ROOT / "SELECTED-CONTRIB-SHA256SUMS"
        for required in (
            manifest_path,
            checksum_path,
            ARCHIVE_ROOT / "kmediavlc/build.gradle.kts",
            ARCHIVE_ROOT / "vlc/meson.build",
            ARCHIVE_ROOT / "BUILD-TOOLCHAIN.txt",
            ARCHIVE_ROOT / "TOOLCHAIN-STATIC-ARCHIVES-SHA256SUMS",
        ):
            if required not in members or not members[required].isfile():
                fail(f"Desktop corresponding source omits required input: {required}")
        manifest = json.loads(read_member(source, members[manifest_path], 16 * 1024 * 1024))
        expected_identity = {
            "schemaVersion": 1,
            "target": "desktop-matrix",
            "targets": TARGETS,
            "releaseVersion": version,
            "testedCommit": tested_commit,
            "vlcRevision": PINNED_REVISION,
            "platformReviewStatus": statuses,
            "components": components,
        }
        for key, expected in expected_identity.items():
            if manifest.get(key) != expected:
                fail(f"Desktop corresponding-source manifest differs at {key}.")
        if not SHA256.fullmatch(str(manifest.get("baseWindowsSourceSha256", ""))):
            fail("Desktop corresponding-source manifest lacks its base source digest.")
        manifest_hashes = manifest.get("selectedContribSha256")
        if (
            not isinstance(manifest_hashes, dict)
            or list(manifest_hashes) != required_archives
            or any(not SHA256.fullmatch(str(value)) for value in manifest_hashes.values())
        ):
            fail("Desktop corresponding-source manifest has an invalid contrib inventory.")
        checksum_hashes = parse_checksums(
            read_member(source, members[checksum_path], 16 * 1024 * 1024)
        )
        if checksum_hashes != manifest_hashes:
            fail("Desktop corresponding-source checksum inventories disagree.")
        actual_contrib = {
            path.name: member
            for path, member in members.items()
            if member.isfile() and path.parent == ARCHIVE_ROOT / "contrib-tarballs"
        }
        if list(sorted(actual_contrib)) != required_archives:
            fail("Desktop corresponding-source contrib payload is incomplete or overbroad.")
        for name in required_archives:
            stream = source.extractfile(actual_contrib[name])
            if stream is None:
                fail(f"Cannot read desktop contrib source: {name}")
            with stream:
                actual = digest_stream(stream)
            if actual != manifest_hashes[name]:
                fail(f"Desktop contrib source digest mismatch: {name}")
    with archive.open("rb") as packaged:
        return digest_stream(packaged)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tested-commit", required=True)
    arguments = parser.parse_args()
    sha256 = verify(
        arguments.root,
        arguments.archive,
        arguments.version,
        arguments.tested_commit,
    )
    print(f"{sha256}  {arguments.archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
