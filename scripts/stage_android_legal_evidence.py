#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath


VLC_REVISION = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
NDK_REVISION = "29.0.14206865"
NDK_SOURCE_STATUS = "exact-source-revisions-recorded-source-package-pending"
LLVM_PROJECT_REVISION = "386af4a5c64ab75eaee2448dc38f2e34a40bfed0"
LLVM_ANDROID_REVISION = "1dab3288f660d43a6cb2479107e2b54b3ab0a2a1"
NDK_SOURCE_PACKAGE = {
    "archiveRoot": "android-ndk-runtime-source",
    "format": "deterministic-tar-gzip-v1",
    "verifiedSourceStatus": "corresponding-source-mapped",
    "sources": {
        "llvm-android-build": {"scope": "complete-tree", "paths": []},
        "llvm-project": {
            "scope": "selected-subtrees",
            "paths": [
                "LICENSE.TXT",
                "README.md",
                "cmake",
                "compiler-rt",
                "libcxx",
                "libcxxabi",
                "libunwind",
                "llvm/cmake",
                "llvm/include",
                "llvm/utils/lit",
                "runtimes",
                "third-party",
            ],
        },
    },
}
REVIEW_STATUS = "candidate-source-mapped-license-review-pending"
COMPONENT_REVIEW_STATUS = "pending-linked-member-review"
LEGAL_REVIEW_STATUS = "candidate-linked-member-review-pending"
EXPECTED_TARGETS = {"android-arm64-v8a", "android-armeabi-v7a"}
SAFE_COMPONENT = re.compile(r"[a-z0-9][a-z0-9-]+")
SAFE_SOURCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)")


def fail(message: str) -> None:
    raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def real_file(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        fail(f"{description} must be a real non-empty file.")
    return path.resolve(strict=True)


def real_directory(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        fail(f"{description} must be a real directory.")
    return path.resolve(strict=True)


def ndk_host_prebuilt(ndk_directory: Path) -> tuple[str, Path]:
    prebuilt = real_directory(
        ndk_directory / "toolchains/llvm/prebuilt", "Android NDK host toolchain directory"
    )
    hosts = sorted(
        path.name for path in prebuilt.iterdir() if path.is_dir() and not path.is_symlink()
    )
    if len(hosts) != 1 or not re.fullmatch(r"(?:darwin|linux)-x86_64", hosts[0]):
        fail("Android NDK must contain exactly one supported host toolchain.")
    return hosts[0], real_directory(
        prebuilt / hosts[0], "Android NDK selected host toolchain directory"
    )


def read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(real_file(path, description).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f"{description} is invalid.") from error
    if not isinstance(value, dict):
        fail(f"{description} root must be an object.")
    return value


def safe_relative(value: str, description: str) -> PurePosixPath:
    if not isinstance(value, str):
        fail(f"{description} path must be text.")
    relative = PurePosixPath(value)
    if (
        relative.is_absolute()
        or relative.as_posix() != value
        or not relative.parts
        or ".." in relative.parts
        or any(
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", part)
            for part in relative.parts
        )
    ):
        fail(f"{description} path is unsafe: {value!r}")
    return relative


def read_archive_member(source: Path, source_name: str, evidence_path: str) -> bytes:
    relative = safe_relative(evidence_path, "Android license evidence")
    try:
        with tarfile.open(source, mode="r:*") as archive:
            matches = []
            for member in archive.getmembers():
                parts = PurePosixPath(member.name).parts
                if len(parts) >= 2 and tuple(parts[1:]) == relative.parts:
                    matches.append(member)
            if len(matches) != 1:
                fail(
                    "Android license evidence is missing or ambiguous: "
                    f"{source_name}!/{evidence_path}"
                )
            member = matches[0]
            if not member.isfile() or member.issym() or member.islnk() or not 0 < member.size <= 2_000_000:
                fail(
                    "Android license evidence must be a bounded regular file: "
                    f"{source_name}!/{evidence_path}"
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                fail(f"Android license evidence is unreadable: {source_name}")
            with extracted:
                value = extracted.read(member.size + 1)
            if len(value) != member.size:
                fail(f"Android license evidence size changed while reading: {source_name}")
            return value
    except (OSError, tarfile.TarError) as error:
        raise ValueError(f"VLC contrib source archive is unreadable: {source_name}") from error


def validate_report(
    report: dict, report_path: Path, policy_sha256: str, expected_component_count: int
) -> list[dict]:
    target = report.get("target")
    if (
        target not in EXPECTED_TARGETS
        or report.get("vlcRevision") != VLC_REVISION
        or report.get("ndkRevision") != NDK_REVISION
        or report.get("reviewStatus") != REVIEW_STATUS
    ):
        fail(f"Android link audit identity or review state is invalid: {report_path.name}")
    libvlc = report.get("libvlc")
    if not isinstance(libvlc, dict) or libvlc.get("effectiveLicenseSpdx") is not None:
        fail(f"Android link audit prematurely declares an effective license: {report_path.name}")
    policy = report.get("evidence", {}).get("staticComponentPolicy")
    if policy != {
        "path": "compliance/policy/android-static-components.json",
        "sha256": policy_sha256,
    }:
        fail(f"Android link audit is not bound to the current component policy: {report_path.name}")
    components = report.get("staticComponents")
    if not isinstance(components, list) or len(components) != expected_component_count:
        fail(f"Android link audit component closure is incomplete: {report_path.name}")
    ids = []
    for component in components:
        if not isinstance(component, dict):
            fail(f"Android link audit component is invalid: {report_path.name}")
        component_id = component.get("id")
        if not isinstance(component_id, str) or not SAFE_COMPONENT.fullmatch(component_id):
            fail(f"Android link audit component identifier is unsafe: {report_path.name}")
        if component.get("licenseReviewStatus") != COMPONENT_REVIEW_STATUS:
            fail(f"Android link audit component review state is invalid: {component_id}")
        licenses = component.get("candidateLicenseSpdx")
        if not isinstance(licenses, list) or licenses != sorted(set(licenses)) or not licenses:
            fail(f"Android link audit candidate SPDX set is invalid: {component_id}")
        ids.append(component_id)
    if ids != sorted(set(ids)):
        fail(f"Android link audit components are not canonical: {report_path.name}")
    return components


def write_file(root: Path, relative: str, value: bytes) -> dict:
    safe = safe_relative(relative, "Android staged legal evidence")
    destination = root.joinpath(*safe.parts)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        fail(f"Android staged legal evidence path is duplicated: {relative}")
    with destination.open("xb") as target:
        target.write(value)
    return {"path": relative, "sha256": sha256_bytes(value), "size": len(value)}


def stage(
    root: Path,
    vlc_source: Path,
    ndk_directory: Path,
    audit_paths: list[Path],
    output: Path,
) -> dict:
    root = real_directory(root, "KMediaVlc source root")
    vlc_source = real_directory(vlc_source, "VLC source root")
    ndk_directory = real_directory(ndk_directory, "Android NDK root")
    if len(audit_paths) != 2:
        fail("Android legal evidence requires exactly two ABI audits.")
    output = output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        fail("Android legal evidence output must be a new directory under a real parent.")

    policy_path = real_file(
        root / "compliance/policy/android-static-components.json",
        "Android static component policy",
    )
    policy = read_json(policy_path, "Android static component policy")
    policy_sha256 = sha256(policy_path)
    if (
        policy.get("vlcRevision") != VLC_REVISION
        or policy.get("ndkRevision") != NDK_REVISION
        or policy.get("reviewStatus") != "source-mapped-license-and-notice-review-pending"
    ):
        fail("Android static component policy identity or review state is invalid.")
    contrib_components = policy.get("contribComponents")
    ndk_components = policy.get("ndkComponents")
    if not isinstance(contrib_components, dict) or not isinstance(ndk_components, dict):
        fail("Android static component policy has no closed component maps.")
    if set(ndk_components) != {"android-ndk-llvm-runtime"}:
        fail("Android NDK component policy is not closed.")
    ndk_component_policy = ndk_components["android-ndk-llvm-runtime"]
    ndk_source_inputs = policy.get("ndkSourceInputs")
    ndk_source_package = policy.get("ndkSourcePackage")
    ndk_release = policy.get("ndkReleaseProvenance")
    if (
        not isinstance(ndk_source_inputs, dict)
        or list(ndk_source_inputs) != ["llvm-android-build", "llvm-project"]
        or ndk_source_inputs["llvm-android-build"].get("revision")
        != LLVM_ANDROID_REVISION
        or ndk_source_inputs["llvm-project"].get("revision")
        != LLVM_PROJECT_REVISION
        or ndk_source_package != NDK_SOURCE_PACKAGE
        or ndk_component_policy.get("sourceInputs") != list(ndk_source_inputs)
        or ndk_component_policy.get("evidenceFiles")
        != ["NOTICE", "NOTICE.toolchain", "source.properties"]
        or ndk_component_policy.get("toolchainEvidenceFiles")
        != ["AndroidVersion.txt", "clang_source_info.md"]
        or ndk_component_policy.get("sourceStatus") != NDK_SOURCE_STATUS
        or not isinstance(ndk_release, dict)
        or ndk_release.get("releaseName") != "r29"
        or ndk_release.get("clangVersion") != "21.0.0"
        or ndk_release.get("clangRevision") != "r563880c"
    ):
        fail("Android NDK source provenance is incomplete.")
    ndk_host_tag, ndk_host_directory = ndk_host_prebuilt(ndk_directory)
    prebuilt_tags = ndk_release.get("prebuiltTags")
    if not isinstance(prebuilt_tags, dict) or ndk_host_tag not in prebuilt_tags:
        fail("Android NDK host prebuilt is absent from release provenance.")
    expected_component_count = len(contrib_components) + len(ndk_components)

    reports = []
    component_views = []
    targets = set()
    for audit_path in audit_paths:
        audit_path = real_file(audit_path, "Android ABI link audit")
        report = read_json(audit_path, "Android ABI link audit")
        component_views.append(
            validate_report(report, audit_path, policy_sha256, expected_component_count)
        )
        targets.add(report["target"])
        reports.append((audit_path, report))
    if targets != EXPECTED_TARGETS or component_views[0] != component_views[1]:
        fail("Android ABI audits do not have identical static component evidence.")

    partial = output.with_name(output.name + ".partial")
    if partial.exists() or partial.is_symlink():
        fail("Android legal evidence partial output already exists.")
    partial.mkdir()

    tarballs = real_directory(vlc_source / "contrib/tarballs", "VLC contrib tarball directory")
    manifest_components = []
    staged_files = []
    for component in component_views[0]:
        component_id = component["id"]
        component_files = []
        component_sources = []
        component_source_inputs = []
        component_binary_provenance = None
        component_source_status = "source-archive-hashes-recorded"
        if component["kind"] == "VLC_CONTRIB":
            sources = component.get("sourceArchives")
            if not isinstance(sources, list) or not sources:
                fail(f"Android contrib component has no source archives: {component_id}")
            for source_entry in sources:
                source_path = source_entry.get("path")
                prefix = "vlc-contrib-tarballs/"
                if not isinstance(source_path, str) or not source_path.startswith(prefix):
                    fail(f"Android contrib source path is invalid: {component_id}")
                source_name = source_path[len(prefix) :]
                if not SAFE_SOURCE.fullmatch(source_name):
                    fail(f"Android contrib source name is unsafe: {component_id}")
                source = real_file(tarballs / source_name, "VLC contrib source archive")
                if source.parent != tarballs:
                    fail("Android contrib source archive escaped its closed tarball directory.")
                if source_entry.get("sha256") != sha256(source) or source_entry.get("size") != source.stat().st_size:
                    fail(f"Android contrib source hash differs from the ABI audits: {source_name}")
                component_sources.append(
                    {
                        "path": source_path,
                        "sha256": source_entry["sha256"],
                        "size": source_entry["size"],
                    }
                )
                evidence_entries = source_entry.get("licenseEvidence")
                expected_paths = policy.get("licenseEvidence", {}).get(source_name)
                if not isinstance(evidence_entries, list) or not isinstance(expected_paths, list):
                    fail(f"Android contrib license evidence is incomplete: {source_name}")
                by_path = {entry.get("path"): entry for entry in evidence_entries}
                expected_full_paths = {
                    f"vlc-contrib-tarballs/{source_name}!/{value}" for value in expected_paths
                }
                if set(by_path) != expected_full_paths or len(by_path) != len(evidence_entries):
                    fail(f"Android contrib license evidence differs from policy: {source_name}")
                for evidence_path in expected_paths:
                    value = read_archive_member(source, source_name, evidence_path)
                    staged_path = (
                        f"contrib/{component_id}/{source_name}/{evidence_path}"
                    )
                    staged = write_file(partial, staged_path, value)
                    audited = by_path[
                        f"vlc-contrib-tarballs/{source_name}!/{evidence_path}"
                    ]
                    if staged["sha256"] != audited.get("sha256") or staged["size"] != audited.get("size"):
                        fail(f"Android staged license evidence differs from its ABI audit: {staged_path}")
                    component_files.append(staged)
                    staged_files.append(staged)
        elif component["kind"] == "NDK_TOOLCHAIN":
            component_source_status = component.get("sourceStatus")
            if component_source_status != NDK_SOURCE_STATUS:
                fail("Android NDK corresponding-source status is invalid.")
            expected_source_inputs = [
                {"id": source_id, **ndk_source_inputs[source_id]}
                for source_id in ndk_component_policy["sourceInputs"]
            ]
            component_source_inputs = component.get("sourceInputs")
            if component_source_inputs != expected_source_inputs:
                fail("Android NDK source inputs differ from the closed policy.")
            expected_binary_provenance = {
                key: value for key, value in ndk_release.items() if key != "prebuiltTags"
            }
            expected_binary_provenance["prebuilt"] = {
                "hostTag": ndk_host_tag,
                **prebuilt_tags[ndk_host_tag],
            }
            component_binary_provenance = component.get("binaryProvenance")
            if component_binary_provenance != expected_binary_provenance:
                fail("Android NDK binary provenance differs from the selected r29 host prebuilt.")
            evidence_entries = component.get("evidenceFiles")
            expected_root_evidence = set(ndk_component_policy["evidenceFiles"])
            expected_toolchain_evidence = set(
                ndk_component_policy["toolchainEvidenceFiles"]
            )
            expected_evidence_paths = {
                f"ndk/{name}" for name in expected_root_evidence | expected_toolchain_evidence
            }
            if not isinstance(evidence_entries, list) or len(evidence_entries) != len(
                expected_evidence_paths
            ):
                fail("Android NDK legal evidence is incomplete.")
            for audited in evidence_entries:
                source_path = audited.get("path")
                if source_path not in expected_evidence_paths:
                    fail("Android NDK legal evidence path is invalid.")
                source_name = source_path[len("ndk/") :]
                source_root = (
                    ndk_directory
                    if source_name in expected_root_evidence
                    else ndk_host_directory
                )
                source = real_file(source_root / source_name, "Android NDK legal evidence")
                if source.parent != source_root:
                    fail("Android NDK legal evidence escaped its closed root.")
                value = source.read_bytes()
                staged = write_file(partial, source_path, value)
                if staged["sha256"] != audited.get("sha256") or staged["size"] != audited.get("size"):
                    fail(f"Android staged NDK evidence differs from its ABI audit: {source_path}")
                component_files.append(staged)
                staged_files.append(staged)
        else:
            fail(f"Android static component kind is unsupported: {component_id}")
        manifest_components.append(
            {
                "id": component_id,
                "kind": component["kind"],
                "version": component["version"],
                "candidateLicenseSpdx": component["candidateLicenseSpdx"],
                "licenseReviewStatus": component["licenseReviewStatus"],
                "sourceArchives": component_sources,
                "sourceInputs": component_source_inputs,
                "sourceStatus": component_source_status,
                "binaryProvenance": component_binary_provenance,
                "files": component_files,
            }
        )

    expected_file_count = sum(
        len(paths) for paths in policy.get("licenseEvidence", {}).values()
    ) + sum(
        len(component["evidenceFiles"]) + len(component["toolchainEvidenceFiles"])
        for component in ndk_components.values()
    )
    if (
        len(staged_files) != expected_file_count
        or len({entry["path"] for entry in staged_files}) != expected_file_count
    ):
        fail("Android legal evidence file count differs from the closed policy.")
    audit_entries = [
        {
            "target": report["target"],
            "reportSha256": sha256(audit_path),
            "libvlcSha256": report["libvlc"]["sha256"],
        }
        for audit_path, report in sorted(reports, key=lambda value: value[1]["target"])
    ]
    license_inventory = sorted(
        {
            license_id
            for component in manifest_components
            for license_id in component["candidateLicenseSpdx"]
        }
    )
    manifest = {
        "schemaVersion": 1,
        "reviewStatus": LEGAL_REVIEW_STATUS,
        "vlcRevision": VLC_REVISION,
        "ndkRevision": NDK_REVISION,
        "effectiveLicenseSpdx": None,
        "candidateLicenseInventorySpdx": license_inventory,
        "staticComponentPolicy": {
            "path": "compliance/policy/android-static-components.json",
            "sha256": policy_sha256,
        },
        "abiAudits": audit_entries,
        "components": manifest_components,
        "files": sorted(staged_files, key=lambda entry: entry["path"]),
    }
    manifest_path = partial / "android-static-legal.json"
    with manifest_path.open("x", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    partial.rename(output)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vlc-source", type=Path, required=True)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--audit", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    manifest = stage(
        arguments.root,
        arguments.vlc_source,
        arguments.ndk,
        arguments.audit,
        arguments.output,
    )
    print(
        f"Staged Android legal evidence for {len(manifest['components'])} components and "
        f"{len(manifest['files'])} files."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
