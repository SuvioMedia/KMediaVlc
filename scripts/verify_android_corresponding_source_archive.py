#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Independently verify complete Android corresponding source against its inputs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import tarfile
from pathlib import Path, PurePosixPath


POLICY_PATH = PurePosixPath("compliance/policy/android-corresponding-source.json")
STATIC_POLICY_PATH = PurePosixPath("compliance/policy/android-static-components.json")
RECIPE_PATH = PurePosixPath("build-recipes/android.json")
SOURCE_IDS = ("kmediavlc", "libvlcjni", "vlc")
AUDIT_TARGETS = ("android-arm64-v8a", "android-armeabi-v7a")
NDK_REVISION = "29.0.14206865"
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
SOURCE_ARCHIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)")
MIN_EPOCH = 315532800
MAX_MEMBERS = 100_000
MAX_GIT_FILE_SIZE = 256 * 1024 * 1024
MAX_EXTERNAL_FILE_SIZE = 1024 * 1024 * 1024
MAX_MANIFEST_SIZE = 64 * 1024 * 1024


def load_ndk_verifier():
    path = Path(__file__).with_name("verify_android_ndk_source_archive.py")
    spec = importlib.util.spec_from_file_location("nested_android_ndk_source_verifier", path)
    if spec is None or spec.loader is None:
        raise ValueError("The independent Android NDK source verifier is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NDK_VERIFIER = load_ndk_verifier()


def fail(message: str) -> None:
    raise ValueError(message)


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


def digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                return digest.hexdigest(), size
            digest.update(block)
            size += len(block)


def read_json_file(path: Path, description: str) -> dict:
    path = real_file(path, description)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{description} is unreadable.") from error
    if not isinstance(value, dict):
        fail(f"{description} root must be an object.")
    return value


def safe_relative(value: str, description: str) -> PurePosixPath:
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


def canonical_paths(values: object, description: str) -> list[str]:
    if (
        not isinstance(values, list)
        or not values
        or values != sorted(set(values))
        or any(not isinstance(value, str) for value in values)
    ):
        fail(f"{description} are not canonical.")
    for value in values:
        safe_relative(value, description)
    return values


def load_policies(root: Path) -> tuple[Path, dict, Path, dict, Path, dict[str, list[str]]]:
    policy_path = real_file(root.joinpath(*POLICY_PATH.parts), "Android corresponding-source policy")
    policy = read_json_file(policy_path, "Android corresponding-source policy")
    if (
        set(policy)
        != {
            "schemaVersion",
            "target",
            "archiveRoot",
            "format",
            "verifiedClosureStatus",
            "sourceInputs",
            "contribSourceArchives",
            "ndkSourcePackage",
            "buildEvidence",
            "generatedFiles",
        }
        or policy.get("schemaVersion") != 1
        or policy.get("target") != "android-arm"
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]+", str(policy.get("archiveRoot")))
        or policy.get("format") != "deterministic-tar-gzip-v1"
        or policy.get("verifiedClosureStatus")
        != "complete-source-and-relink-inputs-packaged"
    ):
        fail("Android corresponding-source policy identity is unsupported.")
    sources = policy.get("sourceInputs")
    if not isinstance(sources, dict) or tuple(sources) != SOURCE_IDS:
        fail("Android corresponding-source Git input closure is incomplete.")
    for source_id in SOURCE_IDS:
        source = sources[source_id]
        expected_keys = (
            {"repository", "revisionBinding", "scope", "requiredPaths"}
            if source_id == "kmediavlc"
            else {"repository", "revision", "tree", "scope", "requiredPaths"}
        )
        if (
            not isinstance(source, dict)
            or set(source) != expected_keys
            or not isinstance(source.get("repository"), str)
            or source.get("scope") != "complete-tree"
        ):
            fail(f"Android corresponding-source Git policy is invalid: {source_id}")
        canonical_paths(source.get("requiredPaths"), f"Required {source_id} source paths")
        if source_id == "kmediavlc":
            if source.get("revisionBinding") != "tested-commit":
                fail("KMediaVlc source policy does not bind the tested commit.")
        elif (
            not COMMIT.fullmatch(str(source.get("revision")))
            or not COMMIT.fullmatch(str(source.get("tree")))
        ):
            fail(f"Android corresponding-source Git pin is invalid: {source_id}")
    if policy.get("contribSourceArchives") != {
        "componentPolicy": STATIC_POLICY_PATH.as_posix(),
        "archiveDirectory": "sources/vlc-contrib-tarballs",
        "archiveCount": 55,
    }:
        fail("Android corresponding-source contrib policy is invalid.")
    if policy.get("ndkSourcePackage") != {
        "componentPolicy": STATIC_POLICY_PATH.as_posix(),
        "archivePath": "source-packages/android-ndk-runtime-source.tar.gz",
        "archiveRoot": "android-ndk-runtime-source",
        "format": "deterministic-tar-gzip-v1",
        "requiresIndependentVerification": True,
    }:
        fail("Android corresponding-source NDK policy is invalid.")
    if policy.get("buildEvidence") != {
        "legalManifestPath": "build-evidence/android-static-legal.json",
        "linkAudits": {
            "android-arm64-v8a": "build-evidence/link-audits/android-arm64-v8a.json",
            "android-armeabi-v7a": "build-evidence/link-audits/android-armeabi-v7a.json",
        },
    } or policy.get("generatedFiles") != ["REBUILD.md", "SOURCE-SHA256SUMS"]:
        fail("Android corresponding-source evidence policy is invalid.")

    static_path = real_file(
        root.joinpath(*STATIC_POLICY_PATH.parts), "Android static component policy"
    )
    static_policy = read_json_file(static_path, "Android static component policy")
    if (
        static_policy.get("schemaVersion") != 1
        or static_policy.get("target") != "android-arm"
        or static_policy.get("vlcRevision") != sources["vlc"]["revision"]
        or static_policy.get("ndkRevision") != NDK_REVISION
    ):
        fail("Android static policy differs from corresponding source.")
    components = static_policy.get("contribComponents")
    if (
        not isinstance(components, dict)
        or list(components) != sorted(components)
        or len(components) != 54
    ):
        fail("Android static policy does not close 54 contrib components.")
    archive_components: dict[str, list[str]] = {}
    for component_id, component in components.items():
        if not isinstance(component, dict) or set(component) != {"version", "sourceArchives"}:
            fail(f"Android contrib component fields are invalid: {component_id}")
        archives = component.get("sourceArchives")
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9-]+", component_id)
            or not isinstance(archives, list)
            or not archives
            or archives != sorted(set(archives))
        ):
            fail(f"Android contrib source map is invalid: {component_id}")
        for archive in archives:
            if not isinstance(archive, str) or not SOURCE_ARCHIVE.fullmatch(archive):
                fail(f"Android contrib source archive is unsafe: {component_id}")
            archive_components.setdefault(archive, []).append(component_id)
    if len(archive_components) != 55:
        fail("Android static policy does not close 55 source archives.")

    recipe_path = real_file(root.joinpath(*RECIPE_PATH.parts), "Android build recipe")
    recipe = read_json_file(recipe_path, "Android build recipe")
    if (
        recipe.get("vlcRevision") != sources["vlc"]["revision"]
        or recipe.get("libvlcjniRevision") != sources["libvlcjni"]["revision"]
        or recipe.get("ndkVersion") != NDK_REVISION
        or recipe.get("correspondingSourcePackagePolicy") != POLICY_PATH.as_posix()
        or recipe.get("requiresCompleteCorrespondingSourcePackage") is not True
        or recipe.get("requiresIndependentCorrespondingSourceVerification") is not True
    ):
        fail("Android build recipe is not bound to complete corresponding source.")
    return policy_path, policy, static_path, static_policy, recipe_path, archive_components


def git_bytes(checkout: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise ValueError("Git is required to verify Android corresponding source.") from error
    if result.returncode != 0:
        fail("An Android corresponding-source checkout could not be inspected with Git.")
    return result.stdout


def git_identity(checkout: Path) -> tuple[str, str]:
    try:
        revision = git_bytes(checkout, "rev-parse", "HEAD").decode("ascii").strip()
        tree = git_bytes(checkout, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError("Android source Git identity is not ASCII.") from error
    return revision, tree


def commit_epoch(root: Path) -> int:
    try:
        value = git_bytes(root, "show", "-s", "--format=%ct", "HEAD").decode("ascii").strip()
        epoch = int(value)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("The tested KMediaVlc commit timestamp is invalid.") from error
    if epoch < 0:
        fail("The tested KMediaVlc commit timestamp is invalid.")
    return max(epoch, MIN_EPOCH)


def git_object_values(
    checkout: Path, entries: list[tuple[str, str, str]]
) -> list[tuple[str, str, str, bytes]]:
    try:
        process = subprocess.Popen(
            ["git", "-C", str(checkout), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise ValueError("Git could not read Android corresponding-source objects.") from error
    if process.stdin is None or process.stdout is None:
        process.kill()
        process.wait()
        fail("Git did not expose its batch object streams.")
    values: list[tuple[str, str, str, bytes]] = []
    try:
        for source_path, mode, expected_blob in entries:
            process.stdin.write((expected_blob + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline()
            try:
                object_id, object_type, encoded_size = header.decode("ascii").strip().split(" ")
                size = int(encoded_size)
            except (UnicodeDecodeError, ValueError) as error:
                raise ValueError("Git returned malformed Android source object metadata.") from error
            if (
                object_id != expected_blob
                or object_type != "blob"
                or size < 0
                or size > MAX_GIT_FILE_SIZE
            ):
                fail("Git returned an unsupported Android source object.")
            value = process.stdout.read(size)
            separator = process.stdout.read(1)
            if len(value) != size or separator != b"\n":
                fail("Git returned a truncated Android source object.")
            git_blob = hashlib.sha1(f"blob {len(value)}\0".encode("ascii"))
            git_blob.update(value)
            if git_blob.hexdigest() != expected_blob:
                fail("Git returned bytes that differ from the requested Android source object.")
            values.append((source_path, mode, expected_blob, value))
        process.stdin.close()
        return_code = process.wait()
        process.stdout.close()
        if return_code != 0:
            fail("Git failed while reading Android source objects.")
    except BaseException:
        if not process.stdin.closed:
            process.stdin.close()
        if not process.stdout.closed:
            process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()
        raise
    return values


def expected_git_records(
    source_id: str,
    checkout: Path,
    policy: dict,
    tested_commit: str,
    root_name: str,
) -> tuple[list[dict], dict]:
    checkout = real_directory(checkout, f"Android source checkout {source_id}")
    revision, tree = git_identity(checkout)
    expected_revision = tested_commit if source_id == "kmediavlc" else policy["revision"]
    if revision != expected_revision or (source_id != "kmediavlc" and tree != policy["tree"]):
        fail(f"Android source checkout differs from its policy: {source_id}")
    if git_bytes(checkout, "status", "--porcelain", "--untracked-files=no"):
        fail(f"Android source checkout has tracked modifications: {source_id}")
    raw = git_bytes(checkout, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    tree_entries: list[tuple[str, str, str]] = []
    source_paths: list[str] = []
    for raw_entry in raw.split(b"\0"):
        if not raw_entry:
            continue
        try:
            metadata, encoded_path = raw_entry.split(b"\t", 1)
            mode, object_type, git_blob = metadata.decode("ascii").split(" ")
            source_path = encoded_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("Android source Git tree entry is malformed.") from error
        if (
            mode not in {"100644", "100755"}
            or object_type != "blob"
            or not COMMIT.fullmatch(git_blob)
            or source_path in source_paths
        ):
            fail(f"Android source Git object is unsupported: {source_id}")
        safe_relative(source_path, "Android tracked source path")
        tree_entries.append((source_path, mode, git_blob))
        source_paths.append(source_path)
    if not tree_entries or source_paths != sorted(source_paths):
        fail(f"Android source Git tree is empty or non-canonical: {source_id}")
    records: list[dict] = []
    for source_path, mode, git_blob, value in git_object_values(checkout, tree_entries):
        records.append(
            {
                "kind": "git-source",
                "path": (
                    PurePosixPath(root_name) / "sources" / source_id / source_path
                ).as_posix(),
                "sourceId": source_id,
                "sourcePath": source_path,
                "gitMode": mode,
                "gitBlob": git_blob,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size": len(value),
            }
        )
    for required in policy["requiredPaths"]:
        required_path = PurePosixPath(required)
        if not any(
            PurePosixPath(path) == required_path or PurePosixPath(path).is_relative_to(required_path)
            for path in source_paths
        ):
            fail(f"Android source checkout omits required input: {source_id}/{required}")
    identity = {
        "id": source_id,
        "repository": policy["repository"],
        "revision": revision,
        "tree": tree,
        "scope": "complete-tree",
        "requiredPaths": policy["requiredPaths"],
        "fileCount": len(records),
    }
    return records, identity


def external_record(root_name: str, relative: str, kind: str, path: Path) -> dict:
    path = real_file(path, f"Android corresponding-source input {relative}")
    sha256, size = digest_file(path)
    if size <= 0 or size > MAX_EXTERNAL_FILE_SIZE:
        fail(f"Android corresponding-source input is empty or oversized: {relative}")
    return {
        "kind": kind,
        "path": (PurePosixPath(root_name) / safe_relative(relative, "Archive input path")).as_posix(),
        "sha256": sha256,
        "size": size,
    }


def legal_source_identity(
    legal_manifest: Path,
    static_path: Path,
    static_policy: dict,
    archive_components: dict[str, list[str]],
) -> tuple[Path, dict, dict[str, dict], dict[str, dict]]:
    legal_manifest = real_file(legal_manifest, "Android legal evidence manifest")
    manifest = read_json_file(legal_manifest, "Android legal evidence manifest")
    static_digest, _ = digest_file(static_path)
    if (
        set(manifest)
        != {
            "schemaVersion",
            "vlcRevision",
            "ndkRevision",
            "reviewStatus",
            "effectiveLicenseSpdx",
            "candidateLicenseInventorySpdx",
            "staticComponentPolicy",
            "abiAudits",
            "files",
            "components",
        }
        or manifest.get("schemaVersion") != 1
        or manifest.get("vlcRevision") != static_policy["vlcRevision"]
        or manifest.get("ndkRevision") != NDK_REVISION
        or manifest.get("reviewStatus")
        not in {"candidate-linked-member-review-pending", "approved"}
        or manifest.get("staticComponentPolicy")
        != {"path": STATIC_POLICY_PATH.as_posix(), "sha256": static_digest}
        or not isinstance(manifest.get("candidateLicenseInventorySpdx"), list)
        or not isinstance(manifest.get("files"), list)
    ):
        fail("Android legal evidence identity is invalid.")
    approved = manifest["reviewStatus"] == "approved"
    effective = manifest.get("effectiveLicenseSpdx")
    if (approved and (not isinstance(effective, str) or not effective.strip())) or (
        not approved and effective is not None
    ):
        fail("Android legal evidence review and effective license disagree.")

    audit_entries = manifest.get("abiAudits")
    if not isinstance(audit_entries, list) or len(audit_entries) != 2:
        fail("Android legal evidence ABI audit closure is invalid.")
    audit_by_target: dict[str, dict] = {}
    for entry in audit_entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"target", "reportSha256", "libvlcSha256"}
            or entry.get("target") not in AUDIT_TARGETS
            or not SHA256.fullmatch(str(entry.get("reportSha256")))
            or not SHA256.fullmatch(str(entry.get("libvlcSha256")))
            or entry["target"] in audit_by_target
        ):
            fail("Android legal evidence ABI audit entry is invalid.")
        audit_by_target[entry["target"]] = entry
    if tuple(audit_by_target) != AUDIT_TARGETS:
        fail("Android legal evidence ABI audit order is not canonical.")

    components = manifest.get("components")
    expected_ids = {"android-ndk-llvm-runtime", *static_policy["contribComponents"]}
    if not isinstance(components, list) or len(components) != len(expected_ids):
        fail("Android legal evidence component closure is invalid.")
    source_by_name: dict[str, dict] = {}
    ids: list[str] = []
    for component in components:
        if not isinstance(component, dict) or component.get("id") not in expected_ids:
            fail("Android legal evidence component entry is invalid.")
        component_id = component["id"]
        ids.append(component_id)
        sources = component.get("sourceArchives")
        if component_id == "android-ndk-llvm-runtime":
            if (
                component.get("kind") != "NDK_TOOLCHAIN"
                or sources != []
                or component.get("sourceStatus")
                not in {
                    "exact-source-revisions-recorded-source-package-pending",
                    "corresponding-source-mapped",
                }
            ):
                fail("Android legal evidence NDK source state is invalid.")
            continue
        expected_names = static_policy["contribComponents"][component_id]["sourceArchives"]
        if (
            component.get("kind") != "VLC_CONTRIB"
            or component.get("sourceStatus") != "source-archive-hashes-recorded"
            or not isinstance(sources, list)
            or len(sources) != len(expected_names)
        ):
            fail(f"Android legal contrib source entry is invalid: {component_id}")
        names: list[str] = []
        for source in sources:
            if not isinstance(source, dict) or set(source) != {"path", "sha256", "size"}:
                fail(f"Android legal source record is invalid: {component_id}")
            value = source.get("path")
            prefix = "vlc-contrib-tarballs/"
            name = value[len(prefix):] if isinstance(value, str) and value.startswith(prefix) else ""
            if (
                not SOURCE_ARCHIVE.fullmatch(name)
                or not SHA256.fullmatch(str(source.get("sha256")))
                or not isinstance(source.get("size"), int)
                or source["size"] <= 0
            ):
                fail(f"Android legal source digest is invalid: {component_id}")
            previous = source_by_name.setdefault(name, source)
            if previous != source:
                fail(f"Android legal source identity disagrees: {name}")
            names.append(name)
        if names != expected_names:
            fail(f"Android legal source mapping differs: {component_id}")
    if ids != sorted(expected_ids) or set(source_by_name) != set(archive_components):
        fail("Android legal source closure is not canonical.")
    return legal_manifest, manifest, source_by_name, audit_by_target


def checked_audit(
    path: Path,
    target: str,
    expected: dict,
    static_path: Path,
    source_policy: dict,
) -> Path:
    path = real_file(path, f"Android {target} link audit")
    digest, _ = digest_file(path)
    if digest != expected["reportSha256"]:
        fail(f"Android link audit differs from legal evidence: {target}")
    audit = read_json_file(path, f"Android {target} link audit")
    static_digest, _ = digest_file(static_path)
    abi = "arm64-v8a" if target == "android-arm64-v8a" else "armeabi-v7a"
    if (
        audit.get("schemaVersion") != 1
        or audit.get("target") != target
        or audit.get("abi") != abi
        or audit.get("androidApi") != 21
        or audit.get("vlcRevision") != source_policy["vlc"]["revision"]
        or audit.get("libvlcjniRevision") != source_policy["libvlcjni"]["revision"]
        or audit.get("ndkRevision") != NDK_REVISION
        or audit.get("reviewStatus")
        not in {"candidate-source-mapped-license-review-pending", "approved"}
        or not isinstance(audit.get("libvlc"), dict)
        or audit["libvlc"].get("sha256") != expected["libvlcSha256"]
        or not isinstance(audit.get("modules"), list)
        or not audit["modules"]
        or not isinstance(audit.get("staticArchives"), list)
        or not audit["staticArchives"]
        or not isinstance(audit.get("staticComponents"), list)
        or len(audit["staticComponents"]) != 55
        or not isinstance(audit.get("evidence"), dict)
        or audit["evidence"].get("staticComponentPolicy")
        != {"path": STATIC_POLICY_PATH.as_posix(), "sha256": static_digest}
    ):
        fail(f"Android link audit identity is invalid: {target}")
    return path


def expected_rebuild(version: str, tested_commit: str) -> bytes:
    command = (
        "bash sources/kmediavlc/scripts/build_vlc_android.sh sources/vlc "
        "sources/libvlcjni /path/to/android-ndk-r29 /path/to/cmake "
        "/path/to/audit-work /path/to/candidate-output"
    )
    return (
        "# Android libVLC 4 corresponding source\n\n"
        f"Release: {version}\n"
        f"KMediaVlc commit: {tested_commit}\n\n"
        "This archive contains the complete tracked KMediaVlc, VLC, and libvlcjni "
        "trees used by the Android build, all selected contrib source archives, "
        "the independently verified NDK runtime source supplement, and both ABI "
        "link-audit reports.\n\n"
        "Rebuild prerequisites:\n\n"
        f"- Android NDK {NDK_REVISION}\n"
        "- CMake 4.1.2\n"
        "- the ordinary host tools checked by scripts/build_vlc_android.sh\n\n"
        "From this directory, run:\n\n"
        f"```shell\n{command}\n```\n\n"
        "The script applies the committed libvlcjni policy patch, rebuilds contribs "
        "from the packaged tarballs, emits fresh ABI audits, and builds the narrow "
        "KMediaVlc JNI bridge. The nested NDK archive provides source for the linked "
        "LLVM runtimes; it is not a replacement for the NDK compiler binaries.\n"
    ).encode("utf-8")


def safe_member(member: tarfile.TarInfo, archive_root: PurePosixPath) -> PurePosixPath:
    name = member.name.rstrip("/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or not path.is_relative_to(archive_root)
    ):
        fail(f"Unsafe Android corresponding-source member: {member.name!r}")
    if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
        fail(f"Android corresponding source contains a link or special file: {member.name!r}")
    if member.size < 0 or member.size > MAX_EXTERNAL_FILE_SIZE:
        fail(f"Android corresponding-source member is oversized: {member.name!r}")
    return path


def read_member(source: tarfile.TarFile, member: tarfile.TarInfo, limit: int) -> bytes:
    if not member.isfile() or member.size > limit:
        fail(f"Android corresponding-source metadata is invalid: {member.name}")
    stream = source.extractfile(member)
    if stream is None:
        fail(f"Android corresponding-source member is unreadable: {member.name}")
    with stream:
        value = stream.read(limit + 1)
    if len(value) != member.size or len(value) > limit:
        fail(f"Android corresponding-source member is truncated: {member.name}")
    return value


def member_hashes(source: tarfile.TarFile, member: tarfile.TarInfo, git: bool) -> tuple[str, str | None]:
    stream = source.extractfile(member)
    if stream is None:
        fail(f"Android corresponding-source member is unreadable: {member.name}")
    sha256 = hashlib.sha256()
    git_blob = hashlib.sha1(f"blob {member.size}\0".encode("ascii")) if git else None
    size = 0
    with stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            sha256.update(block)
            if git_blob is not None:
                git_blob.update(block)
    if size != member.size:
        fail(f"Android corresponding-source member is truncated: {member.name}")
    return sha256.hexdigest(), git_blob.hexdigest() if git_blob is not None else None


def verify(
    root: Path,
    archive: Path,
    vlc: Path,
    libvlcjni: Path,
    contrib_tarballs: Path,
    ndk_source_archive: Path,
    llvm_project: Path,
    llvm_android: Path,
    legal_manifest: Path,
    arm64_audit: Path,
    armv7_audit: Path,
    version: str,
    tested_commit: str,
) -> str:
    root = real_directory(root, "KMediaVlc source root")
    archive = real_file(archive, "Android corresponding-source archive")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Android corresponding-source verification requires immutable SemVer.")
    if not COMMIT.fullmatch(tested_commit):
        fail("Android corresponding-source verification requires an exact tested commit.")
    (
        policy_path,
        policy,
        static_path,
        static_policy,
        recipe_path,
        archive_components,
    ) = load_policies(root)
    root_name = policy["archiveRoot"]
    epoch = commit_epoch(root)

    checkouts = {"kmediavlc": root, "libvlcjni": libvlcjni, "vlc": vlc}
    records: list[dict] = []
    source_identities: list[dict] = []
    for source_id in SOURCE_IDS:
        source_records, identity = expected_git_records(
            source_id,
            checkouts[source_id],
            policy["sourceInputs"][source_id],
            tested_commit,
            root_name,
        )
        records.extend(source_records)
        source_identities.append(identity)

    legal_manifest, legal, legal_sources, legal_audits = legal_source_identity(
        legal_manifest, static_path, static_policy, archive_components
    )
    audit_paths = {
        "android-arm64-v8a": checked_audit(
            arm64_audit,
            "android-arm64-v8a",
            legal_audits["android-arm64-v8a"],
            static_path,
            policy["sourceInputs"],
        ),
        "android-armeabi-v7a": checked_audit(
            armv7_audit,
            "android-armeabi-v7a",
            legal_audits["android-armeabi-v7a"],
            static_path,
            policy["sourceInputs"],
        ),
    }

    ndk_source_archive = real_file(ndk_source_archive, "Android NDK source archive")
    ndk_digest = NDK_VERIFIER.verify(
        root,
        ndk_source_archive,
        llvm_project,
        llvm_android,
        version,
        tested_commit,
    )

    contrib_tarballs = real_directory(contrib_tarballs, "VLC contrib tarball directory")
    contrib_records: list[dict] = []
    for name in sorted(archive_components):
        source = real_file(contrib_tarballs / name, f"VLC contrib source archive {name}")
        try:
            source.relative_to(contrib_tarballs)
        except ValueError:
            fail(f"VLC contrib source archive escaped its directory: {name}")
        record = external_record(
            root_name,
            f"sources/vlc-contrib-tarballs/{name}",
            "contrib-source-archive",
            source,
        )
        legal_entry = legal_sources[name]
        if record["sha256"] != legal_entry["sha256"] or record["size"] != legal_entry["size"]:
            fail(f"VLC contrib source archive differs from legal evidence: {name}")
        records.append(record)
        contrib_records.append(
            {
                "name": name,
                "path": record["path"],
                "sha256": record["sha256"],
                "size": record["size"],
                "components": archive_components[name],
            }
        )

    ndk_record = external_record(
        root_name,
        policy["ndkSourcePackage"]["archivePath"],
        "ndk-source-package",
        ndk_source_archive,
    )
    if ndk_record["sha256"] != ndk_digest:
        fail("Android NDK source archive changed after independent verification.")
    records.append(ndk_record)

    legal_record = external_record(
        root_name,
        policy["buildEvidence"]["legalManifestPath"],
        "legal-evidence-manifest",
        legal_manifest,
    )
    records.append(legal_record)
    audit_records: list[dict] = []
    for target in AUDIT_TARGETS:
        record = external_record(
            root_name,
            policy["buildEvidence"]["linkAudits"][target],
            "link-audit",
            audit_paths[target],
        )
        if record["sha256"] != legal_audits[target]["reportSha256"]:
            fail(f"Android link audit changed after validation: {target}")
        records.append(record)
        audit_records.append({"target": target, **record})

    checksum_records = sorted(
        (record for record in records if record["kind"] != "git-source"),
        key=lambda record: record["path"],
    )
    checksum_data = "".join(
        f"{record['sha256']}  "
        f"{PurePosixPath(record['path']).relative_to(PurePosixPath(root_name)).as_posix()}\n"
        for record in checksum_records
    ).encode("ascii")
    generated = {
        (PurePosixPath(root_name) / "REBUILD.md").as_posix(): expected_rebuild(
            version, tested_commit
        ),
        (PurePosixPath(root_name) / "SOURCE-SHA256SUMS").as_posix(): checksum_data,
    }
    for path, value in generated.items():
        records.append(
            {
                "kind": "generated",
                "path": path,
                "sha256": hashlib.sha256(value).hexdigest(),
                "size": len(value),
            }
        )
    records.sort(key=lambda record: record["path"])
    if len({record["path"] for record in records}) != len(records):
        fail("Expected Android corresponding-source inventory contains duplicate paths.")

    policy_digest, _ = digest_file(policy_path)
    static_digest, _ = digest_file(static_path)
    recipe_digest, _ = digest_file(recipe_path)
    expected_manifest = {
        "schemaVersion": 1,
        "target": "android-arm-corresponding-source",
        "releaseVersion": version,
        "testedCommit": tested_commit,
        "sourceDateEpoch": epoch,
        "verifiedClosureStatus": policy["verifiedClosureStatus"],
        "correspondingSourcePolicy": {
            "path": POLICY_PATH.as_posix(),
            "sha256": policy_digest,
        },
        "staticComponentPolicy": {
            "path": STATIC_POLICY_PATH.as_posix(),
            "sha256": static_digest,
        },
        "buildRecipe": {"path": RECIPE_PATH.as_posix(), "sha256": recipe_digest},
        "sourceInputs": source_identities,
        "contribSourceArchives": contrib_records,
        "ndkSourcePackage": {
            "path": ndk_record["path"],
            "sha256": ndk_record["sha256"],
            "size": ndk_record["size"],
            "verifiedSha256": ndk_digest,
            "archiveRoot": policy["ndkSourcePackage"]["archiveRoot"],
        },
        "buildEvidence": {
            "reviewStatus": legal["reviewStatus"],
            "effectiveLicenseSpdx": legal["effectiveLicenseSpdx"],
            "legalManifest": legal_record,
            "abiAudits": audit_records,
        },
        "files": records,
    }
    manifest_data = (json.dumps(expected_manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path = PurePosixPath(root_name) / "SOURCE-MANIFEST.json"
    file_paths = {PurePosixPath(record["path"]) for record in records} | {manifest_path}
    directories = {PurePosixPath(root_name)}
    for path in file_paths:
        directories.update(parent for parent in path.parents if parent.parts)

    with archive.open("rb") as raw:
        gzip_header = raw.read(10)
    if (
        len(gzip_header) != 10
        or gzip_header[:4] != b"\x1f\x8b\x08\x00"
        or struct.unpack("<I", gzip_header[4:8])[0] != epoch
        or gzip_header[8:] != b"\x02\xff"
    ):
        fail("Android corresponding-source gzip header is not deterministic.")

    archive_root = PurePosixPath(root_name)
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            fail("Android corresponding-source archive member count is invalid.")
        by_path: dict[PurePosixPath, tarfile.TarInfo] = {}
        order: list[PurePosixPath] = []
        for member in members:
            path = safe_member(member, archive_root)
            if path in by_path:
                fail(f"Duplicate Android corresponding-source member: {path}")
            by_path[path] = member
            order.append(path)
        expected_order = sorted(
            directories, key=lambda value: (len(value.parts), value.as_posix())
        ) + sorted(file_paths)
        if order != expected_order:
            fail("Android corresponding-source member order is not deterministic.")
        actual_files = {path for path, member in by_path.items() if member.isfile()}
        actual_directories = {path for path, member in by_path.items() if member.isdir()}
        if actual_files != file_paths or actual_directories != directories:
            fail("Android corresponding-source archive closure is incomplete or contains extras.")
        actual_manifest_data = read_member(source, by_path[manifest_path], MAX_MANIFEST_SIZE)
        try:
            actual_manifest = json.loads(actual_manifest_data)
        except json.JSONDecodeError as error:
            raise ValueError("Android corresponding-source manifest is invalid JSON.") from error
        if actual_manifest != expected_manifest or actual_manifest_data != manifest_data:
            fail("Android corresponding-source manifest differs from the exact source inputs.")

        record_by_path = {PurePosixPath(record["path"]): record for record in records}
        for path, member in by_path.items():
            if (
                member.uid != 0
                or member.gid != 0
                or member.uname != ""
                or member.gname != ""
                or member.mtime != epoch
            ):
                fail(f"Android corresponding-source metadata is not reproducible: {path}")
            if member.isdir():
                if member.mode != 0o755 or member.size != 0:
                    fail(f"Android corresponding-source directory metadata is invalid: {path}")
                continue
            if path == manifest_path:
                if member.mode != 0o644 or member.size != len(manifest_data):
                    fail("Android corresponding-source manifest metadata is invalid.")
                continue
            record = record_by_path[path]
            expected_mode = (
                0o755
                if record["kind"] == "git-source" and record["gitMode"] == "100755"
                else 0o644
            )
            if member.mode != expected_mode or member.size != record["size"]:
                fail(f"Android corresponding-source member mode or size differs: {path}")
            if record["kind"] == "generated":
                value = read_member(source, member, 16 * 1024 * 1024)
                if value != generated[path.as_posix()]:
                    fail(f"Android corresponding-source generated file differs: {path}")
                continue
            sha256, git_blob = member_hashes(
                source, member, record["kind"] == "git-source"
            )
            if sha256 != record["sha256"] or (
                record["kind"] == "git-source" and git_blob != record["gitBlob"]
            ):
                fail(f"Android corresponding-source member differs from its input: {path}")
    digest, _ = digest_file(archive)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--vlc", type=Path, required=True)
    parser.add_argument("--libvlcjni", type=Path, required=True)
    parser.add_argument("--contrib-tarballs", type=Path, required=True)
    parser.add_argument("--ndk-source-archive", type=Path, required=True)
    parser.add_argument("--llvm-project", type=Path, required=True)
    parser.add_argument("--llvm-android", type=Path, required=True)
    parser.add_argument("--legal-manifest", type=Path, required=True)
    parser.add_argument("--arm64-audit", type=Path, required=True)
    parser.add_argument("--armv7-audit", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tested-commit", required=True)
    arguments = parser.parse_args()
    digest = verify(
        arguments.root,
        arguments.archive,
        arguments.vlc,
        arguments.libvlcjni,
        arguments.contrib_tarballs,
        arguments.ndk_source_archive,
        arguments.llvm_project,
        arguments.llvm_android,
        arguments.legal_manifest,
        arguments.arm64_audit,
        arguments.armv7_audit,
        arguments.version,
        arguments.tested_commit,
    )
    print(f"{digest}  {arguments.archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
