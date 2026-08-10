#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministically package the exact Android NDK runtime source closure."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import NamedTuple


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
MAX_FILE_SIZE = 128 * 1024 * 1024


class SourceFile(NamedTuple):
    source_id: str
    source_path: str
    checkout_path: Path
    git_mode: str
    git_blob: str

    @property
    def archive_path(self) -> PurePosixPath:
        return PurePosixPath(self.source_id) / self.source_path


def fail(message: str) -> None:
    raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                return digest.hexdigest()
            digest.update(block)


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
        raise ValueError("Git is required to package Android NDK sources.") from error
    if result.returncode != 0:
        fail("Android NDK source checkout could not be inspected with Git.")
    return result.stdout


def inspect_checkout(
    source_id: str, checkout: Path, source_policy: dict, selection: dict
) -> list[SourceFile]:
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
    entries: list[SourceFile] = []
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
        source_file = real_file(
            checkout.joinpath(*relative.parts), f"Android NDK tracked source {source_id}/{source_path}"
        )
        try:
            source_file.relative_to(checkout)
        except ValueError:
            fail(f"Android NDK tracked source escaped its checkout: {source_id}/{source_path}")
        entries.append(SourceFile(source_id, source_path, source_file, mode, git_blob))
        seen.add(source_path)
    if not entries or entries != sorted(entries, key=lambda entry: entry.source_path):
        fail(f"Android NDK source checkout inventory is empty or non-canonical: {source_id}")
    if selection["scope"] == "selected-subtrees":
        for selected in selection["paths"]:
            selected_path = PurePosixPath(selected)
            if not any(
                PurePosixPath(entry.source_path) == selected_path
                or PurePosixPath(entry.source_path).is_relative_to(selected_path)
                for entry in entries
            ):
                fail(f"Android NDK package source path is missing: {source_id}/{selected}")
    required_paths = source_policy["requiredPaths"]
    for required in required_paths:
        required_path = PurePosixPath(required)
        if not any(
            PurePosixPath(entry.source_path) == required_path
            or PurePosixPath(entry.source_path).is_relative_to(required_path)
            for entry in entries
        ):
            fail(f"Android NDK checkout omits required source: {source_id}/{required}")
    return entries


def source_record(root_name: str, entry: SourceFile) -> dict:
    expected_size = entry.checkout_path.stat().st_size
    if expected_size > MAX_FILE_SIZE:
        fail(f"Android NDK source file is oversized: {entry.source_id}/{entry.source_path}")
    value = entry.checkout_path.read_bytes()
    if len(value) != expected_size:
        fail(f"Android NDK source file changed while reading: {entry.source_id}/{entry.source_path}")
    git_digest = hashlib.sha1(f"blob {len(value)}\0".encode("ascii"))
    git_digest.update(value)
    if git_digest.hexdigest() != entry.git_blob:
        fail(f"Android NDK source bytes differ from the pinned Git blob: {entry.source_id}/{entry.source_path}")
    return {
        "path": (PurePosixPath(root_name) / entry.archive_path).as_posix(),
        "sourceId": entry.source_id,
        "sourcePath": entry.source_path,
        "gitMode": entry.git_mode,
        "gitBlob": entry.git_blob,
        "sha256": hashlib.sha256(value).hexdigest(),
        "size": len(value),
    }


def tar_info(
    path: PurePosixPath, epoch: int, *, directory: bool = False, size: int = 0, mode: int = 0o644
) -> tarfile.TarInfo:
    info = tarfile.TarInfo(path.as_posix() + ("/" if directory else ""))
    info.mtime = epoch
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o755 if directory else mode
    info.size = 0 if directory else size
    if directory:
        info.type = tarfile.DIRTYPE
    return info


def package(
    root: Path,
    llvm_project: Path,
    llvm_android: Path,
    output: Path,
    tested_commit: str,
    version: str,
    epoch: int,
) -> str:
    root = real_directory(root, "KMediaVlc source root")
    output = output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        fail("Android NDK source output must be a new file in a real directory.")
    if not COMMIT.fullmatch(tested_commit):
        fail("Android NDK source package requires an exact tested KMediaVlc commit.")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Android NDK source package requires immutable non-SNAPSHOT SemVer.")
    normalized_epoch = max(epoch, MIN_EPOCH)
    policy_path, policy, source_inputs, package_policy = load_policy(root)
    checkouts = {
        "llvm-android-build": llvm_android,
        "llvm-project": llvm_project,
    }
    entries: list[SourceFile] = []
    for source_id in SOURCE_IDS:
        entries.extend(
            inspect_checkout(
                source_id,
                checkouts[source_id],
                source_inputs[source_id],
                package_policy["sources"][source_id],
            )
        )
    root_name = package_policy["archiveRoot"]
    records = [source_record(root_name, entry) for entry in entries]
    if records != sorted(records, key=lambda record: record["path"]):
        fail("Android NDK source package file inventory is not canonical.")
    manifest = {
        "schemaVersion": 1,
        "target": "android-ndk-r29-runtime-source",
        "releaseVersion": version,
        "testedCommit": tested_commit,
        "sourceDateEpoch": normalized_epoch,
        "ndkRevision": NDK_REVISION,
        "verifiedSourceStatus": package_policy["verifiedSourceStatus"],
        "staticComponentPolicy": {
            "path": POLICY_PATH.as_posix(),
            "sha256": sha256_file(policy_path),
        },
        "sourceInputs": [
            {"id": source_id, **source_inputs[source_id]} for source_id in SOURCE_IDS
        ],
        "sourcePackagePolicy": package_policy,
        "ndkReleaseProvenance": policy["ndkReleaseProvenance"],
        "ndkArchiveSourcePaths": policy["ndkArchiveSourcePaths"],
        "files": records,
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path = PurePosixPath(root_name) / "SOURCE-MANIFEST.json"
    by_archive_path = {
        PurePosixPath(record["path"]): (entry, record)
        for entry, record in zip(entries, records)
    }
    directories = {PurePosixPath(root_name)}
    for path in (*by_archive_path, manifest_path):
        directories.update(parent for parent in path.parents if parent.parts)
    partial = output.with_name(output.name + ".partial")
    if partial.exists() or partial.is_symlink():
        fail("Android NDK source partial output already exists.")
    try:
        with partial.open("xb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=normalized_epoch
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for directory in sorted(
                        directories, key=lambda value: (len(value.parts), value.as_posix())
                    ):
                        archive.addfile(tar_info(directory, normalized_epoch, directory=True))
                    for path in sorted((*by_archive_path, manifest_path)):
                        if path == manifest_path:
                            archive.addfile(
                                tar_info(path, normalized_epoch, size=len(manifest_data)),
                                io.BytesIO(manifest_data),
                            )
                            continue
                        entry, expected = by_archive_path[path]
                        value = entry.checkout_path.read_bytes()
                        actual = source_record(root_name, entry)
                        if actual != expected:
                            fail(f"Android NDK source changed during packaging: {path}")
                        archive.addfile(
                            tar_info(
                                path,
                                normalized_epoch,
                                size=len(value),
                                mode=0o755 if entry.git_mode == "100755" else 0o644,
                            ),
                            io.BytesIO(value),
                        )
        partial.rename(output)
    except BaseException:
        if partial.exists() and not partial.is_symlink():
            partial.unlink()
        raise
    return sha256_file(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--llvm-project", type=Path, required=True)
    parser.add_argument("--llvm-android", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tested-commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    arguments = parser.parse_args()
    digest = package(
        arguments.root,
        arguments.llvm_project,
        arguments.llvm_android,
        arguments.output,
        arguments.tested_commit,
        arguments.version,
        arguments.epoch,
    )
    print(f"{digest}  {arguments.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
