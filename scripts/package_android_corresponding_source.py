#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministically package the complete Android corresponding-source closure."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath
from typing import NamedTuple


POLICY_PATH = PurePosixPath("compliance/policy/android-corresponding-source.json")
STATIC_POLICY_PATH = PurePosixPath("compliance/policy/android-static-components.json")
RECIPE_PATH = PurePosixPath("build-recipes/android.json")
NDK_REVISION = "29.0.14206865"
SOURCE_IDS = ("kmediavlc", "libvlcjni", "vlc")
AUDIT_TARGETS = ("android-arm64-v8a", "android-armeabi-v7a")
COMMIT = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
SOURCE_ARCHIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)")
MIN_EPOCH = 315532800
MAX_GIT_FILE_SIZE = 256 * 1024 * 1024
MAX_EXTERNAL_FILE_SIZE = 1024 * 1024 * 1024


def load_ndk_verifier():
    path = Path(__file__).with_name("verify_android_ndk_source_archive.py")
    spec = importlib.util.spec_from_file_location("android_ndk_source_verifier", path)
    if spec is None or spec.loader is None:
        raise ValueError("The independent Android NDK source verifier is unavailable.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NDK_VERIFIER = load_ndk_verifier()


class GitSourceFile(NamedTuple):
    source_id: str
    source_path: str
    checkout: Path
    git_mode: str
    git_blob: str


class VerifiedReader:
    def __init__(self, source: object) -> None:
        self.source = source
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        value = self.source.read(size)
        self.digest.update(value)
        self.size += len(value)
        return value


def fail(message: str) -> None:
    raise ValueError(message)


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                return digest.hexdigest(), size
            digest.update(block)
            size += len(block)


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


def safe_path(value: str, description: str) -> PurePosixPath:
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


def read_json(path: Path, description: str) -> dict:
    path = real_file(path, description)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{description} is unreadable.") from error
    if not isinstance(value, dict):
        fail(f"{description} root must be an object.")
    return value


def validate_required_paths(values: object, description: str) -> list[str]:
    if (
        not isinstance(values, list)
        or not values
        or values != sorted(set(values))
        or any(not isinstance(value, str) for value in values)
    ):
        fail(f"{description} are not canonical.")
    for value in values:
        safe_path(value, description)
    return values


def load_policies(root: Path) -> tuple[Path, dict, Path, dict, Path, dict, dict[str, list[str]]]:
    policy_path = real_file(root.joinpath(*POLICY_PATH.parts), "Android corresponding-source policy")
    policy = read_json(policy_path, "Android corresponding-source policy")
    expected_policy_keys = {
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
    if (
        set(policy) != expected_policy_keys
        or policy.get("schemaVersion") != 1
        or policy.get("target") != "android-arm"
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]+", str(policy.get("archiveRoot")))
        or policy.get("format") != "deterministic-tar-gzip-v1"
        or policy.get("verifiedClosureStatus")
        != "complete-source-and-relink-inputs-packaged"
    ):
        fail("Android corresponding-source policy identity is unsupported.")

    source_inputs = policy.get("sourceInputs")
    if not isinstance(source_inputs, dict) or tuple(source_inputs) != SOURCE_IDS:
        fail("Android corresponding source must close all three Git inputs.")
    for source_id in SOURCE_IDS:
        source = source_inputs[source_id]
        expected = (
            {"repository", "revisionBinding", "scope", "requiredPaths"}
            if source_id == "kmediavlc"
            else {"repository", "revision", "tree", "scope", "requiredPaths"}
        )
        if (
            not isinstance(source, dict)
            or set(source) != expected
            or not isinstance(source.get("repository"), str)
            or source.get("scope") != "complete-tree"
        ):
            fail(f"Android corresponding-source Git identity is incomplete: {source_id}")
        if source_id == "kmediavlc":
            if source.get("revisionBinding") != "tested-commit":
                fail("KMediaVlc corresponding source must bind the tested commit.")
        elif (
            not COMMIT.fullmatch(str(source.get("revision")))
            or not COMMIT.fullmatch(str(source.get("tree")))
        ):
            fail(f"Android corresponding-source Git pin is invalid: {source_id}")
        validate_required_paths(source.get("requiredPaths"), f"Required {source_id} source paths")

    contrib_policy = policy.get("contribSourceArchives")
    if contrib_policy != {
        "componentPolicy": STATIC_POLICY_PATH.as_posix(),
        "archiveDirectory": "sources/vlc-contrib-tarballs",
        "archiveCount": 55,
    }:
        fail("Android corresponding-source contrib policy is not closed.")
    ndk_policy = policy.get("ndkSourcePackage")
    if ndk_policy != {
        "componentPolicy": STATIC_POLICY_PATH.as_posix(),
        "archivePath": "source-packages/android-ndk-runtime-source.tar.gz",
        "archiveRoot": "android-ndk-runtime-source",
        "format": "deterministic-tar-gzip-v1",
        "requiresIndependentVerification": True,
    }:
        fail("Android corresponding-source NDK package policy is not closed.")
    build_evidence = policy.get("buildEvidence")
    if (
        not isinstance(build_evidence, dict)
        or set(build_evidence) != {"legalManifestPath", "linkAudits"}
        or build_evidence.get("legalManifestPath") != "build-evidence/android-static-legal.json"
        or build_evidence.get("linkAudits")
        != {
            "android-arm64-v8a": "build-evidence/link-audits/android-arm64-v8a.json",
            "android-armeabi-v7a": "build-evidence/link-audits/android-armeabi-v7a.json",
        }
        or policy.get("generatedFiles") != ["REBUILD.md", "SOURCE-SHA256SUMS"]
    ):
        fail("Android corresponding-source evidence paths are not closed.")

    static_path = real_file(
        root.joinpath(*STATIC_POLICY_PATH.parts), "Android static component policy"
    )
    static_policy = read_json(static_path, "Android static component policy")
    if (
        static_policy.get("schemaVersion") != 1
        or static_policy.get("target") != "android-arm"
        or static_policy.get("vlcRevision") != source_inputs["vlc"]["revision"]
        or static_policy.get("ndkRevision") != NDK_REVISION
    ):
        fail("Android static component identity differs from corresponding source.")
    components = static_policy.get("contribComponents")
    if (
        not isinstance(components, dict)
        or list(components) != sorted(components)
        or len(components) != 54
    ):
        fail("Android corresponding source requires the exact 54 contrib components.")
    archive_components: dict[str, list[str]] = {}
    for component_id, component in components.items():
        sources = component.get("sourceArchives") if isinstance(component, dict) else None
        if (
            not re.fullmatch(r"[a-z0-9][a-z0-9-]+", component_id)
            or set(component) != {"version", "sourceArchives"}
            or not isinstance(sources, list)
            or not sources
            or sources != sorted(set(sources))
        ):
            fail(f"Android contrib source mapping is invalid: {component_id}")
        for name in sources:
            if not isinstance(name, str) or not SOURCE_ARCHIVE.fullmatch(name):
                fail(f"Android contrib source archive name is unsafe: {component_id}")
            archive_components.setdefault(name, []).append(component_id)
    if len(archive_components) != 55 or any(
        values != sorted(set(values)) for values in archive_components.values()
    ):
        fail("Android corresponding source requires exactly 55 mapped contrib archives.")

    recipe_path = real_file(root.joinpath(*RECIPE_PATH.parts), "Android build recipe")
    recipe = read_json(recipe_path, "Android build recipe")
    if (
        recipe.get("vlcRevision") != source_inputs["vlc"]["revision"]
        or recipe.get("libvlcjniRevision") != source_inputs["libvlcjni"]["revision"]
        or recipe.get("ndkVersion") != NDK_REVISION
        or recipe.get("correspondingSourcePackagePolicy") != POLICY_PATH.as_posix()
        or recipe.get("requiresCompleteCorrespondingSourcePackage") is not True
        or recipe.get("requiresIndependentCorrespondingSourceVerification") is not True
    ):
        fail("Android build recipe is not bound to the corresponding-source policy.")
    return (
        policy_path,
        policy,
        static_path,
        static_policy,
        recipe_path,
        recipe,
        archive_components,
    )


def git_output(checkout: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(checkout), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise ValueError("Git is required to package Android corresponding source.") from error
    if result.returncode != 0:
        fail("An Android corresponding-source checkout could not be inspected with Git.")
    return result.stdout


def git_commit_epoch(checkout: Path) -> int:
    try:
        value = git_output(checkout, "show", "-s", "--format=%ct", "HEAD").decode("ascii").strip()
        epoch = int(value)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("The tested KMediaVlc commit timestamp is invalid.") from error
    if epoch < 0:
        fail("The tested KMediaVlc commit timestamp is invalid.")
    return max(epoch, MIN_EPOCH)


def inspect_checkout(
    source_id: str,
    checkout: Path,
    source_policy: dict,
    tested_commit: str,
) -> tuple[list[GitSourceFile], dict]:
    checkout = real_directory(checkout, f"Android source checkout {source_id}")
    try:
        revision = git_output(checkout, "rev-parse", "HEAD").decode("ascii").strip()
        tree = git_output(checkout, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ValueError("Android source Git identity is not ASCII.") from error
    expected_revision = tested_commit if source_id == "kmediavlc" else source_policy["revision"]
    if revision != expected_revision or (
        source_id != "kmediavlc" and tree != source_policy["tree"]
    ):
        fail(f"Android source checkout differs from the pinned Git identity: {source_id}")
    if git_output(checkout, "status", "--porcelain", "--untracked-files=no"):
        fail(f"Android source checkout has tracked modifications: {source_id}")
    raw = git_output(checkout, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    entries: list[GitSourceFile] = []
    seen: set[str] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, git_blob = metadata.decode("ascii").split(" ")
            source_path = encoded_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("Android source Git tree entry is malformed.") from error
        if (
            mode not in {"100644", "100755"}
            or object_type != "blob"
            or not COMMIT.fullmatch(git_blob)
            or source_path in seen
        ):
            fail(f"Android source Git tree entry is unsupported: {source_id}")
        safe_path(source_path, "Android tracked source path")
        entries.append(GitSourceFile(source_id, source_path, checkout, mode, git_blob))
        seen.add(source_path)
    if not entries or entries != sorted(entries, key=lambda entry: entry.source_path):
        fail(f"Android source checkout inventory is empty or non-canonical: {source_id}")
    for required in source_policy["requiredPaths"]:
        required_path = PurePosixPath(required)
        if not any(
            PurePosixPath(entry.source_path) == required_path
            or PurePosixPath(entry.source_path).is_relative_to(required_path)
            for entry in entries
        ):
            fail(f"Android source checkout omits required input: {source_id}/{required}")
    identity = {
        "id": source_id,
        "repository": source_policy["repository"],
        "revision": revision,
        "tree": tree,
        "scope": "complete-tree",
        "requiredPaths": source_policy["requiredPaths"],
        "fileCount": len(entries),
    }
    return entries, identity


def git_blob_values(checkout: Path, entries: list[GitSourceFile]) -> list[tuple[GitSourceFile, bytes]]:
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
    values: list[tuple[GitSourceFile, bytes]] = []
    try:
        for entry in entries:
            process.stdin.write((entry.git_blob + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline()
            try:
                object_id, object_type, encoded_size = header.decode("ascii").strip().split(" ")
                size = int(encoded_size)
            except (UnicodeDecodeError, ValueError) as error:
                raise ValueError("Git returned malformed Android source object metadata.") from error
            if (
                object_id != entry.git_blob
                or object_type != "blob"
                or size < 0
                or size > MAX_GIT_FILE_SIZE
            ):
                fail(f"Git returned an unsupported Android source object: {entry.source_id}")
            value = process.stdout.read(size)
            separator = process.stdout.read(1)
            if len(value) != size or separator != b"\n":
                fail(f"Git returned a truncated Android source object: {entry.source_id}")
            values.append((entry, value))
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


def git_record(root_name: str, entry: GitSourceFile, value: bytes) -> dict:
    if len(value) > MAX_GIT_FILE_SIZE:
        fail(f"Android Git source file is oversized: {entry.source_id}/{entry.source_path}")
    git_digest = hashlib.sha1(f"blob {len(value)}\0".encode("ascii"))
    git_digest.update(value)
    if git_digest.hexdigest() != entry.git_blob:
        fail(
            "Android source bytes differ from the pinned Git blob: "
            f"{entry.source_id}/{entry.source_path}"
        )
    path = (
        PurePosixPath(root_name) / "sources" / entry.source_id / entry.source_path
    ).as_posix()
    record = {
        "kind": "git-source",
        "path": path,
        "sourceId": entry.source_id,
        "sourcePath": entry.source_path,
        "gitMode": entry.git_mode,
        "gitBlob": entry.git_blob,
        "sha256": hashlib.sha256(value).hexdigest(),
        "size": len(value),
    }
    return record


def external_record(root_name: str, relative: str, kind: str, source: Path) -> dict:
    path = real_file(source, f"Android corresponding-source input {relative}")
    digest, size = sha256_file(path)
    if size <= 0 or size > MAX_EXTERNAL_FILE_SIZE:
        fail(f"Android corresponding-source input is empty or oversized: {relative}")
    return {
        "kind": kind,
        "path": (PurePosixPath(root_name) / safe_path(relative, "Archive input path")).as_posix(),
        "sha256": digest,
        "size": size,
    }


def load_legal_manifest(
    legal_manifest: Path,
    static_path: Path,
    static_policy: dict,
    archive_components: dict[str, list[str]],
) -> tuple[Path, dict, dict[str, dict], dict[str, dict]]:
    legal_manifest = real_file(legal_manifest, "Android legal evidence manifest")
    manifest = read_json(legal_manifest, "Android legal evidence manifest")
    expected_keys = {
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
    static_digest, _ = sha256_file(static_path)
    review_status = manifest.get("reviewStatus")
    if (
        frozenset(manifest)
        not in {frozenset(expected_keys), frozenset(expected_keys | {"automaticLicenseScan"})}
        or manifest.get("schemaVersion") != 1
        or manifest.get("vlcRevision") != static_policy["vlcRevision"]
        or manifest.get("ndkRevision") != static_policy["ndkRevision"]
        or review_status not in {
            "candidate-linked-member-review-pending",
            "automatic-forbidden-license-scan-passed",
            "approved",
        }
        or manifest.get("staticComponentPolicy")
        != {"path": STATIC_POLICY_PATH.as_posix(), "sha256": static_digest}
        or not isinstance(manifest.get("files"), list)
        or not isinstance(manifest.get("candidateLicenseInventorySpdx"), list)
    ):
        fail("Android legal evidence does not match the corresponding-source inputs.")
    publishable = review_status in {"automatic-forbidden-license-scan-passed", "approved"}
    if publishable != isinstance(manifest.get("effectiveLicenseSpdx"), str):
        fail("Android legal evidence review state and effective license disagree.")
    if not publishable and manifest.get("effectiveLicenseSpdx") is not None:
        fail("Candidate Android legal evidence must not declare an effective license.")
    if review_status == "automatic-forbidden-license-scan-passed" and manifest.get(
        "automaticLicenseScan"
    ) != {
        "forbiddenPrefixes": ["GPL-", "AGPL-", "LicenseRef-NonFree", "unknown"],
        "result": "passed",
        "scanner": "scripts/verify_fast_release_licenses.py",
    }:
        fail("Android automatic license scan evidence is incomplete.")

    audits = manifest.get("abiAudits")
    if not isinstance(audits, list) or len(audits) != 2:
        fail("Android legal evidence must bind both ABI audits.")
    audit_by_target: dict[str, dict] = {}
    for audit in audits:
        if (
            not isinstance(audit, dict)
            or set(audit) != {"target", "reportSha256", "libvlcSha256"}
            or audit.get("target") not in AUDIT_TARGETS
            or not SHA256.fullmatch(str(audit.get("reportSha256")))
            or not SHA256.fullmatch(str(audit.get("libvlcSha256")))
            or audit["target"] in audit_by_target
        ):
            fail("Android legal evidence ABI audit identity is invalid.")
        audit_by_target[audit["target"]] = audit
    if tuple(audit_by_target) != AUDIT_TARGETS:
        fail("Android legal evidence ABI audit order is not canonical.")

    components = manifest.get("components")
    if not isinstance(components, list) or len(components) != 55:
        fail("Android legal evidence component closure is incomplete.")
    source_by_name: dict[str, dict] = {}
    component_ids: list[str] = []
    expected_ids = {"android-ndk-llvm-runtime", *static_policy["contribComponents"]}
    for component in components:
        if not isinstance(component, dict):
            fail("Android legal evidence component entry is invalid.")
        component_id = component.get("id")
        if not isinstance(component_id, str) or component_id not in expected_ids:
            fail("Android legal evidence component identifier is invalid.")
        component_ids.append(component_id)
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
            fail(f"Android legal evidence contrib source entry is invalid: {component_id}")
        actual_names: list[str] = []
        for entry in sources:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
                fail(f"Android legal evidence source archive entry is invalid: {component_id}")
            path = entry.get("path")
            prefix = "vlc-contrib-tarballs/"
            name = path[len(prefix):] if isinstance(path, str) and path.startswith(prefix) else ""
            if (
                not SOURCE_ARCHIVE.fullmatch(name)
                or not SHA256.fullmatch(str(entry.get("sha256")))
                or not isinstance(entry.get("size"), int)
                or entry["size"] <= 0
            ):
                fail(f"Android legal evidence source archive hash is invalid: {component_id}")
            previous = source_by_name.setdefault(name, entry)
            if previous != entry:
                fail(f"Android legal evidence source archive identity disagrees: {name}")
            actual_names.append(name)
        if actual_names != expected_names:
            fail(f"Android legal evidence source archive mapping differs: {component_id}")
    if component_ids != sorted(expected_ids) or set(source_by_name) != set(archive_components):
        fail("Android legal evidence source archive closure is not canonical.")
    return legal_manifest, manifest, source_by_name, audit_by_target


def validate_audit(
    path: Path,
    target: str,
    expected: dict,
    static_path: Path,
    static_policy: dict,
    source_policy: dict,
) -> Path:
    path = real_file(path, f"Android {target} link audit")
    digest, _ = sha256_file(path)
    if digest != expected["reportSha256"]:
        fail(f"Android link audit differs from the legal manifest: {target}")
    audit = read_json(path, f"Android {target} link audit")
    static_digest, _ = sha256_file(static_path)
    expected_abi = "arm64-v8a" if target == "android-arm64-v8a" else "armeabi-v7a"
    if (
        audit.get("schemaVersion") != 1
        or audit.get("target") != target
        or audit.get("abi") != expected_abi
        or audit.get("androidApi") != 21
        or audit.get("vlcRevision") != source_policy["vlc"]["revision"]
        or audit.get("libvlcjniRevision") != source_policy["libvlcjni"]["revision"]
        or audit.get("ndkRevision") != NDK_REVISION
        or audit.get("reviewStatus")
        not in {"candidate-source-mapped-license-review-pending", "approved"}
        or not isinstance(audit.get("modules"), list)
        or not audit["modules"]
        or not isinstance(audit.get("staticArchives"), list)
        or not audit["staticArchives"]
        or not isinstance(audit.get("staticComponents"), list)
        or len(audit["staticComponents"]) != 55
        or not isinstance(audit.get("libvlc"), dict)
        or audit["libvlc"].get("sha256") != expected["libvlcSha256"]
        or not isinstance(audit.get("evidence"), dict)
        or audit["evidence"].get("staticComponentPolicy")
        != {"path": STATIC_POLICY_PATH.as_posix(), "sha256": static_digest}
    ):
        fail(f"Android link audit identity is incomplete: {target}")
    return path


def rebuild_text(version: str, tested_commit: str, ndk_revision: str) -> bytes:
    return (
        "# Android libVLC 4 corresponding source\n\n"
        f"Release: {version}\n"
        f"KMediaVlc commit: {tested_commit}\n\n"
        "This archive contains the complete tracked KMediaVlc, VLC, and libvlcjni "
        "trees used by the Android build, all selected contrib source archives, "
        "the independently verified NDK runtime source supplement, and both ABI "
        "link-audit reports.\n\n"
        "Rebuild prerequisites:\n\n"
        f"- Android NDK {ndk_revision}\n"
        "- CMake 4.1.2\n"
        "- the ordinary host tools checked by scripts/build_vlc_android.sh\n\n"
        "From this directory, run:\n\n"
        "```shell\n"
        "bash sources/kmediavlc/scripts/build_vlc_android.sh sources/vlc "
        "sources/libvlcjni /path/to/android-ndk-r29 /path/to/cmake "
        "/path/to/audit-work /path/to/candidate-output\n"
        "```\n\n"
        "The script applies the committed libvlcjni policy patch, rebuilds contribs "
        "from the packaged tarballs, emits fresh ABI audits, and builds the narrow "
        "KMediaVlc JNI bridge. The nested NDK archive provides source for the linked "
        "LLVM runtimes; it is not a replacement for the NDK compiler binaries.\n"
    ).encode("utf-8")


def tar_info(
    path: PurePosixPath,
    epoch: int,
    *,
    directory: bool = False,
    size: int = 0,
    mode: int = 0o644,
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
    vlc: Path,
    libvlcjni: Path,
    contrib_tarballs: Path,
    ndk_source_archive: Path,
    llvm_project: Path,
    llvm_android: Path,
    legal_manifest: Path,
    arm64_audit: Path,
    armv7_audit: Path,
    output: Path,
    tested_commit: str,
    version: str,
    epoch: int,
) -> str:
    root = real_directory(root, "KMediaVlc source root")
    output = output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        fail("Android corresponding-source output must be a new file in a real directory.")
    if not COMMIT.fullmatch(tested_commit):
        fail("Android corresponding source requires an exact tested KMediaVlc commit.")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Android corresponding source requires immutable non-SNAPSHOT SemVer.")
    normalized_epoch = max(epoch, MIN_EPOCH)
    (
        policy_path,
        policy,
        static_path,
        static_policy,
        recipe_path,
        _,
        archive_components,
    ) = load_policies(root)
    root_name = policy["archiveRoot"]
    if normalized_epoch != git_commit_epoch(root):
        fail("Android corresponding source epoch must equal the tested commit timestamp.")

    checkouts = {"kmediavlc": root, "libvlcjni": libvlcjni, "vlc": vlc}
    git_entries: dict[str, list[GitSourceFile]] = {}
    source_identities: list[dict] = []
    for source_id in SOURCE_IDS:
        entries, identity = inspect_checkout(
            source_id, checkouts[source_id], policy["sourceInputs"][source_id], tested_commit
        )
        git_entries[source_id] = entries
        source_identities.append(identity)

    legal_manifest, legal, legal_sources, legal_audits = load_legal_manifest(
        legal_manifest, static_path, static_policy, archive_components
    )
    audit_paths = {
        "android-arm64-v8a": validate_audit(
            arm64_audit,
            "android-arm64-v8a",
            legal_audits["android-arm64-v8a"],
            static_path,
            static_policy,
            policy["sourceInputs"],
        ),
        "android-armeabi-v7a": validate_audit(
            armv7_audit,
            "android-armeabi-v7a",
            legal_audits["android-armeabi-v7a"],
            static_path,
            static_policy,
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

    file_records: list[dict] = []
    git_data: dict[PurePosixPath, tuple[GitSourceFile, dict, bytes]] = {}
    for source_id in SOURCE_IDS:
        for entry, value in git_blob_values(checkouts[source_id], git_entries[source_id]):
            record = git_record(root_name, entry, value)
            path = PurePosixPath(record["path"])
            if path in git_data:
                fail(f"Duplicate Android corresponding-source path: {path}")
            git_data[path] = (entry, record, value)
            file_records.append(record)

    contrib_tarballs = real_directory(contrib_tarballs, "VLC contrib tarball directory")
    external_files: dict[PurePosixPath, tuple[Path, dict]] = {}
    contrib_records: list[dict] = []
    for name in sorted(archive_components):
        source = real_file(contrib_tarballs / name, f"VLC contrib source archive {name}")
        try:
            source.relative_to(contrib_tarballs)
        except ValueError:
            fail(f"VLC contrib source archive escaped its directory: {name}")
        relative = f"sources/vlc-contrib-tarballs/{name}"
        record = external_record(root_name, relative, "contrib-source-archive", source)
        legal_entry = legal_sources[name]
        if record["sha256"] != legal_entry["sha256"] or record["size"] != legal_entry["size"]:
            fail(f"VLC contrib source archive differs from the legal audit: {name}")
        path = PurePosixPath(record["path"])
        external_files[path] = (source, record)
        file_records.append(record)
        contrib_records.append(
            {
                "name": name,
                "path": record["path"],
                "sha256": record["sha256"],
                "size": record["size"],
                "components": archive_components[name],
            }
        )

    ndk_relative = policy["ndkSourcePackage"]["archivePath"]
    ndk_record = external_record(
        root_name, ndk_relative, "ndk-source-package", ndk_source_archive
    )
    if ndk_record["sha256"] != ndk_digest:
        fail("Android NDK source archive changed after independent verification.")
    external_files[PurePosixPath(ndk_record["path"])] = (ndk_source_archive, ndk_record)
    file_records.append(ndk_record)

    legal_relative = policy["buildEvidence"]["legalManifestPath"]
    legal_record = external_record(
        root_name, legal_relative, "legal-evidence-manifest", legal_manifest
    )
    external_files[PurePosixPath(legal_record["path"])] = (legal_manifest, legal_record)
    file_records.append(legal_record)
    audit_records: list[dict] = []
    for target in AUDIT_TARGETS:
        relative = policy["buildEvidence"]["linkAudits"][target]
        record = external_record(root_name, relative, "link-audit", audit_paths[target])
        if record["sha256"] != legal_audits[target]["reportSha256"]:
            fail(f"Android link audit changed after validation: {target}")
        external_files[PurePosixPath(record["path"])] = (audit_paths[target], record)
        file_records.append(record)
        audit_records.append({"target": target, **record})

    checksum_records = sorted(
        (record for record in file_records if record["kind"] != "git-source"),
        key=lambda record: record["path"],
    )
    checksum_data = "".join(
        f"{record['sha256']}  "
        f"{PurePosixPath(record['path']).relative_to(PurePosixPath(root_name)).as_posix()}\n"
        for record in checksum_records
    ).encode("ascii")
    generated_data = {
        PurePosixPath(root_name) / "REBUILD.md": rebuild_text(
            version, tested_commit, static_policy["ndkRevision"]
        ),
        PurePosixPath(root_name) / "SOURCE-SHA256SUMS": checksum_data,
    }
    for path, value in generated_data.items():
        file_records.append(
            {
                "kind": "generated",
                "path": path.as_posix(),
                "sha256": hashlib.sha256(value).hexdigest(),
                "size": len(value),
            }
        )
    file_records.sort(key=lambda record: record["path"])
    if len({record["path"] for record in file_records}) != len(file_records):
        fail("Android corresponding-source file inventory contains duplicate paths.")

    policy_digest, _ = sha256_file(policy_path)
    static_digest, _ = sha256_file(static_path)
    recipe_digest, _ = sha256_file(recipe_path)
    manifest = {
        "schemaVersion": 1,
        "target": "android-arm-corresponding-source",
        "releaseVersion": version,
        "testedCommit": tested_commit,
        "sourceDateEpoch": normalized_epoch,
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
        "files": file_records,
    }
    manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path = PurePosixPath(root_name) / "SOURCE-MANIFEST.json"
    all_file_paths = {PurePosixPath(record["path"]) for record in file_records} | {
        manifest_path
    }
    directories = {PurePosixPath(root_name)}
    for path in all_file_paths:
        directories.update(parent for parent in path.parents if parent.parts)

    partial = output.with_name(output.name + ".partial")
    if partial.exists() or partial.is_symlink():
        fail("Android corresponding-source partial output already exists.")
    try:
        with partial.open("xb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                compresslevel=9,
                mtime=normalized_epoch,
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
                    for directory in sorted(
                        directories, key=lambda value: (len(value.parts), value.as_posix())
                    ):
                        archive.addfile(tar_info(directory, normalized_epoch, directory=True))
                    for path in sorted(all_file_paths):
                        if path == manifest_path:
                            archive.addfile(
                                tar_info(path, normalized_epoch, size=len(manifest_data)),
                                io.BytesIO(manifest_data),
                            )
                        elif path in generated_data:
                            value = generated_data[path]
                            archive.addfile(
                                tar_info(path, normalized_epoch, size=len(value)),
                                io.BytesIO(value),
                            )
                        elif path in git_data:
                            entry, expected, value = git_data[path]
                            if git_record(root_name, entry, value) != expected:
                                fail(f"Android Git source object changed during packaging: {path}")
                            archive.addfile(
                                tar_info(
                                    path,
                                    normalized_epoch,
                                    size=len(value),
                                    mode=0o755 if entry.git_mode == "100755" else 0o644,
                                ),
                                io.BytesIO(value),
                            )
                        else:
                            source_path, expected = external_files[path]
                            with source_path.open("rb") as source:
                                verified = VerifiedReader(source)
                                archive.addfile(
                                    tar_info(path, normalized_epoch, size=expected["size"]),
                                    verified,
                                )
                            if (
                                verified.size != expected["size"]
                                or verified.digest.hexdigest() != expected["sha256"]
                            ):
                                fail(f"Android source input changed during packaging: {path}")
        for identity in source_identities:
            checkout = checkouts[identity["id"]]
            try:
                revision = git_output(checkout, "rev-parse", "HEAD").decode("ascii").strip()
                tree = git_output(checkout, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
            except UnicodeDecodeError as error:
                raise ValueError("Android source Git identity changed during packaging.") from error
            if (
                revision != identity["revision"]
                or tree != identity["tree"]
                or git_output(checkout, "status", "--porcelain", "--untracked-files=no")
            ):
                fail(f"Android source checkout changed during packaging: {identity['id']}")
        partial.rename(output)
    except BaseException:
        if partial.exists() and not partial.is_symlink():
            partial.unlink()
        raise
    digest, _ = sha256_file(output)
    return digest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vlc", type=Path, required=True)
    parser.add_argument("--libvlcjni", type=Path, required=True)
    parser.add_argument("--contrib-tarballs", type=Path, required=True)
    parser.add_argument("--ndk-source-archive", type=Path, required=True)
    parser.add_argument("--llvm-project", type=Path, required=True)
    parser.add_argument("--llvm-android", type=Path, required=True)
    parser.add_argument("--legal-manifest", type=Path, required=True)
    parser.add_argument("--arm64-audit", type=Path, required=True)
    parser.add_argument("--armv7-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tested-commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    arguments = parser.parse_args()
    digest = package(
        arguments.root,
        arguments.vlc,
        arguments.libvlcjni,
        arguments.contrib_tarballs,
        arguments.ndk_source_archive,
        arguments.llvm_project,
        arguments.llvm_android,
        arguments.legal_manifest,
        arguments.arm64_audit,
        arguments.armv7_audit,
        arguments.output,
        arguments.tested_commit,
        arguments.version,
        arguments.epoch,
    )
    print(f"{digest}  {arguments.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
