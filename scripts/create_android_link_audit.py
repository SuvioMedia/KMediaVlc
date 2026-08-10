#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath


VLC_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
LIBVLCJNI_REVISION = "a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21"
NDK_REVISION = "29.0.14206865"
NDK_SOURCE_STATUS = "exact-source-revisions-recorded-source-package-pending"
NDK_ROOT_EVIDENCE = ["NOTICE", "NOTICE.toolchain", "source.properties"]
NDK_TOOLCHAIN_EVIDENCE = ["AndroidVersion.txt", "clang_source_info.md"]
LLVM_PROJECT_REVISION = "386af4a5c64ab75eaee2448dc38f2e34a40bfed0"
LLVM_ANDROID_REVISION = "1dab3288f660d43a6cb2479107e2b54b3ab0a2a1"
SUPPORTED_TARGETS = {
    "arm64-v8a": "aarch64-linux-android",
    "armeabi-v7a": "arm-linux-androideabi",
}
ABI_TOOLCHAIN_NAMES = {
    "arm64-v8a": {
        "runtimeArch": "aarch64",
        "builtinsArch": "aarch64",
        "targetTuple": "aarch64-linux-android",
    },
    "armeabi-v7a": {
        "runtimeArch": "arm",
        "builtinsArch": "arm",
        "targetTuple": "arm-linux-androideabi",
    },
}
LGPL_TEXT = (
    "Licensed under the terms of the GNU Lesser General Public License, "
    "version 2.1 or later."
)
GPL_TEXT = "Licensed under the terms of the GNU General Public License, version 2 or later."
MODULE_ENTRY = re.compile(r"^\s*vlc_entry__([a-z0-9_]+),\s*$")
ARCHIVE_INPUT = re.compile(r"^(?P<archive>/[^\n]*?\.a)\((?P<member>[^()\n]+)\):")
NEEDED_ENTRY = re.compile(r"Shared library: \[([^\]\r\n]+)\]")
LOAD_ENTRY = re.compile(r"^\s*LOAD\s+.*\s+(0x[0-9a-fA-F]+)\s*$")
SAFE_MODULE = re.compile(r"[a-z0-9_]+")
SAFE_NEEDED = re.compile(r"lib[A-Za-z0-9_+.-]+\.so")
SAFE_COMPONENT = re.compile(r"[a-z0-9][a-z0-9-]+")
SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]+")
SAFE_SOURCE_ARCHIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)")
SAFE_CONTRIB_ARCHIVE = re.compile(r"vlc-contrib/lib/lib[A-Za-z0-9_+.-]+\.a")
SAFE_HOST_TAG = re.compile(r"(?:darwin|linux)-x86_64")
EXPECTED_NDK_TEMPLATES = {
    "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
    "libclang_rt.builtins-{builtinsArch}-android.a",
    "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
    "{runtimeArch}/libunwind.a",
    "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
    "libc++_static.a",
    "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
    "libc++abi.a",
}
EXPECTED_NDK_SOURCE_PATHS = {
    "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
    "libclang_rt.builtins-{builtinsArch}-android.a": [
        "llvm-project/compiler-rt/lib/builtins"
    ],
    "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
    "{runtimeArch}/libunwind.a": ["llvm-project/libunwind", "llvm-project/runtimes"],
    "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
    "libc++_static.a": ["llvm-project/libcxx", "llvm-project/runtimes"],
    "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
    "libc++abi.a": ["llvm-project/libcxxabi", "llvm-project/runtimes"],
}
EXPECTED_NDK_SOURCE_INPUTS = {
    "llvm-android-build": {
        "repository": "https://android.googlesource.com/toolchain/llvm_android",
        "revision": LLVM_ANDROID_REVISION,
        "tree": "9cf89bb8f12fb9e993e81d2ee2d43f2bc8819d53",
        "role": "android-runtime-build-and-patch-set",
        "requiredPaths": [
            "do_build.py",
            "patches",
            "src/llvm_android/android_version.py",
            "src/llvm_android/builders.py",
        ],
    },
    "llvm-project": {
        "repository": "https://android.googlesource.com/toolchain/llvm-project",
        "revision": LLVM_PROJECT_REVISION,
        "tree": "a49e40b73bcc972355bbf00df0d85d00312a625f",
        "role": "linked-runtime-source",
        "requiredPaths": [
            "compiler-rt/lib/builtins",
            "libcxx",
            "libcxxabi",
            "libunwind",
            "runtimes",
        ],
    },
}
EXPECTED_NDK_SOURCE_PACKAGE = {
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
EXPECTED_NDK_RELEASE_PROVENANCE = {
    "releaseName": "r29",
    "clangVersion": "21.0.0",
    "clangRevision": "r563880c",
    "ndkRepository": "https://android.googlesource.com/platform/ndk",
    "ndkTag": "ndk-r29",
    "ndkTagObject": "5199c56421d79df5099aad8e32e32c101ff85cca",
    "ndkCommit": "196e0661200bad5361340700fea67be12e1f1684",
    "manifestRepository": "https://android.googlesource.com/platform/manifest",
    "manifestTagObject": "5d4df6d77b33dc6d31576a66a8ff283c8825493f",
    "manifestCommit": "82eb8adcaafe02dce4e462db2379fad3ea0b54d8",
    "prebuiltTags": {
        "darwin-x86_64": {
            "repository": (
                "https://android.googlesource.com/platform/prebuilts/clang/host/darwin-x86"
            ),
            "tagObject": "c547cdbfbec71e85920c1f0976e18defc01a0b5b",
            "commit": "2ede290b28d234595fcc23207c633961690c57ba",
        },
        "linux-x86_64": {
            "repository": (
                "https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86"
            ),
            "tagObject": "be61f23178d3459a558b45dd0df4304b0fda6b26",
            "commit": "568b941cf0c249b9c2a1f853e94a29f0e6291c59",
        },
    },
}
EXPECTED_CONTRIB_CANDIDATE_LICENSES = {
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSL-1.0",
    "CC0-1.0",
    "FTL",
    "IJG",
    "ISC",
    "LGPL-2.0-or-later",
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "Libpng-2.0",
    "LicenseRef-Public-Domain",
    "MIT",
    "TU-Berlin-1.0",
    "Unicode-DFS-2016",
    "Zlib",
}
REQUIRED_EXPORTS = {
    "JNI_OnLoad",
    "libvlc_get_changeset",
    "libvlc_get_version",
    "libvlc_new",
    "libvlc_video_set_output_callbacks",
}
FORBIDDEN_NEEDED = {"libc++_shared.so", "libvlcjni.so"}


def fail(message: str) -> None:
    raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_stream(source) -> str:
    digest = hashlib.sha256()
    for block in iter(lambda: source.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def real_file(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        fail(f"{description} must be a real non-empty file.")
    return path.resolve(strict=True)


def real_directory(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        fail(f"{description} must be a real directory.")
    resolved = path.resolve(strict=True)
    if any(character.isspace() for character in str(resolved)):
        fail(f"{description} path must not contain whitespace.")
    return resolved


def relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        fail(f"Path escapes its expected audit root: {path.name}")
    raise AssertionError("unreachable")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def required_modules(root: Path) -> set[str]:
    recipe = read_json(root / "build-recipes/android.json")
    values = recipe.get("requiredPlaybackModules")
    if (
        not isinstance(values, list)
        or values != sorted(set(values))
        or any(not isinstance(value, str) or not SAFE_MODULE.fullmatch(value) for value in values)
    ):
        fail("Android required playback modules are not a closed sorted list.")
    return set(values)


def libvlcjni_patch(root: Path) -> tuple[str, Path]:
    recipe = read_json(root / "build-recipes/android.json")
    value = recipe.get("libvlcjniPatch")
    if not isinstance(value, str):
        fail("Android libvlcjni patch path is missing.")
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() != value or ".." in relative.parts:
        fail("Android libvlcjni patch path is not canonical.")
    patch = real_file(root.joinpath(*relative.parts), "Android libvlcjni policy patch")
    relative_to(patch, root)
    return value, patch


def static_component_policy(root: Path) -> dict:
    policy = read_json(root / "compliance/policy/android-static-components.json")
    expected_keys = {
        "schemaVersion",
        "target",
        "vlcRevision",
        "ndkRevision",
        "reviewStatus",
        "contribComponents",
        "candidateLicenseSpdx",
        "licenseEvidence",
        "contribArchives",
        "ndkComponents",
        "ndkSourceInputs",
        "ndkSourcePackage",
        "ndkReleaseProvenance",
        "ndkArchiveTemplates",
        "ndkArchiveSourcePaths",
    }
    if set(policy) != expected_keys or policy.get("schemaVersion") != 1:
        fail("Android static component policy fields are not closed.")
    if (
        policy.get("target") != "android-arm"
        or policy.get("vlcRevision") != VLC_REVISION
        or policy.get("ndkRevision") != NDK_REVISION
        or policy.get("reviewStatus")
        != "source-mapped-license-and-notice-review-pending"
    ):
        fail("Android static component policy identity or review state is invalid.")

    components = policy.get("contribComponents")
    if not isinstance(components, dict) or list(components) != sorted(components) or not components:
        fail("Android contrib components must be a non-empty sorted closed map.")
    for component_id, component in components.items():
        if not SAFE_COMPONENT.fullmatch(component_id) or not isinstance(component, dict):
            fail(f"Android contrib component is unsafe: {component_id!r}")
        if set(component) != {"version", "sourceArchives"}:
            fail(f"Android contrib component fields are not closed: {component_id}")
        if not isinstance(component["version"], str) or not SAFE_VERSION.fullmatch(
            component["version"]
        ):
            fail(f"Android contrib component version is unsafe: {component_id}")
        sources = component["sourceArchives"]
        if (
            not isinstance(sources, list)
            or sources != sorted(set(sources))
            or not sources
            or any(
                not isinstance(source, str) or not SAFE_SOURCE_ARCHIVE.fullmatch(source)
                for source in sources
            )
        ):
            fail(f"Android contrib source archives are not canonical: {component_id}")

    source_archives = {
        source
        for component in components.values()
        for source in component["sourceArchives"]
    }
    candidate_licenses = policy.get("candidateLicenseSpdx")
    if (
        not isinstance(candidate_licenses, dict)
        or list(candidate_licenses) != sorted(candidate_licenses)
        or set(candidate_licenses) != set(components)
    ):
        fail("Android candidate SPDX mapping must cover every contrib component exactly.")
    for component_id, licenses in candidate_licenses.items():
        if (
            not isinstance(licenses, list)
            or licenses != sorted(set(licenses))
            or not licenses
            or any(
                not isinstance(license_id, str)
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9.+-]*(?: WITH [A-Za-z0-9][A-Za-z0-9.+-]*)?",
                    license_id,
                )
                or license_id.startswith(("GPL-", "AGPL-", "LicenseRef-NonFree", "unknown"))
                for license_id in licenses
            )
        ):
            fail(f"Android candidate SPDX mapping is unsafe: {component_id}")
    if {
        license_id for licenses in candidate_licenses.values() for license_id in licenses
    } != EXPECTED_CONTRIB_CANDIDATE_LICENSES:
        fail("Android contrib candidate SPDX set changed without linked-member review.")
    license_evidence = policy.get("licenseEvidence")
    if (
        not isinstance(license_evidence, dict)
        or list(license_evidence) != sorted(license_evidence)
        or set(license_evidence) != source_archives
    ):
        fail("Android contrib license evidence must cover every source archive exactly.")
    license_evidence_count = 0
    for source, paths in license_evidence.items():
        if not isinstance(paths, list) or paths != sorted(set(paths)) or not paths:
            fail(f"Android contrib license evidence is not canonical: {source}")
        for value in paths:
            if not isinstance(value, str):
                fail(f"Android contrib license evidence path is unsafe: {source}")
            relative = PurePosixPath(value)
            if (
                relative.is_absolute()
                or relative.as_posix() != value
                or ".." in relative.parts
                or not relative.parts
                or any(
                    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", part)
                    for part in relative.parts
                )
            ):
                fail(f"Android contrib license evidence path is unsafe: {source}!/{value}")
        license_evidence_count += len(paths)
    if license_evidence_count != 83:
        fail("Android contrib license evidence must contain exactly 83 selected records.")

    contrib_archives = policy.get("contribArchives")
    if (
        not isinstance(contrib_archives, dict)
        or list(contrib_archives) != sorted(contrib_archives)
        or len(contrib_archives) != 62
    ):
        fail("Android contrib archive map must contain the exact sorted 62-archive graph.")
    for archive, component_id in contrib_archives.items():
        if not SAFE_CONTRIB_ARCHIVE.fullmatch(archive) or component_id not in components:
            fail(f"Android contrib archive mapping is unsafe: {archive!r}")
    if set(contrib_archives.values()) != set(components):
        fail("Android contrib component policy contains unused or missing components.")

    ndk_components = policy.get("ndkComponents")
    if not isinstance(ndk_components, dict) or list(ndk_components) != sorted(ndk_components):
        fail("Android NDK components must be a sorted closed map.")
    for component_id, component in ndk_components.items():
        if not SAFE_COMPONENT.fullmatch(component_id) or not isinstance(component, dict):
            fail(f"Android NDK component is unsafe: {component_id!r}")
        if set(component) != {
            "version",
            "candidateLicenseSpdx",
            "evidenceFiles",
            "toolchainEvidenceFiles",
            "sourceInputs",
            "sourceStatus",
        }:
            fail(f"Android NDK component fields are not closed: {component_id}")
        if component["version"] != NDK_REVISION:
            fail(f"Android NDK component version is invalid: {component_id}")
        if component["candidateLicenseSpdx"] != ["Apache-2.0 WITH LLVM-exception"]:
            fail(f"Android NDK candidate SPDX mapping is invalid: {component_id}")
        if component["evidenceFiles"] != NDK_ROOT_EVIDENCE:
            fail(f"Android NDK evidence files are incomplete: {component_id}")
        if component["toolchainEvidenceFiles"] != NDK_TOOLCHAIN_EVIDENCE:
            fail(f"Android NDK toolchain provenance files are incomplete: {component_id}")
        if component["sourceInputs"] != sorted(EXPECTED_NDK_SOURCE_INPUTS):
            fail(f"Android NDK source input references are incomplete: {component_id}")
        if component["sourceStatus"] != NDK_SOURCE_STATUS:
            fail(f"Android NDK source review state is invalid: {component_id}")

    if policy.get("ndkSourceInputs") != EXPECTED_NDK_SOURCE_INPUTS:
        fail("Android NDK source revisions or trees differ from the closed map.")
    if policy.get("ndkSourcePackage") != EXPECTED_NDK_SOURCE_PACKAGE:
        fail("Android NDK source package selection differs from the closed map.")
    if policy.get("ndkReleaseProvenance") != EXPECTED_NDK_RELEASE_PROVENANCE:
        fail("Android NDK release/prebuilt provenance differs from r29.")

    ndk_templates = policy.get("ndkArchiveTemplates")
    if (
        not isinstance(ndk_templates, dict)
        or list(ndk_templates) != sorted(ndk_templates)
        or set(ndk_templates) != EXPECTED_NDK_TEMPLATES
        or set(ndk_templates.values()) != set(ndk_components)
    ):
        fail("Android NDK archive templates are not the exact closed runtime graph.")
    ndk_source_paths = policy.get("ndkArchiveSourcePaths")
    if (
        not isinstance(ndk_source_paths, dict)
        or list(ndk_source_paths) != sorted(ndk_source_paths)
        or ndk_source_paths != EXPECTED_NDK_SOURCE_PATHS
        or set(ndk_source_paths) != set(ndk_templates)
    ):
        fail("Android NDK archive-to-source map is not the exact closed runtime graph.")
    return policy


def ndk_host_prebuilt(ndk_directory: Path) -> tuple[str, Path]:
    prebuilt = real_directory(
        ndk_directory / "toolchains/llvm/prebuilt", "Android NDK host toolchain directory"
    )
    host_tags = sorted(
        path.name for path in prebuilt.iterdir() if path.is_dir() and not path.is_symlink()
    )
    if len(host_tags) != 1 or not SAFE_HOST_TAG.fullmatch(host_tags[0]):
        fail("Android NDK must contain exactly one supported host toolchain.")
    return host_tags[0], real_directory(
        prebuilt / host_tags[0], "Android NDK selected host toolchain directory"
    )


def validate_ndk_toolchain_provenance(ndk_directory: Path, policy: dict) -> tuple[str, Path]:
    host_tag, host_prebuilt = ndk_host_prebuilt(ndk_directory)
    version_file = real_file(
        host_prebuilt / "AndroidVersion.txt", "Android NDK Clang version evidence"
    )
    source_info_file = real_file(
        host_prebuilt / "clang_source_info.md", "Android NDK Clang source evidence"
    )
    release = policy["ndkReleaseProvenance"]
    expected_version = (
        f"{release['clangVersion']}\n"
        f"based on {release['clangRevision']}\n"
        "for additional information on LLVM revision and cherry-picks, "
        "see clang_source_info.md\n"
    )
    if version_file.read_text(encoding="utf-8") != expected_version:
        fail("Android NDK Clang version evidence differs from the closed r29 identity.")
    source_info = source_info_file.read_text(encoding="utf-8")
    if not source_info.startswith(
        f"Base revision: [{LLVM_PROJECT_REVISION}]"
        f"(https://github.com/llvm/llvm-project/commits/{LLVM_PROJECT_REVISION})\n"
    ):
        fail("Android NDK Clang source evidence has a different LLVM base revision.")
    patch_revisions = set(
        re.findall(r"toolchain/llvm_android/\+/([0-9a-f]{40})/patches/", source_info)
    )
    if patch_revisions != {LLVM_ANDROID_REVISION}:
        fail("Android NDK Clang source evidence has a different Android patch revision.")
    if host_tag not in release["prebuiltTags"]:
        fail("Android NDK host prebuilt is absent from the closed r29 provenance map.")
    return host_tag, host_prebuilt


def expanded_ndk_archive_components(ndk_directory: Path, abi: str, policy: dict) -> dict[str, str]:
    host_tag, _ = ndk_host_prebuilt(ndk_directory)
    values = {"hostTag": host_tag, **ABI_TOOLCHAIN_NAMES[abi]}
    return {
        template.format_map(values): component
        for template, component in policy["ndkArchiveTemplates"].items()
    }


def expanded_ndk_archive_source_paths(
    ndk_directory: Path, abi: str, policy: dict
) -> dict[str, list[str]]:
    host_tag, _ = ndk_host_prebuilt(ndk_directory)
    values = {"hostTag": host_tag, **ABI_TOOLCHAIN_NAMES[abi]}
    return {
        template.format_map(values): source_paths
        for template, source_paths in policy["ndkArchiveSourcePaths"].items()
    }


def source_archive_license_evidence(
    source: Path, source_name: str, evidence_paths: list[str]
) -> list[dict]:
    try:
        with tarfile.open(source, mode="r:*") as archive:
            members = archive.getmembers()
            entries = []
            for evidence_path in evidence_paths:
                relative = PurePosixPath(evidence_path)
                matches = []
                for member in members:
                    parts = PurePosixPath(member.name).parts
                    if len(parts) >= 2 and tuple(parts[1:]) == relative.parts:
                        matches.append(member)
                if len(matches) != 1:
                    fail(
                        "Android contrib license evidence is missing or ambiguous: "
                        f"{source_name}!/{evidence_path}"
                    )
                member = matches[0]
                if not member.isfile() or member.issym() or member.islnk() or not 0 < member.size <= 2_000_000:
                    fail(
                        "Android contrib license evidence must be a bounded regular file: "
                        f"{source_name}!/{evidence_path}"
                    )
                extracted = archive.extractfile(member)
                if extracted is None:
                    fail(f"Android contrib license evidence is unreadable: {source_name}")
                with extracted:
                    evidence_sha256 = sha256_stream(extracted)
                entries.append(
                    {
                        "path": f"vlc-contrib-tarballs/{source_name}!/{evidence_path}",
                        "sha256": evidence_sha256,
                        "size": member.size,
                    }
                )
            return entries
    except (OSError, tarfile.TarError) as error:
        raise ValueError(f"VLC contrib source archive is unreadable: {source_name}") from error


def static_component_evidence(
    vlc_source: Path,
    ndk_directory: Path,
    policy: dict,
    contrib_component_ids: set[str],
    ndk_component_ids: set[str],
) -> list[dict]:
    tarballs = real_directory(vlc_source / "contrib/tarballs", "VLC contrib tarball directory")
    entries: list[dict] = []
    for component_id in sorted(contrib_component_ids):
        component = policy["contribComponents"][component_id]
        source_entries = []
        for source_name in component["sourceArchives"]:
            source = real_file(tarballs / source_name, f"VLC contrib source archive {source_name}")
            if source.parent != tarballs:
                fail("Android contrib source archive escaped its closed tarball directory.")
            source_entries.append(
                {
                    "path": f"vlc-contrib-tarballs/{source_name}",
                    "sha256": sha256(source),
                    "size": source.stat().st_size,
                    "licenseEvidence": source_archive_license_evidence(
                        source,
                        source_name,
                        policy["licenseEvidence"][source_name],
                    ),
                }
            )
        entries.append(
            {
                "id": component_id,
                "kind": "VLC_CONTRIB",
                "version": component["version"],
                "candidateLicenseSpdx": policy["candidateLicenseSpdx"][component_id],
                "licenseReviewStatus": "pending-linked-member-review",
                "sourceArchives": source_entries,
            }
        )

    ndk_host_tag = None
    ndk_host_prebuilt = None
    if ndk_component_ids:
        ndk_host_tag, ndk_host_prebuilt = validate_ndk_toolchain_provenance(
            ndk_directory, policy
        )
    for component_id in sorted(ndk_component_ids):
        assert ndk_host_tag is not None and ndk_host_prebuilt is not None
        component = policy["ndkComponents"][component_id]
        evidence_files = []
        for relative in component["evidenceFiles"]:
            evidence = real_file(ndk_directory / relative, f"Android NDK evidence file {relative}")
            if evidence.parent != ndk_directory:
                fail("Android NDK evidence file escaped its closed root.")
            evidence_files.append(
                {
                    "path": f"ndk/{relative}",
                    "sha256": sha256(evidence),
                    "size": evidence.stat().st_size,
                }
            )
        for relative in component["toolchainEvidenceFiles"]:
            evidence = real_file(
                ndk_host_prebuilt / relative,
                f"Android NDK toolchain evidence file {relative}",
            )
            if evidence.parent != ndk_host_prebuilt:
                fail("Android NDK toolchain evidence file escaped its closed root.")
            evidence_files.append(
                {
                    "path": f"ndk/{relative}",
                    "sha256": sha256(evidence),
                    "size": evidence.stat().st_size,
                }
            )
        source_inputs = [
            {"id": source_id, **policy["ndkSourceInputs"][source_id]}
            for source_id in component["sourceInputs"]
        ]
        release = policy["ndkReleaseProvenance"]
        binary_provenance = {
            key: value for key, value in release.items() if key != "prebuiltTags"
        }
        binary_provenance["prebuilt"] = {
            "hostTag": ndk_host_tag,
            **release["prebuiltTags"][ndk_host_tag],
        }
        entries.append(
            {
                "id": component_id,
                "kind": "NDK_TOOLCHAIN",
                "version": component["version"],
                "candidateLicenseSpdx": component["candidateLicenseSpdx"],
                "licenseReviewStatus": "pending-linked-member-review",
                "sourceStatus": component["sourceStatus"],
                "sourceInputs": source_inputs,
                "binaryProvenance": binary_provenance,
                "evidenceFiles": evidence_files,
            }
        )
    return sorted(entries, key=lambda entry: entry["id"])


def parse_module_manifest(path: Path) -> list[str]:
    text = real_file(path, "Generated VLC module manifest").read_text(encoding="utf-8")
    inside = False
    found: list[str] = []
    for line in text.splitlines():
        if line.strip() == "const void *vlc_static_modules[] = {":
            if inside:
                fail("Generated VLC module manifest contains duplicate arrays.")
            inside = True
            continue
        if not inside:
            continue
        if line.strip() == "NULL":
            inside = False
            break
        match = MODULE_ENTRY.fullmatch(line)
        if match is None:
            fail("Generated VLC module manifest contains an unsafe entry.")
        found.append(match.group(1))
    if inside or not found or len(found) != len(set(found)):
        fail("Generated VLC module manifest is empty or duplicated.")
    return sorted(found)


def parse_link_map(path: Path) -> dict[Path, list[str]]:
    archives: dict[Path, set[str]] = {}
    with real_file(path, "Android libvlc linker map").open(
        "r", encoding="utf-8", errors="strict"
    ) as source:
        for line in source:
            for token in line.split():
                match = ARCHIVE_INPUT.match(token)
                if match is None:
                    continue
                archive = Path(match.group("archive"))
                if not archive.is_absolute():
                    fail("Android linker map contains a relative static archive.")
                archive = real_file(archive, "Linked static archive")
                member = match.group("member")
                if (
                    not member
                    or len(member) > 512
                    or any(ord(character) < 0x20 or ord(character) > 0x7E for character in member)
                ):
                    fail("Android linker map contains an unsafe archive member.")
                archives.setdefault(archive, set()).add(member)
    if not archives or len(archives) > 2048:
        fail("Android linker map has no bounded static archive graph.")
    return {path: sorted(members) for path, members in sorted(archives.items(), key=lambda item: str(item[0]))}


def run_tool(executable: Path, *arguments: str) -> str:
    if not executable.is_absolute():
        fail("Android NDK audit tool path must be absolute.")
    try:
        resolved = executable.resolve(strict=True)
    except OSError as error:
        raise ValueError("Android NDK audit tool is missing.") from error
    if not resolved.is_file():
        fail("Android NDK audit tool must resolve to a file.")
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="strict",
        )
    except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
        raise ValueError("Android NDK audit tool failed without trusted output.") from error
    return completed.stdout


def verify_lgpl_strings(text: str, description: str) -> None:
    lines = set(text.splitlines())
    if LGPL_TEXT not in lines or GPL_TEXT in lines:
        fail(f"{description} does not have the closed LGPL module marker.")


def verify_final_license_strings(text: str) -> bool:
    lines = set(text.splitlines())
    if GPL_TEXT in lines:
        fail("Final Android libvlc.so retains a forbidden GPL module marker.")
    return LGPL_TEXT in lines


def parse_needed(text: str) -> list[str]:
    values = sorted(set(NEEDED_ENTRY.findall(text)))
    if (
        not values
        or any(not SAFE_NEEDED.fullmatch(value) for value in values)
        or FORBIDDEN_NEEDED.intersection(values)
    ):
        fail("Android libvlc has an invalid or forbidden DT_NEEDED graph.")
    return values


def verify_load_alignment(text: str) -> int:
    values = [int(match.group(1), 16) for line in text.splitlines() if (match := LOAD_ENTRY.match(line))]
    if not values or any(value != 0x4000 for value in values):
        fail("Android libvlc LOAD segments are not exactly 16 KiB aligned.")
    return 0x4000


def parse_exports(text: str) -> list[str]:
    values = {
        line.split()[-1]
        for line in text.splitlines()
        if line.split() and not line.split()[-1].endswith(":")
    }
    missing = REQUIRED_EXPORTS - values
    if missing:
        fail(f"Android libvlc omits required exports: {sorted(missing)}")
    return sorted(REQUIRED_EXPORTS)


def classify_archive(
    archive: Path,
    build_directory: Path,
    plugin_directory: Path,
    contrib_directory: Path,
    ndk_directory: Path,
    core_archives: dict[Path, str],
) -> tuple[str, str]:
    if archive in core_archives:
        return "VLC_CORE", f"vlc-build/{core_archives[archive]}"
    try:
        relative = archive.relative_to(plugin_directory).as_posix()
        return "VLC_MODULE", f"vlc-build/install/lib/vlc/plugins/{relative}"
    except ValueError:
        pass
    try:
        relative = archive.relative_to(contrib_directory).as_posix()
        return "CONTRIB", f"vlc-contrib/{relative}"
    except ValueError:
        pass
    try:
        relative = archive.relative_to(ndk_directory).as_posix()
        return "NDK_TOOLCHAIN", f"ndk/{relative}"
    except ValueError:
        pass
    try:
        relative = archive.relative_to(build_directory).as_posix()
    except ValueError:
        relative = None
    if relative is not None:
        fail(f"Unclassified VLC build archive entered libvlc.so: {relative}")
    fail(f"Static archive entered libvlc.so from outside the closed roots: {archive.name}")
    raise AssertionError("unreachable")


def create(
    root: Path,
    vlc_source: Path,
    ndk_directory: Path,
    abi: str,
    libvlc: Path,
    link_map: Path,
    readelf: Path,
    nm: Path,
    strings: Path,
    output: Path,
) -> dict:
    if abi not in SUPPORTED_TARGETS:
        fail("Android link audit target ABI is unsupported.")
    root = real_directory(root, "KMediaVlc source root")
    vlc_source = real_directory(vlc_source, "VLC source root")
    ndk_directory = real_directory(ndk_directory, "Android NDK root")
    libvlc = real_file(libvlc, "Source-built Android libvlc.so")
    link_map = real_file(link_map, "Android libvlc linker map")
    output = output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        fail("Android link audit output must be a new file in a real directory.")

    tuple_name = SUPPORTED_TARGETS[abi]
    build_directory = real_directory(
        vlc_source / f"build-android-{tuple_name}", "VLC Android build directory"
    )
    plugin_directory = real_directory(
        build_directory / "install/lib/vlc/plugins", "VLC Android module directory"
    )
    contrib_directory = real_directory(
        vlc_source / f"contrib/{tuple_name}", "VLC Android contrib directory"
    )
    module_manifest = build_directory / "ndk/libvlcjni-modules.c"
    modules = parse_module_manifest(module_manifest)
    required = required_modules(root)
    patch_path, patch_file = libvlcjni_patch(root)
    component_policy = static_component_policy(root)
    component_policy_path = real_file(
        root / "compliance/policy/android-static-components.json",
        "Android static component policy",
    )
    expected_ndk_archives = expanded_ndk_archive_components(
        ndk_directory, abi, component_policy
    )
    expected_ndk_sources = expanded_ndk_archive_source_paths(
        ndk_directory, abi, component_policy
    )
    if set(expected_ndk_sources) != set(expected_ndk_archives):
        fail("Android NDK expanded archive/source maps differ.")
    if not required.issubset(modules):
        fail(f"Android libvlc omits required playback modules: {sorted(required - set(modules))}")

    module_archives = {
        path.resolve(strict=True): path.name[len("lib") : -len("_plugin.a")]
        for path in plugin_directory.glob("lib*_plugin.a")
        if path.is_file() and not path.is_symlink()
    }
    if set(module_archives.values()) != set(modules) or len(module_archives) != len(modules):
        fail("Generated Android module array differs from its static archive directory.")

    core_relative = {
        "lib/.libs/libvlc.a": "lib/.libs/libvlc.a",
        "src/.libs/libvlccore.a": "src/.libs/libvlccore.a",
        "compat/.libs/libcompat.a": "compat/.libs/libcompat.a",
    }
    core_archives = {
        real_file(build_directory / relative, "VLC core static archive"): canonical
        for relative, canonical in core_relative.items()
    }
    linked = parse_link_map(link_map)
    if not set(module_archives).issubset(linked):
        missing = sorted(module_archives[path] for path in set(module_archives) - set(linked))
        fail(f"Android linker map omits selected VLC modules: {missing}")
    if not set(core_archives).issubset(linked):
        fail("Android linker map omits one or more VLC core archives.")

    archive_entries = []
    module_entries = []
    linked_contrib_archives: set[str] = set()
    linked_ndk_archives: set[str] = set()
    contrib_component_ids: set[str] = set()
    ndk_component_ids: set[str] = set()
    for archive, members in linked.items():
        kind, canonical_path = classify_archive(
            archive,
            build_directory,
            plugin_directory,
            contrib_directory,
            ndk_directory,
            core_archives,
        )
        entry = {
            "kind": kind,
            "path": canonical_path,
            "sha256": sha256(archive),
            "size": archive.stat().st_size,
            "linkedObjects": members,
        }
        if kind == "CONTRIB":
            component_id = component_policy["contribArchives"].get(canonical_path)
            if component_id is None:
                fail(f"Unmapped Android contrib archive entered libvlc.so: {canonical_path}")
            entry["component"] = component_id
            linked_contrib_archives.add(canonical_path)
            contrib_component_ids.add(component_id)
        elif kind == "NDK_TOOLCHAIN":
            component_id = expected_ndk_archives.get(canonical_path)
            if component_id is None:
                fail(f"Unmapped Android NDK archive entered libvlc.so: {canonical_path}")
            entry["component"] = component_id
            entry["sourcePaths"] = expected_ndk_sources[canonical_path]
            linked_ndk_archives.add(canonical_path)
            ndk_component_ids.add(component_id)
        archive_entries.append(entry)
        if kind == "VLC_MODULE":
            module = module_archives.get(archive)
            if module is None:
                fail("An unselected VLC module archive entered libvlc.so.")
            verify_lgpl_strings(run_tool(strings, str(archive)), f"VLC module {module}")
            module_entries.append(
                {
                    "name": module,
                    "archiveSha256": entry["sha256"],
                    "licenseSpdx": "LGPL-2.1-or-later",
                    "linkedObjects": members,
                }
            )
    if not any(entry["kind"] == "CONTRIB" for entry in archive_entries):
        fail("Android libvlc has no audited static contrib archives.")
    if not any(entry["kind"] == "NDK_TOOLCHAIN" for entry in archive_entries):
        fail("Android libvlc has no audited static NDK runtime archives.")
    expected_contrib_archives = set(component_policy["contribArchives"])
    if linked_contrib_archives != expected_contrib_archives:
        fail(
            "Android contrib link graph differs from the exact source-mapped policy: "
            f"missing={sorted(expected_contrib_archives - linked_contrib_archives)}, "
            f"unexpected={sorted(linked_contrib_archives - expected_contrib_archives)}"
        )
    if linked_ndk_archives != set(expected_ndk_archives):
        fail(
            "Android NDK link graph differs from the exact source-mapped policy: "
            f"missing={sorted(set(expected_ndk_archives) - linked_ndk_archives)}, "
            f"unexpected={sorted(linked_ndk_archives - set(expected_ndk_archives))}"
        )
    archive_entries.sort(key=lambda entry: (entry["kind"], entry["path"]))
    module_entries.sort(key=lambda entry: entry["name"])
    if [entry["name"] for entry in module_entries] != modules:
        fail("Android module link evidence is not canonical.")

    final_lgpl_marker_retained = verify_final_license_strings(
        run_tool(strings, str(libvlc))
    )
    needed = parse_needed(run_tool(readelf, "-d", str(libvlc)))
    alignment = verify_load_alignment(run_tool(readelf, "-l", str(libvlc)))
    exports = parse_exports(run_tool(nm, "-D", "--defined-only", str(libvlc)))
    component_entries = static_component_evidence(
        vlc_source,
        ndk_directory,
        component_policy,
        contrib_component_ids,
        ndk_component_ids,
    )
    audit = {
        "schemaVersion": 1,
        "target": f"android-{abi}",
        "abi": abi,
        "androidApi": 21,
        "vlcRevision": VLC_REVISION,
        "libvlcjniRevision": LIBVLCJNI_REVISION,
        "ndkRevision": NDK_REVISION,
        "reviewStatus": "candidate-source-mapped-license-review-pending",
        "libvlc": {
            "sha256": sha256(libvlc),
            "size": libvlc.stat().st_size,
            "loadAlignment": alignment,
            "needed": needed,
            "requiredExports": exports,
            "declaredVlcLicenseSpdx": "LGPL-2.1-or-later",
            "effectiveLicenseSpdx": None,
            "lgplModuleMarkerRetained": final_lgpl_marker_retained,
        },
        "modules": module_entries,
        "staticArchives": archive_entries,
        "staticComponents": component_entries,
        "evidence": {
            "libvlcjniPatch": {
                "path": patch_path,
                "sha256": sha256(patch_file),
            },
            "linkMapSha256": sha256(link_map),
            "moduleManifestSha256": sha256(module_manifest),
            "staticComponentPolicy": {
                "path": "compliance/policy/android-static-components.json",
                "sha256": sha256(component_policy_path),
            },
        },
    }
    with output.open("x", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vlc-source", type=Path, required=True)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--abi", choices=sorted(SUPPORTED_TARGETS), required=True)
    parser.add_argument("--libvlc", type=Path, required=True)
    parser.add_argument("--link-map", type=Path, required=True)
    parser.add_argument("--readelf", type=Path, required=True)
    parser.add_argument("--nm", type=Path, required=True)
    parser.add_argument("--strings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    audit = create(
        arguments.root,
        arguments.vlc_source,
        arguments.ndk,
        arguments.abi,
        arguments.libvlc,
        arguments.link_map,
        arguments.readelf,
        arguments.nm,
        arguments.strings,
        arguments.output,
    )
    print(
        f"Created {audit['target']} link audit with {len(audit['modules'])} modules and "
        f"{len(audit['staticArchives'])} static archives."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
