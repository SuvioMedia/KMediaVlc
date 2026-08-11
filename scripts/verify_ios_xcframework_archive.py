#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

"""Fail-closed verification for the installable KMediaVlc iOS archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import stat
import struct
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from assemble_ios_xcframeworks import (  # noqa: E402
    EXPECTED_FRAMEWORK_COUNT,
    EXPECTED_SELECTED_PLUGIN_COUNT,
    POLICY_FILES,
    RECIPE_FILES,
    REVISION,
    SEMVER,
    expected_frameworks,
    expected_install_name,
    podspec,
)
from stage_vlc_ios_frameworks import (  # noqa: E402
    EXPECTED_MINIMUM_IOS,
    EXPECTED_RAW_PLUGIN_COUNT,
    PINNED_REVISION,
)


MACHO_MAGIC_64 = 0xFEEDFACF
MACHO_ARM64 = 0x0100000C
MH_DYLIB = 6
LC_LOAD_DYLIB = 0xC
LC_ID_DYLIB = 0xD
LC_LOAD_WEAK_DYLIB = 0x80000018
LC_RPATH = 0x8000001C
LC_REEXPORT_DYLIB = 0x8000001F
LC_LOAD_UPWARD_DYLIB = 0x80000023
LC_BUILD_VERSION = 0x32
IOS_PLATFORM = 2
IOS_SIMULATOR_PLATFORM = 7
IOS_16_2_0 = (16 << 16) | (2 << 8)
SYSTEM_DEPENDENCY_PREFIXES = ("/System/Library/", "/usr/lib/")
MAX_FILES = 30_000
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024


class IosArchiveError(ValueError):
    """The iOS archive differs from its closed installable contract."""


def fail(message: str) -> None:
    raise IosArchiveError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IosArchiveError(f"cannot read {label}") from error


def safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if not members or len(members) > MAX_FILES:
        fail("iOS archive file count is invalid")
    names: set[str] = set()
    total = 0
    for member in members:
        name = member.filename
        path = PurePosixPath(name)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if (
            not name
            or "\\" in name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(part.casefold().startswith(".env") for part in path.parts)
            or name in names
            or member.is_dir()
            or file_type not in {0, stat.S_IFREG}
            or member.file_size < 0
            or member.file_size > MAX_FILE_BYTES
        ):
            fail("iOS archive contains an unsafe or unsupported member")
        names.add(name)
        total += member.file_size
        if total > MAX_TOTAL_BYTES:
            fail("iOS archive expands beyond its size limit")
    return members


def extract_checked(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    output: Path,
) -> None:
    for member in members:
        destination = output.joinpath(*PurePosixPath(member.filename).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member, "r") as source, destination.open("xb") as target:
            shutil.copyfileobj(source, target, length=64 * 1024)
        if destination.stat().st_size != member.file_size:
            fail("iOS archive member size changed during extraction")


def dylib_name(command: bytes, command_offset: int, command_size: int) -> str:
    if command_size < 24:
        fail("iOS Mach-O dylib command is truncated")
    name_offset = struct.unpack_from("<I", command, command_offset + 8)[0]
    if name_offset < 24 or name_offset >= command_size:
        fail("iOS Mach-O dylib name offset is invalid")
    start = command_offset + name_offset
    end_limit = command_offset + command_size
    end = command.find(b"\0", start, end_limit)
    if end < 0:
        fail("iOS Mach-O dylib name is unterminated")
    try:
        name = command[start:end].decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise IosArchiveError("iOS Mach-O dylib name is not UTF-8") from error
    if not name:
        fail("iOS Mach-O dylib name is empty")
    return name


def verify_macho(
    path: Path,
    role: str,
    framework_name: str,
    simulator: bool,
) -> dict[str, Any]:
    with path.open("rb") as binary:
        header = binary.read(32)
        if len(header) != 32:
            fail("iOS framework binary is truncated")
        (
            magic,
            cpu_type,
            _cpu_subtype,
            file_type,
            command_count,
            command_bytes,
            _flags,
            _reserved,
        ) = struct.unpack("<IIIIIIII", header)
        if command_bytes > path.stat().st_size - 32:
            fail("iOS framework load commands are truncated")
        commands = binary.read(command_bytes)
    if (
        len(commands) != command_bytes
        or magic != MACHO_MAGIC_64
        or cpu_type != MACHO_ARM64
        or file_type != MH_DYLIB
        or command_count < 1
        or command_bytes < 8
    ):
        fail("iOS framework Mach-O identity is invalid")

    offset = 0
    build_versions: list[tuple[int, int]] = []
    install_names: list[str] = []
    dependencies: list[str] = []
    dylib_load_commands = {
        LC_LOAD_DYLIB,
        LC_LOAD_WEAK_DYLIB,
        LC_REEXPORT_DYLIB,
        LC_LOAD_UPWARD_DYLIB,
    }
    for _index in range(command_count):
        if offset + 8 > command_bytes:
            fail("iOS Mach-O load commands are truncated")
        command, size = struct.unpack_from("<II", commands, offset)
        if size < 8 or offset + size > command_bytes:
            fail("iOS Mach-O load command size is invalid")
        if command == LC_BUILD_VERSION:
            if size < 24:
                fail("iOS LC_BUILD_VERSION is truncated")
            platform, minimum = struct.unpack_from("<II", commands, offset + 8)
            build_versions.append((platform, minimum))
        elif command == LC_ID_DYLIB:
            install_names.append(dylib_name(commands, offset, size))
        elif command in dylib_load_commands:
            dependencies.append(dylib_name(commands, offset, size))
        elif command == LC_RPATH:
            fail("iOS framework contains an uncontrolled LC_RPATH")
        offset += size
    if offset != command_bytes:
        fail("iOS Mach-O load command inventory is invalid")

    expected_platform = IOS_SIMULATOR_PLATFORM if simulator else IOS_PLATFORM
    if build_versions != [(expected_platform, IOS_16_2_0)]:
        fail("iOS framework platform or minimum version is invalid")
    expected_id = expected_install_name(role, framework_name)
    if install_names != [expected_id]:
        fail("iOS framework install name is invalid")
    expected_core = "@rpath/KMediaVlcCore.framework/KMediaVlcCore"
    internal = [dependency for dependency in dependencies if dependency.startswith("@rpath/")]
    if internal != ([expected_core] if role in {"LIBVLC", "PLUGIN"} else []):
        fail("iOS framework internal dependency graph is invalid")
    external = [dependency for dependency in dependencies if dependency not in internal]
    if any(not dependency.startswith(SYSTEM_DEPENDENCY_PREFIXES) for dependency in external):
        fail("iOS framework contains an external dependency")
    return {
        "architectures": ["arm64"],
        "dependencies": [expected_id, *dependencies],
        "installName": expected_id,
        "minimumIos": EXPECTED_MINIMUM_IOS,
        "platform": "IOSSIMULATOR" if simulator else "IOS",
    }


def expected_evidence_paths() -> set[str]:
    paths = {
        "compliance/ios-arm64-frameworks.json",
        "compliance/ios-simulator-arm64-frameworks.json",
    }
    paths.update(
        f"compliance/build-recipes/{Path(relative).relative_to('build-recipes').as_posix()}"
        for relative in RECIPE_FILES
    )
    paths.update(f"compliance/policy/{Path(relative).name}" for relative in POLICY_FILES)
    return paths


def verify_evidence(root: Path, inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    evidence = inventory.get("evidenceSha256")
    expected_paths = expected_evidence_paths()
    if not isinstance(evidence, dict) or set(evidence) != expected_paths:
        fail("iOS aggregate evidence hash inventory is invalid")
    for relative, expected_hash in evidence.items():
        path = root.joinpath(*PurePosixPath(relative).parts)
        if (
            not isinstance(expected_hash, str)
            or len(expected_hash) != 64
            or not path.is_file()
            or sha256(path) != expected_hash
        ):
            fail("iOS aggregate evidence file differs from its hash")

    for relative in RECIPE_FILES:
        archived = root / "compliance/build-recipes" / Path(relative).relative_to(
            "build-recipes"
        )
        if archived.read_bytes() != (ROOT / relative).read_bytes():
            fail(f"iOS archived recipe differs from the release source: {relative}")
    for relative in POLICY_FILES:
        archived = root / "compliance/policy" / Path(relative).name
        if archived.read_bytes() != (ROOT / relative).read_bytes():
            fail(f"iOS archived policy differs from the release source: {relative}")

    reports: dict[str, dict[str, Any]] = {}
    for target in ("ios-arm64", "ios-simulator-arm64"):
        report = read_json(root / f"compliance/{target}-frameworks.json", f"{target} report")
        expected_simulator = target == "ios-simulator-arm64"
        if (
            not isinstance(report, dict)
            or report.get("schemaVersion") != 1
            or report.get("target") != target
            or report.get("architecture") != "arm64"
            or report.get("minimumIos") != EXPECTED_MINIMUM_IOS
            or report.get("simulator") != expected_simulator
            or report.get("vlcRevision") != PINNED_REVISION
            or report.get("rawPluginCount") != EXPECTED_RAW_PLUGIN_COUNT
            or report.get("selectedPluginCount") != EXPECTED_SELECTED_PLUGIN_COUNT
            or report.get("frameworkCount") != EXPECTED_FRAMEWORK_COUNT
            or not isinstance(report.get("frameworks"), list)
        ):
            fail(f"{target} archived framework report is invalid")
        by_name: dict[str, Any] = {}
        for record in report["frameworks"]:
            binary = record.get("binary") if isinstance(record, dict) else None
            if not isinstance(binary, str) or binary in by_name:
                fail(f"{target} archived framework report contains a duplicate")
            by_name[binary] = record
        if len(by_name) != EXPECTED_FRAMEWORK_COUNT:
            fail(f"{target} archived framework report is incomplete")
        reports[target] = {
            "byName": by_name,
            "reviewStatus": report.get("reviewStatus"),
            "binaryReviewStatus": report.get("binaryReviewStatus"),
        }
    expected_candidate = any(
        status != "approved"
        for report in reports.values()
        for status in (report["reviewStatus"], report["binaryReviewStatus"])
    )
    if inventory.get("auditCandidate") is not expected_candidate:
        fail("iOS aggregate audit-candidate state differs from its reports")
    return reports


def verify_framework(
    root: Path,
    descriptor: dict[str, str],
    record: dict[str, Any],
    reports: dict[str, dict[str, Any]],
) -> set[str]:
    framework_name = descriptor["frameworkName"]
    expected_record_keys = {"frameworkName", "role", "slices", "xcframework"}
    if descriptor["role"] == "PLUGIN":
        expected_record_keys.update({"family", "module"})
    if (
        set(record) != expected_record_keys
        or record.get("frameworkName") != framework_name
        or record.get("role") != descriptor["role"]
        or record.get("family") != descriptor.get("family")
        or record.get("module") != descriptor.get("module")
        or record.get("xcframework") != f"{framework_name}.xcframework"
        or not isinstance(record.get("slices"), list)
        or len(record["slices"]) != 2
    ):
        fail(f"{framework_name}: aggregate inventory record is invalid")

    xcframework = root / "Frameworks" / f"{framework_name}.xcframework"
    if not xcframework.is_dir() or xcframework.is_symlink():
        fail(f"{framework_name}: XCFramework directory is missing")
    try:
        with (xcframework / "Info.plist").open("rb") as source:
            metadata = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as error:
        raise IosArchiveError(f"{framework_name}: XCFramework Info.plist is invalid") from error
    libraries = metadata.get("AvailableLibraries")
    if not isinstance(libraries, list) or len(libraries) != 2:
        fail(f"{framework_name}: XCFramework slice metadata is invalid")
    metadata_by_identifier: dict[str, dict[str, Any]] = {}
    for library in libraries:
        identifier = library.get("LibraryIdentifier") if isinstance(library, dict) else None
        if not isinstance(identifier, str) or PurePosixPath(identifier).name != identifier:
            fail(f"{framework_name}: XCFramework identifier is invalid")
        if identifier in metadata_by_identifier:
            fail(f"{framework_name}: XCFramework identifier is duplicated")
        metadata_by_identifier[identifier] = library

    expected_files = {f"Frameworks/{framework_name}.xcframework/Info.plist"}
    observed_variants: set[str] = set()
    for slice_record in record["slices"]:
        if not isinstance(slice_record, dict) or set(slice_record) != {
            "identifier",
            "sha256",
            "size",
            "sourceTarget",
            "variant",
        }:
            fail(f"{framework_name}: slice inventory schema is invalid")
        identifier = slice_record["identifier"]
        variant = slice_record["variant"]
        target = slice_record["sourceTarget"]
        if (
            not isinstance(identifier, str)
            or variant not in {"device", "simulator"}
            or variant in observed_variants
            or target
            != ("ios-simulator-arm64" if variant == "simulator" else "ios-arm64")
        ):
            fail(f"{framework_name}: slice inventory identity is invalid")
        observed_variants.add(variant)
        library = metadata_by_identifier.get(identifier)
        expected_variant = "simulator" if variant == "simulator" else None
        if (
            library is None
            or library.get("LibraryPath") != f"{framework_name}.framework"
            or library.get("SupportedPlatform") != "ios"
            or library.get("SupportedArchitectures") != ["arm64"]
            or library.get("SupportedPlatformVariant") != expected_variant
        ):
            fail(f"{framework_name}: XCFramework slice differs from inventory")
        source_record = reports[target]["byName"].get(framework_name)
        if (
            source_record is None
            or slice_record.get("sha256") != source_record.get("sha256")
            or slice_record.get("size") != source_record.get("size")
        ):
            fail(f"{framework_name}: slice differs from its source report")

        framework = xcframework / identifier / f"{framework_name}.framework"
        binary = framework / framework_name
        if (
            not binary.is_file()
            or binary.is_symlink()
            or binary.stat().st_size != slice_record["size"]
            or sha256(binary) != slice_record["sha256"]
        ):
            fail(f"{framework_name}: framework binary hash or size is invalid")
        macho = verify_macho(binary, descriptor["role"], framework_name, variant == "simulator")
        if macho != source_record.get("machO"):
            fail(f"{framework_name}: Mach-O differs from its source report")
        try:
            with (framework / "Info.plist").open("rb") as source:
                framework_metadata = plistlib.load(source)
        except (OSError, plistlib.InvalidFileException) as error:
            raise IosArchiveError(f"{framework_name}: framework Info.plist is invalid") from error
        if (
            framework_metadata.get("CFBundleExecutable") != framework_name
            or framework_metadata.get("CFBundlePackageType") != "FMWK"
            or framework_metadata.get("MinimumOSVersion") != EXPECTED_MINIMUM_IOS
            or not (framework / "Modules/module.modulemap").is_file()
            or not (framework / "Headers").is_dir()
        ):
            fail(f"{framework_name}: framework metadata is invalid")
        headers = sorted(path.name for path in (framework / "Headers").iterdir() if path.is_file())
        if not headers or headers != source_record.get("headers"):
            fail(f"{framework_name}: framework headers differ from the source report")
        prefix = (
            f"Frameworks/{framework_name}.xcframework/{identifier}/"
            f"{framework_name}.framework"
        )
        expected_files.update(
            {
                f"{prefix}/{framework_name}",
                f"{prefix}/Info.plist",
                f"{prefix}/Modules/module.modulemap",
                *(f"{prefix}/Headers/{header}" for header in headers),
            }
        )
    if observed_variants != {"device", "simulator"}:
        fail(f"{framework_name}: device/simulator matrix is incomplete")
    return expected_files


def verify(
    archive_path: Path,
    expected_version: str,
    expected_revision: str,
    allow_audit_candidate: bool,
) -> None:
    if (
        not SEMVER.fullmatch(expected_version)
        or not REVISION.fullmatch(expected_revision)
        or archive_path.name
        != f"kmedia-vlc-{expected_version}-ios-xcframeworks.zip"
        or archive_path.is_symlink()
        or not archive_path.is_file()
    ):
        fail("iOS archive inputs are invalid")
    temporary = Path(tempfile.mkdtemp(prefix="kmediavlc-ios-verify-"))
    try:
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members = safe_members(archive)
                if archive.testzip() is not None:
                    fail("iOS archive CRC verification failed")
                extract_checked(archive, members, temporary)
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise IosArchiveError("iOS archive is unreadable") from error

        top_level = {
            PurePosixPath(member.filename).parts[0]
            for member in members
        }
        if top_level != {
            "Frameworks",
            "compliance",
            "LICENSE",
            "LICENSES",
            "NOTICE",
            "THIRD_PARTY_NOTICES.md",
        }:
            fail("iOS archive top-level inventory is invalid")

        inventory = read_json(
            temporary / "compliance/xcframework-inventory.json",
            "XCFramework inventory",
        )
        inventory_keys = {
            "auditCandidate",
            "evidenceSha256",
            "frameworkCount",
            "frameworks",
            "minimumIos",
            "recipeRevision",
            "schemaVersion",
            "selectedPluginCount",
            "version",
            "vlcRevision",
        }
        if (
            not isinstance(inventory, dict)
            or set(inventory) != inventory_keys
            or inventory.get("schemaVersion") != 1
            or inventory.get("version") != expected_version
            or inventory.get("recipeRevision") != expected_revision
            or inventory.get("vlcRevision") != PINNED_REVISION
            or inventory.get("minimumIos") != EXPECTED_MINIMUM_IOS
            or inventory.get("selectedPluginCount") != EXPECTED_SELECTED_PLUGIN_COUNT
            or inventory.get("frameworkCount") != EXPECTED_FRAMEWORK_COUNT
            or not isinstance(inventory.get("frameworks"), list)
            or len(inventory["frameworks"]) != EXPECTED_FRAMEWORK_COUNT
            or not isinstance(inventory.get("auditCandidate"), bool)
        ):
            fail("iOS aggregate inventory is invalid")
        if inventory["auditCandidate"] and not allow_audit_candidate:
            fail("iOS archive remains an audit candidate")
        reports = verify_evidence(temporary, inventory)

        descriptors = expected_frameworks()
        by_name: dict[str, dict[str, Any]] = {}
        for record in inventory["frameworks"]:
            name = record.get("frameworkName") if isinstance(record, dict) else None
            if not isinstance(name, str) or name in by_name:
                fail("iOS aggregate framework inventory contains a duplicate")
            by_name[name] = record
        expected_names = {descriptor["frameworkName"] for descriptor in descriptors}
        if set(by_name) != expected_names:
            fail("iOS aggregate framework inventory is incomplete")

        framework_files: set[str] = set()
        for descriptor in descriptors:
            framework_files.update(
                verify_framework(
                    temporary,
                    descriptor,
                    by_name[descriptor["frameworkName"]],
                    reports,
                )
            )
        actual_framework_files = {
            path.relative_to(temporary).as_posix()
            for path in (temporary / "Frameworks").rglob("*")
            if path.is_file()
        }
        if actual_framework_files != framework_files:
            fail("iOS framework tree contains an unexpected file")

        expected_compliance_files = expected_evidence_paths() | {
            "compliance/build-recipe-revision.txt",
            "compliance/xcframework-inventory.json",
        }
        actual_compliance_files = {
            path.relative_to(temporary).as_posix()
            for path in (temporary / "compliance").rglob("*")
            if path.is_file()
        }
        if actual_compliance_files != expected_compliance_files:
            fail("iOS compliance evidence contains an unexpected file")
        recorded_revision = (
            temporary / "compliance/build-recipe-revision.txt"
        ).read_text(encoding="utf-8").strip()
        if recorded_revision != expected_revision:
            fail("iOS aggregate revision evidence is invalid")

        for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            if temporary.joinpath(name).read_bytes() != ROOT.joinpath(name).read_bytes():
                fail(f"iOS archive {name} differs from the release source")
        expected_licenses = {
            path.relative_to(ROOT / "LICENSES").as_posix(): path.read_bytes()
            for path in (ROOT / "LICENSES").rglob("*")
            if path.is_file()
        }
        actual_licenses = {
            path.relative_to(temporary / "LICENSES").as_posix(): path.read_bytes()
            for path in (temporary / "LICENSES").rglob("*")
            if path.is_file()
        }
        if actual_licenses != expected_licenses:
            fail("iOS archive license inventory differs from the release source")
    finally:
        shutil.rmtree(temporary)


def verify_podspec(
    podspec_path: Path,
    archive_path: Path,
    expected_version: str,
) -> None:
    if podspec_path.is_symlink() or not podspec_path.is_file():
        fail("iOS podspec is not a regular file")
    if podspec_path.name != "KMediaVlc.podspec" or podspec_path.stat().st_size > 64 * 1024:
        fail("iOS podspec identity is invalid")
    expected = podspec(
        expected_version,
        EXPECTED_MINIMUM_IOS,
        f"kmedia-vlc-{expected_version}-ios-xcframeworks.zip",
        sha256(archive_path),
    )
    if podspec_path.read_text(encoding="utf-8") != expected:
        fail("iOS podspec differs from the verified archive")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--podspec", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    arguments = parser.parse_args()
    try:
        verify(
            arguments.archive.absolute(),
            arguments.expected_version,
            arguments.expected_revision,
            arguments.allow_audit_candidate,
        )
        verify_podspec(
            arguments.podspec.absolute(),
            arguments.archive.absolute(),
            arguments.expected_version,
        )
    except (IosArchiveError, OSError, UnicodeDecodeError) as error:
        print(f"iOS archive verification failed: {error}", file=sys.stderr)
        return 1
    print("iOS XCFramework archive verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
