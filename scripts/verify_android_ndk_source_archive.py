#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Independently verify the Android NDK runtime source package against Git."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import tarfile
from pathlib import Path, PurePosixPath


NDK_REVISION = "29.0.14206865"
POLICY_PATH = PurePosixPath("compliance/policy/android-static-components.json")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
SOURCE_IDS = ("llvm-android-build", "llvm-project")
MIN_EPOCH = 315532800
MAX_MEMBERS = 100_000
MAX_FILE_SIZE = 128 * 1024 * 1024
MAX_MANIFEST_SIZE = 64 * 1024 * 1024


def fail(message: str) -> None:
    raise ValueError(message)


def digest_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                return value.hexdigest()
            value.update(block)


def real_file(path: Path, description: str) -> Path:
    if path.is_symlink():
        fail(f"{description} must not be symbolic.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{description} is missing.") from error
    if not resolved.is_file():
        fail(f"{description} must be a regular file.")
    return resolved


def real_directory(path: Path, description: str) -> Path:
    if path.is_symlink():
        fail(f"{description} must not be symbolic.")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"{description} is missing.") from error
    if not resolved.is_dir():
        fail(f"{description} must be a directory.")
    return resolved


def safe_repo_path(value: str, description: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        fail(f"{description} is unsafe: {value!r}")
    return path


def load_policy(root: Path) -> tuple[Path, dict, dict, dict]:
    policy_path = real_file(root.joinpath(*POLICY_PATH.parts), "Android static component policy")
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Android static component policy is unreadable.") from error
    if (
        policy.get("schemaVersion") != 1
        or policy.get("target") != "android-arm"
        or policy.get("ndkRevision") != NDK_REVISION
    ):
        fail("Android NDK source policy identity is unsupported.")
    source_inputs = policy.get("ndkSourceInputs")
    package_policy = policy.get("ndkSourcePackage")
    if (
        not isinstance(source_inputs, dict)
        or tuple(source_inputs) != SOURCE_IDS
        or not isinstance(package_policy, dict)
        or set(package_policy)
        != {"archiveRoot", "format", "verifiedSourceStatus", "sources"}
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]+", str(package_policy.get("archiveRoot")))
        or package_policy.get("format") != "deterministic-tar-gzip-v1"
        or package_policy.get("verifiedSourceStatus") != "corresponding-source-mapped"
    ):
        fail("Android NDK source package policy is incomplete.")
    package_sources = package_policy.get("sources")
    if not isinstance(package_sources, dict) or tuple(package_sources) != SOURCE_IDS:
        fail("Android NDK source package does not close both Git inputs.")
    for source_id in SOURCE_IDS:
        source = source_inputs[source_id]
        selection = package_sources[source_id]
        if (
            not isinstance(source, dict)
            or set(source) != {"repository", "revision", "tree", "role", "requiredPaths"}
            or not isinstance(source.get("repository"), str)
            or not COMMIT.fullmatch(str(source.get("revision")))
            or not COMMIT.fullmatch(str(source.get("tree")))
            or not isinstance(source.get("role"), str)
            or not isinstance(source.get("requiredPaths"), list)
            or not source["requiredPaths"]
            or not isinstance(selection, dict)
            or set(selection) != {"scope", "paths"}
        ):
            fail(f"Android NDK source identity is incomplete: {source_id}")
        required_paths = source["requiredPaths"]
        if required_paths != sorted(set(required_paths)):
            fail(f"Android NDK required source paths are not canonical: {source_id}")
        for value in required_paths:
            if not isinstance(value, str):
                fail(f"Android NDK required source path is invalid: {source_id}")
            safe_repo_path(value, "Android NDK required source path")
        scope = selection.get("scope")
        paths = selection.get("paths")
        if scope == "complete-tree":
            if paths != []:
                fail(f"Complete Android NDK source tree must not list paths: {source_id}")
        elif scope == "selected-subtrees":
            if (
                not isinstance(paths, list)
                or not paths
                or paths != sorted(set(paths))
                or any(not isinstance(value, str) for value in paths)
            ):
                fail(f"Android NDK selected source paths are not canonical: {source_id}")
            selected = [safe_repo_path(value, "Android NDK package source path") for value in paths]
            for required in required_paths:
                required_path = PurePosixPath(required)
                if not any(
                    required_path == candidate or required_path.is_relative_to(candidate)
                    for candidate in selected
                ):
                    fail(f"Android NDK package omits required path: {source_id}/{required}")
        else:
            fail(f"Android NDK source package scope is unsupported: {source_id}")
    archive_sources = policy.get("ndkArchiveSourcePaths")
    if not isinstance(archive_sources, dict) or not archive_sources:
        fail("Android NDK archive-to-source map is missing.")
    for paths in archive_sources.values():
        if not isinstance(paths, list) or not paths:
            fail("Android NDK archive-to-source map is invalid.")
        for value in paths:
            if not isinstance(value, str):
                fail("Android NDK archive source path is invalid.")
            source_id, separator, relative = value.partition("/")
            if not separator or source_id not in source_inputs:
                fail(f"Android NDK archive source path has no source input: {value}")
            safe_repo_path(relative, "Android NDK archive source path")
            selection = package_sources[source_id]
            if selection["scope"] == "selected-subtrees":
                relative_path = PurePosixPath(relative)
                if not any(
                    relative_path == PurePosixPath(candidate)
                    or relative_path.is_relative_to(PurePosixPath(candidate))
                    for candidate in selection["paths"]
                ):
                    fail(f"Android NDK package omits archive source path: {value}")
    return policy_path, policy, source_inputs, package_policy


def git_output(checkout: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise ValueError("Git is required to verify Android NDK sources.") from error
    if result.returncode != 0:
        fail("Android NDK source checkout could not be inspected with Git.")
    return result.stdout


def expected_git_files(
    source_id: str, checkout: Path, source_policy: dict, selection: dict, root_name: str
) -> list[dict]:
    checkout = real_directory(checkout, f"Android NDK source checkout {source_id}")
    try:
        revision = git_output(checkout, "rev-parse", "HEAD").decode("ascii").strip()
        tree = git_output(checkout, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError("Android NDK Git identity is not ASCII.") from error
    if revision != source_policy["revision"] or tree != source_policy["tree"]:
        fail(f"Android NDK source checkout differs from the pinned Git identity: {source_id}")
    if git_output(checkout, "status", "--porcelain", "--untracked-files=no"):
        fail(f"Android NDK source checkout has tracked modifications: {source_id}")
    arguments = ["ls-tree", "-r", "-z", "--full-tree", "HEAD"]
    if selection["scope"] == "selected-subtrees":
        arguments.extend(["--", *selection["paths"]])
    raw = git_output(checkout, *arguments)
    values: list[dict] = []
    seen: set[str] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, git_blob = metadata.decode("ascii").split(" ")
            source_path = encoded_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("Android NDK Git tree entry is malformed.") from error
        if (
            mode not in {"100644", "100755"}
            or object_type != "blob"
            or not COMMIT.fullmatch(git_blob)
            or source_path in seen
        ):
            fail(f"Android NDK Git tree entry is unsupported: {source_id}")
        relative = safe_repo_path(source_path, "Android NDK Git source path")
        values.append(
            {
                "path": (
                    PurePosixPath(root_name) / source_id / relative
                ).as_posix(),
                "sourceId": source_id,
                "sourcePath": source_path,
                "gitMode": mode,
                "gitBlob": git_blob,
            }
        )
        seen.add(source_path)
    if not values or values != sorted(values, key=lambda value: value["path"]):
        fail(f"Android NDK Git source inventory is empty or non-canonical: {source_id}")
    if selection["scope"] == "selected-subtrees":
        for selected in selection["paths"]:
            selected_path = PurePosixPath(selected)
            if not any(
                PurePosixPath(value["sourcePath"]) == selected_path
                or PurePosixPath(value["sourcePath"]).is_relative_to(selected_path)
                for value in values
            ):
                fail(f"Android NDK package source path is missing: {source_id}/{selected}")
    for required in source_policy["requiredPaths"]:
        required_path = PurePosixPath(required)
        if not any(
            PurePosixPath(value["sourcePath"]) == required_path
            or PurePosixPath(value["sourcePath"]).is_relative_to(required_path)
            for value in values
        ):
            fail(f"Android NDK checkout omits required source: {source_id}/{required}")
    return values


def safe_member(member: tarfile.TarInfo, archive_root: PurePosixPath) -> PurePosixPath:
    name = member.name.rstrip("/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in path.parts)
        or not (path == archive_root or path.is_relative_to(archive_root))
    ):
        fail(f"Unsafe Android NDK source member: {member.name!r}")
    if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
        fail(f"Android NDK source contains a link or special file: {member.name!r}")
    if member.size < 0 or member.size > MAX_FILE_SIZE:
        fail(f"Android NDK source member is oversized: {member.name!r}")
    return path


def read_member(source: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> bytes:
    if not member.isfile() or member.size > limit:
        fail(f"Android NDK source metadata is invalid: {member.name}")
    stream = source.extractfile(member)
    if stream is None:
        fail(f"Android NDK source metadata is unreadable: {member.name}")
    with stream:
        data = stream.read(limit + 1)
    if len(data) != member.size or len(data) > limit:
        fail(f"Android NDK source metadata is truncated: {member.name}")
    return data


def stream_hashes(source: tarfile.TarFile, member: tarfile.TarInfo) -> tuple[str, str]:
    stream = source.extractfile(member)
    if stream is None:
        fail(f"Android NDK source member is unreadable: {member.name}")
    sha256 = hashlib.sha256()
    git_blob = hashlib.sha1(f"blob {member.size}\0".encode("ascii"))
    count = 0
    with stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            count += len(block)
            sha256.update(block)
            git_blob.update(block)
    if count != member.size:
        fail(f"Android NDK source member is truncated: {member.name}")
    return sha256.hexdigest(), git_blob.hexdigest()


def verify(
    root: Path,
    archive: Path,
    llvm_project: Path,
    llvm_android: Path,
    version: str,
    tested_commit: str,
) -> str:
    root = real_directory(root, "KMediaVlc source root")
    archive = real_file(archive, "Android NDK source archive")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Android NDK source verification requires immutable non-SNAPSHOT SemVer.")
    if not COMMIT.fullmatch(tested_commit):
        fail("Android NDK source verification requires an exact tested KMediaVlc commit.")
    policy_path, policy, source_inputs, package_policy = load_policy(root)
    archive_root = PurePosixPath(package_policy["archiveRoot"])
    checkouts = {
        "llvm-android-build": llvm_android,
        "llvm-project": llvm_project,
    }
    expected_files: list[dict] = []
    for source_id in SOURCE_IDS:
        expected_files.extend(
            expected_git_files(
                source_id,
                checkouts[source_id],
                source_inputs[source_id],
                package_policy["sources"][source_id],
                package_policy["archiveRoot"],
            )
        )
    expected_files.sort(key=lambda value: value["path"])
    manifest_path = archive_root / "SOURCE-MANIFEST.json"
    with archive.open("rb") as raw:
        gzip_header = raw.read(10)
    if (
        len(gzip_header) != 10
        or gzip_header[:4] != b"\x1f\x8b\x08\x00"
    ):
        fail("Android NDK source archive has a non-deterministic gzip header.")
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            fail("Android NDK source archive member count is invalid.")
        by_path: dict[PurePosixPath, tarfile.TarInfo] = {}
        for member in members:
            path = safe_member(member, archive_root)
            if path in by_path:
                fail(f"Duplicate Android NDK source member: {path}")
            by_path[path] = member
        if manifest_path not in by_path:
            fail("Android NDK source archive omits its manifest.")
        try:
            manifest = json.loads(
                read_member(source, by_path[manifest_path], MAX_MANIFEST_SIZE)
            )
        except json.JSONDecodeError as error:
            raise ValueError("Android NDK source manifest is invalid JSON.") from error
        expected_manifest_keys = {
            "schemaVersion",
            "target",
            "releaseVersion",
            "testedCommit",
            "sourceDateEpoch",
            "ndkRevision",
            "verifiedSourceStatus",
            "staticComponentPolicy",
            "sourceInputs",
            "sourcePackagePolicy",
            "ndkReleaseProvenance",
            "ndkArchiveSourcePaths",
            "files",
        }
        if set(manifest) != expected_manifest_keys:
            fail("Android NDK source manifest fields are not closed.")
        epoch = manifest.get("sourceDateEpoch")
        if (
            manifest.get("schemaVersion") != 1
            or manifest.get("target") != "android-ndk-r29-runtime-source"
            or manifest.get("releaseVersion") != version
            or manifest.get("testedCommit") != tested_commit
            or not isinstance(epoch, int)
            or epoch < MIN_EPOCH
            or manifest.get("ndkRevision") != NDK_REVISION
            or manifest.get("verifiedSourceStatus")
            != package_policy["verifiedSourceStatus"]
            or manifest.get("staticComponentPolicy")
            != {"path": POLICY_PATH.as_posix(), "sha256": digest_file(policy_path)}
            or manifest.get("sourceInputs")
            != [{"id": source_id, **source_inputs[source_id]} for source_id in SOURCE_IDS]
            or manifest.get("sourcePackagePolicy") != package_policy
            or manifest.get("ndkReleaseProvenance") != policy.get("ndkReleaseProvenance")
            or manifest.get("ndkArchiveSourcePaths") != policy.get("ndkArchiveSourcePaths")
        ):
            fail("Android NDK source manifest does not match the pinned release identity.")
        if struct.unpack("<I", gzip_header[4:8])[0] != epoch:
            fail("Android NDK source gzip timestamp differs from its manifest.")
        records = manifest.get("files")
        if (
            not isinstance(records, list)
            or records != sorted(records, key=lambda value: value.get("path", ""))
            or len(records) != len(expected_files)
        ):
            fail("Android NDK source manifest file inventory is not canonical.")
        expected_by_path = {value["path"]: value for value in expected_files}
        record_by_path: dict[str, dict] = {}
        for record in records:
            if not isinstance(record, dict) or set(record) != {
                "path",
                "sourceId",
                "sourcePath",
                "gitMode",
                "gitBlob",
                "sha256",
                "size",
            }:
                fail("Android NDK source manifest file entry is invalid.")
            path = record.get("path")
            expected = expected_by_path.get(path)
            identity = {
                key: record.get(key)
                for key in ("path", "sourceId", "sourcePath", "gitMode", "gitBlob")
            }
            if (
                expected is None
                or identity != expected
                or path in record_by_path
                or not SHA256.fullmatch(str(record.get("sha256")))
                or not isinstance(record.get("size"), int)
                or not 0 <= record["size"] <= MAX_FILE_SIZE
            ):
                fail("Android NDK source manifest differs from the exact Git objects.")
            record_by_path[path] = record
        expected_regular_paths = {PurePosixPath(value) for value in record_by_path} | {
            manifest_path
        }
        actual_regular_paths = {path for path, member in by_path.items() if member.isfile()}
        if actual_regular_paths != expected_regular_paths:
            fail("Android NDK source archive file closure differs from its manifest.")
        expected_directories = {archive_root}
        for path in expected_regular_paths:
            expected_directories.update(parent for parent in path.parents if parent.parts)
        actual_directories = {path for path, member in by_path.items() if member.isdir()}
        if actual_directories != expected_directories:
            fail("Android NDK source archive directory closure is not canonical.")
        for path, member in by_path.items():
            if (
                member.uid != 0
                or member.gid != 0
                or member.uname != ""
                or member.gname != ""
                or member.mtime != epoch
            ):
                fail(f"Android NDK source member metadata is not reproducible: {path}")
            if member.isdir():
                if member.mode != 0o755 or member.size != 0:
                    fail(f"Android NDK source directory metadata is invalid: {path}")
                continue
            if path == manifest_path:
                if member.mode != 0o644:
                    fail("Android NDK source manifest mode is invalid.")
                continue
            record = record_by_path[path.as_posix()]
            expected_mode = 0o755 if record["gitMode"] == "100755" else 0o644
            if member.mode != expected_mode or member.size != record["size"]:
                fail(f"Android NDK source member mode or size differs: {path}")
            sha256, git_blob = stream_hashes(source, member)
            if sha256 != record["sha256"] or git_blob != record["gitBlob"]:
                fail(f"Android NDK source member differs from its Git object: {path}")
    return digest_file(archive)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--llvm-project", type=Path, required=True)
    parser.add_argument("--llvm-android", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tested-commit", required=True)
    arguments = parser.parse_args()
    digest = verify(
        arguments.root,
        arguments.archive,
        arguments.llvm_project,
        arguments.llvm_android,
        arguments.version,
        arguments.tested_commit,
    )
    print(f"{digest}  {arguments.archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
