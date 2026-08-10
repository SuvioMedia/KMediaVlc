#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

"""Assemble audited iOS device/simulator frameworks into one CocoaPods payload."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from stage_vlc_ios_frameworks import (  # noqa: E402
    EXPECTED_MINIMUM_IOS,
    EXPECTED_RAW_PLUGIN_COUNT,
    FIXED_FRAMEWORKS,
    PINNED_REVISION,
    TARGETS,
    audit_macho,
    executable_for_plugin,
)


SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_SELECTED_PLUGIN_COUNT = 84
EXPECTED_FRAMEWORK_COUNT = EXPECTED_SELECTED_PLUGIN_COUNT + len(FIXED_FRAMEWORKS)
SYSTEM_DEPENDENCY_PREFIXES = ("/System/Library/", "/usr/lib/")
RECIPE_FILES = (
    "build-recipes/ios.json",
    "build-recipes/vlc-apple.conf",
    "build-recipes/vlc-apple-native.ini",
    "build-recipes/vlc-contrib-utfcpp-rules.mak",
    "build-recipes/patches/fribidi-meson-native-generator.patch",
    "build-recipes/patches/vlc-ios-meson-native-compiler.patch",
)
POLICY_FILES = (
    "compliance/policy/ios-binary-components.json",
    "compliance/policy/ios-playback-modules.json",
    "compliance/policy/release-policy.json",
)


class IosAssemblyError(ValueError):
    """The iOS aggregate differs from its closed distribution contract."""


def fail(message: str) -> None:
    raise IosAssemblyError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_json(path: Path, label: str) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
        fail(f"{label} must be a bounded regular file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IosAssemblyError(f"cannot read {label}") from error


def reject_unsafe_tree(root: Path, label: str) -> None:
    if root.is_symlink() or not root.is_dir():
        fail(f"{label} must be a real directory")
    for path in root.rglob("*"):
        if path.is_symlink():
            fail(f"{label} contains a symbolic link")
        relative = path.relative_to(root)
        if any(part.casefold().startswith(".env") for part in relative.parts):
            fail(f"{label} contains a forbidden secret-bearing path")
        if not path.is_dir() and not path.is_file():
            fail(f"{label} contains a special filesystem entry")


def expected_frameworks(root: Path = ROOT) -> list[dict[str, str]]:
    policy = read_json(
        root / "compliance/policy/ios-playback-modules.json",
        "iOS playback module policy",
    )
    families = policy.get("modulesByFamily") if isinstance(policy, dict) else None
    if (
        policy.get("schemaVersion") != 1
        or policy.get("vlcRevision") != PINNED_REVISION
        or not isinstance(families, dict)
    ):
        fail("iOS playback module policy is invalid")

    records = [
        {
            "frameworkName": FIXED_FRAMEWORKS["BRIDGE"],
            "role": "BRIDGE",
        },
        {
            "frameworkName": FIXED_FRAMEWORKS["LIBVLC"],
            "role": "LIBVLC",
        },
        {
            "frameworkName": FIXED_FRAMEWORKS["CORE"],
            "role": "CORE",
        },
    ]
    seen: set[str] = set()
    for family in sorted(families):
        modules = families[family]
        if not isinstance(modules, list) or modules != sorted(modules):
            fail(f"iOS playback module family is not sorted: {family}")
        for module in modules:
            if not isinstance(module, str) or module in seen:
                fail("iOS playback module inventory is invalid")
            seen.add(module)
            records.append(
                {
                    "frameworkName": executable_for_plugin(module),
                    "role": "PLUGIN",
                    "family": family,
                    "module": module,
                }
            )
    if len(seen) != EXPECTED_SELECTED_PLUGIN_COUNT or len(records) != EXPECTED_FRAMEWORK_COUNT:
        fail("iOS framework inventory has drifted from its closed count")
    return records


def expected_install_name(role: str, framework_name: str) -> str:
    if role == "PLUGIN":
        return f"@rpath/{framework_name}.framework/{framework_name}"
    if FIXED_FRAMEWORKS.get(role) != framework_name:
        fail(f"unsupported iOS framework role: {role}")
    return f"@rpath/{framework_name}.framework/{framework_name}"


def expected_source_components(
    descriptor: dict[str, str],
    binary_policy: dict[str, Any],
) -> list[str]:
    if descriptor["role"] == "CORE":
        return binary_policy["coreComponents"]
    if descriptor["role"] == "PLUGIN":
        return binary_policy["moduleComponents"].get(descriptor["module"], [])
    return []


def verify_framework_tree(
    frameworks: Path,
    report_record: dict[str, Any],
    descriptor: dict[str, str],
    target_name: str,
) -> None:
    framework_name = descriptor["frameworkName"]
    framework = frameworks / f"{framework_name}.framework"
    reject_unsafe_tree(framework, f"{target_name} {framework_name}.framework")
    binary = framework / framework_name
    if binary.is_symlink() or not binary.is_file():
        fail(f"{target_name} framework binary is missing: {framework_name}")
    if report_record.get("sha256") != sha256(binary) or report_record.get("size") != binary.stat().st_size:
        fail(f"{target_name} framework binary differs from its report: {framework_name}")

    headers = framework / "Headers"
    module_map = framework / "Modules/module.modulemap"
    metadata_path = framework / "Info.plist"
    if not headers.is_dir() or not module_map.is_file() or not metadata_path.is_file():
        fail(f"{target_name} framework metadata is incomplete: {framework_name}")
    actual_headers = sorted(path.name for path in headers.iterdir() if path.is_file())
    if not actual_headers or actual_headers != report_record.get("headers"):
        fail(f"{target_name} framework header inventory differs: {framework_name}")
    expected_files = {
        framework_name,
        "Info.plist",
        "Modules/module.modulemap",
        *(f"Headers/{name}" for name in actual_headers),
    }
    actual_files = {
        path.relative_to(framework).as_posix()
        for path in framework.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        fail(f"{target_name} framework contains an unexpected file: {framework_name}")
    try:
        with metadata_path.open("rb") as source:
            metadata = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as error:
        raise IosAssemblyError(f"{framework_name} Info.plist is invalid") from error
    if (
        metadata.get("CFBundleExecutable") != framework_name
        or metadata.get("CFBundlePackageType") != "FMWK"
        or metadata.get("MinimumOSVersion") != EXPECTED_MINIMUM_IOS
    ):
        fail(f"{target_name} framework metadata is invalid: {framework_name}")

    try:
        observed_macho = audit_macho(
            binary,
            descriptor["role"],
            framework_name,
            TARGETS[target_name],
            Path("/usr/bin/otool"),
            Path("/usr/bin/lipo"),
        )
    except SystemExit as error:
        raise IosAssemblyError(str(error)) from error
    if observed_macho != report_record.get("machO"):
        fail(f"{target_name} Mach-O audit differs from its report: {framework_name}")


def verify_slice(
    frameworks: Path,
    report_path: Path,
    target_name: str,
    allow_audit_candidate: bool,
) -> dict[str, Any]:
    reject_unsafe_tree(frameworks, f"{target_name} framework slice")
    report = read_json(report_path, f"{target_name} framework report")
    report_keys = {
        "architecture",
        "binaryReviewStatus",
        "components",
        "excludedPluginCount",
        "frameworkCount",
        "frameworks",
        "minimumIos",
        "rawPluginCount",
        "reviewStatus",
        "schemaVersion",
        "selectedPluginCount",
        "simulator",
        "target",
        "vlcRevision",
    }
    target = TARGETS[target_name]
    if (
        not isinstance(report, dict)
        or set(report) != report_keys
        or report.get("schemaVersion") != 1
        or report.get("target") != target_name
        or report.get("architecture") != "arm64"
        or report.get("minimumIos") != EXPECTED_MINIMUM_IOS
        or report.get("simulator") != target["simulator"]
        or report.get("vlcRevision") != PINNED_REVISION
        or report.get("rawPluginCount") != EXPECTED_RAW_PLUGIN_COUNT
        or report.get("selectedPluginCount") != EXPECTED_SELECTED_PLUGIN_COUNT
        or report.get("excludedPluginCount")
        != EXPECTED_RAW_PLUGIN_COUNT - EXPECTED_SELECTED_PLUGIN_COUNT
        or report.get("frameworkCount") != EXPECTED_FRAMEWORK_COUNT
    ):
        fail(f"{target_name} framework report identity is invalid")

    playback_policy = read_json(
        ROOT / "compliance/policy/ios-playback-modules.json",
        "iOS playback module policy",
    )
    binary_policy = read_json(
        ROOT / "compliance/policy/ios-binary-components.json",
        "iOS binary component policy",
    )
    if (
        report.get("reviewStatus") != playback_policy.get("reviewStatus")
        or report.get("binaryReviewStatus") != binary_policy.get("reviewStatus")
        or report.get("components") != binary_policy.get("components")
    ):
        fail(f"{target_name} report differs from current iOS policies")
    if not allow_audit_candidate and (
        report["reviewStatus"] != "approved"
        or report["binaryReviewStatus"] != "approved"
    ):
        fail("iOS XCFramework assembly requires approved dependency policies")

    descriptors = expected_frameworks()
    records = report.get("frameworks")
    if not isinstance(records, list) or len(records) != len(descriptors):
        fail(f"{target_name} framework report is incomplete")
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            fail(f"{target_name} framework report contains a malformed record")
        binary = record.get("binary")
        if not isinstance(binary, str) or binary in by_name:
            fail(f"{target_name} framework report contains a duplicate")
        by_name[binary] = record

    expected_names = {descriptor["frameworkName"] for descriptor in descriptors}
    actual_directories = {
        path.name.removesuffix(".framework")
        for path in frameworks.iterdir()
        if path.is_dir() and path.name.endswith(".framework")
    }
    if set(by_name) != expected_names or actual_directories != expected_names:
        fail(f"{target_name} framework slice has a missing or extra framework")

    for descriptor in descriptors:
        framework_name = descriptor["frameworkName"]
        record = by_name[framework_name]
        expected_keys = {
            "binary",
            "framework",
            "headers",
            "machO",
            "role",
            "sha256",
            "size",
            "sourceComponents",
        }
        if descriptor["role"] == "PLUGIN":
            expected_keys.update({"family", "module"})
        if (
            set(record) != expected_keys
            or record.get("framework") != f"{framework_name}.framework"
            or record.get("role") != descriptor["role"]
            or record.get("family") != descriptor.get("family")
            or record.get("module") != descriptor.get("module")
            or record.get("sourceComponents")
            != expected_source_components(descriptor, binary_policy)
        ):
            fail(f"{target_name} framework record is invalid: {framework_name}")
        verify_framework_tree(frameworks, record, descriptor, target_name)
    report["_recordsByName"] = by_name
    return report


def compare_framework_copies(source: Path, copied: Path, label: str) -> None:
    reject_unsafe_tree(copied, label)
    source_files = {
        path.relative_to(source).as_posix(): path
        for path in source.rglob("*")
        if path.is_file()
    }
    copied_files = {
        path.relative_to(copied).as_posix(): path
        for path in copied.rglob("*")
        if path.is_file()
    }
    if set(source_files) != set(copied_files):
        fail(f"{label} differs from its staged framework")
    for relative, source_file in source_files.items():
        copied_file = copied_files[relative]
        if source_file.stat().st_size != copied_file.stat().st_size or sha256(source_file) != sha256(copied_file):
            fail(f"{label} changed during XCFramework assembly: {relative}")


def verify_xcframework(
    xcframework: Path,
    descriptor: dict[str, str],
    device_frameworks: Path,
    simulator_frameworks: Path,
    device_record: dict[str, Any],
    simulator_record: dict[str, Any],
) -> dict[str, Any]:
    framework_name = descriptor["frameworkName"]
    reject_unsafe_tree(xcframework, f"{framework_name}.xcframework")
    try:
        with (xcframework / "Info.plist").open("rb") as source:
            metadata = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as error:
        raise IosAssemblyError(f"{framework_name} XCFramework metadata is invalid") from error
    libraries = metadata.get("AvailableLibraries")
    if not isinstance(libraries, list) or len(libraries) != 2:
        fail(f"{framework_name}: expected device and simulator slices")

    source_by_variant = {
        "device": (device_frameworks, device_record, "ios-arm64"),
        "simulator": (
            simulator_frameworks,
            simulator_record,
            "ios-simulator-arm64",
        ),
    }
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for library in libraries:
        if not isinstance(library, dict):
            fail(f"{framework_name}: malformed XCFramework library record")
        identifier = library.get("LibraryIdentifier")
        library_path = library.get("LibraryPath")
        variant_value = library.get("SupportedPlatformVariant")
        variant = "simulator" if variant_value == "simulator" else "device"
        if (
            not isinstance(identifier, str)
            or Path(identifier).name != identifier
            or library_path != f"{framework_name}.framework"
            or library.get("SupportedPlatform") != "ios"
            or library.get("SupportedArchitectures") != ["arm64"]
            or variant_value not in {None, "simulator"}
            or variant in seen
        ):
            fail(f"{framework_name}: XCFramework slice identity is invalid")
        seen.add(variant)
        source_root, source_record, source_target = source_by_variant[variant]
        source_framework = source_root / f"{framework_name}.framework"
        copied_framework = xcframework / identifier / f"{framework_name}.framework"
        compare_framework_copies(
            source_framework,
            copied_framework,
            f"{framework_name} {variant} slice",
        )
        binary = copied_framework / framework_name
        if sha256(binary) != source_record["sha256"] or binary.stat().st_size != source_record["size"]:
            fail(f"{framework_name}: XCFramework binary differs from its source report")
        records.append(
            {
                "identifier": identifier,
                "sha256": source_record["sha256"],
                "size": source_record["size"],
                "sourceTarget": source_target,
                "variant": variant,
            }
        )
    if seen != {"device", "simulator"}:
        fail(f"{framework_name}: XCFramework matrix is incomplete")
    result: dict[str, Any] = {
        "frameworkName": framework_name,
        "role": descriptor["role"],
        "slices": sorted(records, key=lambda item: item["variant"]),
        "xcframework": xcframework.name,
    }
    if descriptor["role"] == "PLUGIN":
        result["family"] = descriptor["family"]
        result["module"] = descriptor["module"]
    return result


def podspec(
    version: str,
    minimum_ios: str,
    archive_name: str,
    archive_sha256: str,
) -> str:
    if not SEMVER.fullmatch(version):
        fail("iOS CocoaPod version must be a stable semantic version")
    if len(archive_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in archive_sha256
    ):
        fail("iOS archive SHA-256 is invalid")
    return f"""Pod::Spec.new do |spec|
  spec.name                  = 'KMediaVlc'
  spec.version               = '{version}'
  spec.summary               = 'Bundled, audited libVLC 4 runtime for KMediaPlayer on iOS.'
  spec.homepage              = 'https://github.com/SuvioMedia/KMediaVlc'
  spec.license               = {{ :type => 'Proprietary', :file => 'LICENSE' }}
  spec.author                = {{ 'SuvioMedia' => 'SuvioMedia' }}
  spec.source                = {{
    :http => 'https://github.com/SuvioMedia/KMediaVlc/releases/download/v{version}/{archive_name}',
    :sha256 => '{archive_sha256}'
  }}
  spec.ios.deployment_target = '{minimum_ios}'
  spec.vendored_frameworks   = 'Frameworks/*.xcframework'
  spec.preserve_paths        = ['compliance/**/*', 'LICENSES/**/*', 'NOTICE',
                                'THIRD_PARTY_NOTICES.md']
end
"""


def zip_tree(root: Path, archive: Path, source_date_epoch: int) -> None:
    if archive.exists() or archive.is_symlink():
        fail("iOS archive output must not already exist")
    timestamp = max(source_date_epoch, 315532800)
    date_time = tuple(time.gmtime(timestamp)[:6])
    with zipfile.ZipFile(
        archive,
        "x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as output:
        paths = sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
        for path in paths:
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=date_time)
            mode = stat.S_IMODE(path.stat().st_mode)
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, path.read_bytes())


def git_output(arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or "git command failed"
        raise IosAssemblyError(detail)
    return result.stdout.strip()


def run_xcodebuild(command: list[str]) -> None:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        raise IosAssemblyError(f"xcodebuild failed: {detail}")


def assemble(
    device_frameworks: Path,
    device_report: Path,
    simulator_frameworks: Path,
    simulator_report: Path,
    output: Path,
    archive: Path,
    podspec_output: Path,
    version: str,
    revision: str,
    allow_audit_candidate: bool,
) -> None:
    expected_archive = f"kmedia-vlc-{version}-ios-xcframeworks.zip"
    if not SEMVER.fullmatch(version) or not REVISION.fullmatch(revision):
        fail("iOS assembly version or revision is invalid")
    if archive.name != expected_archive or podspec_output.name != "KMediaVlc.podspec":
        fail("iOS assembly output names are not release-canonical")
    for path, label in (
        (output, "aggregate"),
        (archive, "archive"),
        (podspec_output, "podspec"),
    ):
        if path.exists() or path.is_symlink():
            fail(f"iOS {label} output must be a new path")
        path.parent.resolve(strict=True)
    if output in archive.parents or output in podspec_output.parents:
        fail("iOS archive and podspec must remain outside the aggregate")
    if git_output(["rev-parse", "HEAD"]) != revision:
        fail("iOS recipe revision is not the checked-out commit")
    if git_output(["status", "--porcelain"]):
        fail("iOS release assembly requires a clean repository checkout")

    device = verify_slice(
        device_frameworks,
        device_report,
        "ios-arm64",
        allow_audit_candidate,
    )
    simulator = verify_slice(
        simulator_frameworks,
        simulator_report,
        "ios-simulator-arm64",
        allow_audit_candidate,
    )
    audit_candidate = any(
        status != "approved"
        for status in (
            device["reviewStatus"],
            device["binaryReviewStatus"],
            simulator["reviewStatus"],
            simulator["binaryReviewStatus"],
        )
    )
    revision_epoch = int(git_output(["show", "-s", "--format=%ct", revision]))
    descriptors = expected_frameworks()
    temporary = Path(tempfile.mkdtemp(prefix=".kmediavlc-ios-aggregate-", dir=output.parent))
    try:
        frameworks_output = temporary / "Frameworks"
        frameworks_output.mkdir()
        inventory_records: list[dict[str, Any]] = []
        for descriptor in descriptors:
            framework_name = descriptor["frameworkName"]
            destination = frameworks_output / f"{framework_name}.xcframework"
            run_xcodebuild(
                [
                    "/usr/bin/xcodebuild",
                    "-create-xcframework",
                    "-framework",
                    str(device_frameworks / f"{framework_name}.framework"),
                    "-framework",
                    str(simulator_frameworks / f"{framework_name}.framework"),
                    "-output",
                    str(destination),
                ]
            )
            inventory_records.append(
                verify_xcframework(
                    destination,
                    descriptor,
                    device_frameworks,
                    simulator_frameworks,
                    device["_recordsByName"][framework_name],
                    simulator["_recordsByName"][framework_name],
                )
            )

        compliance = temporary / "compliance"
        recipes = compliance / "build-recipes"
        policies = compliance / "policy"
        recipes.mkdir(parents=True)
        policies.mkdir()
        copied_evidence: list[Path] = []
        for source, destination_name in (
            (device_report, "ios-arm64-frameworks.json"),
            (simulator_report, "ios-simulator-arm64-frameworks.json"),
        ):
            destination = compliance / destination_name
            shutil.copyfile(source, destination)
            copied_evidence.append(destination)
        for relative in RECIPE_FILES:
            source = ROOT / relative
            destination = recipes / Path(relative).relative_to("build-recipes")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied_evidence.append(destination)
        for relative in POLICY_FILES:
            source = ROOT / relative
            destination = policies / Path(relative).name
            shutil.copyfile(source, destination)
            copied_evidence.append(destination)
        (compliance / "build-recipe-revision.txt").write_text(
            revision + "\n",
            encoding="utf-8",
        )
        evidence_hashes = {
            path.relative_to(temporary).as_posix(): sha256(path)
            for path in sorted(copied_evidence)
        }
        inventory = {
            "auditCandidate": audit_candidate,
            "evidenceSha256": evidence_hashes,
            "frameworkCount": len(inventory_records),
            "frameworks": inventory_records,
            "minimumIos": EXPECTED_MINIMUM_IOS,
            "recipeRevision": revision,
            "schemaVersion": 1,
            "selectedPluginCount": EXPECTED_SELECTED_PLUGIN_COUNT,
            "version": version,
            "vlcRevision": PINNED_REVISION,
        }
        (compliance / "xcframework-inventory.json").write_text(
            json.dumps(inventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for name in ("LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md"):
            shutil.copyfile(ROOT / name, temporary / name)
        shutil.copytree(ROOT / "LICENSES", temporary / "LICENSES")
        reject_unsafe_tree(temporary, "iOS aggregate")
        if len(list(frameworks_output.glob("*.xcframework"))) != EXPECTED_FRAMEWORK_COUNT:
            fail("iOS XCFramework aggregate is incomplete")
        temporary.rename(output)
        zip_tree(output, archive, revision_epoch)
        podspec_output.write_text(
            podspec(version, EXPECTED_MINIMUM_IOS, archive.name, sha256(archive)),
            encoding="utf-8",
        )
    except BaseException:
        if output.exists() and not output.is_symlink():
            shutil.rmtree(output)
        if archive.exists() and not archive.is_symlink():
            archive.unlink()
        if podspec_output.exists() and not podspec_output.is_symlink():
            podspec_output.unlink()
        raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device-frameworks", type=Path, required=True)
    parser.add_argument("--device-report", type=Path, required=True)
    parser.add_argument("--simulator-frameworks", type=Path, required=True)
    parser.add_argument("--simulator-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--podspec", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    arguments = parser.parse_args()
    try:
        assemble(
            arguments.device_frameworks.resolve(strict=True),
            arguments.device_report.resolve(strict=True),
            arguments.simulator_frameworks.resolve(strict=True),
            arguments.simulator_report.resolve(strict=True),
            arguments.output.absolute(),
            arguments.archive.absolute(),
            arguments.podspec.absolute(),
            arguments.version,
            arguments.revision,
            arguments.allow_audit_candidate,
        )
    except (
        IosAssemblyError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        print(f"iOS XCFramework assembly failed: {error}", file=sys.stderr)
        return 1
    print(arguments.archive.absolute())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
