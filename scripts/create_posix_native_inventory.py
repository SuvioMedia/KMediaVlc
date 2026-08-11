#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path


PINNED_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
PINNED_VERSION = "4.0.0-dev"
BASE_LICENSE = "LGPL-2.1-or-later"
AUDIT_NAME = "BINARY-COMPONENTS.json"
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
TARGETS = {
    "linux-x86_64": {"engine": "GLES2", "hdr10": False},
    "linux-aarch64": {"engine": "GLES2", "hdr10": False},
    "macos-aarch64": {"engine": "OPENGL", "hdr10": False},
}


def fail(message: str) -> None:
    raise ValueError(message)


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        fail(f"Cannot load the platform stager: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_policies(
    root: Path,
    target: str,
    allow_audit_candidate: bool,
) -> tuple[dict, dict]:
    if target.startswith("linux-"):
        stager = load_module(
            "kmediavlc_linux_stager",
            root / "scripts/stage_vlc_linux_runtime.py",
        )
        try:
            playback, binary, _ = stager.load_policy(root, target, allow_audit_candidate)
        except SystemExit as error:
            fail(str(error))
        return playback, binary
    if target == "macos-aarch64":
        stager = load_module(
            "kmediavlc_macos_stager",
            root / "scripts/stage_vlc_macos_runtime.py",
        )
        try:
            playback, binary, _ = stager.load_policy(root, allow_audit_candidate)
        except SystemExit as error:
            fail(str(error))
        return playback, binary
    fail(f"Unsupported POSIX runtime target: {target}")


def canonical_expression(license_ids: list[str]) -> str:
    if not license_ids or any(not isinstance(value, str) or not value for value in license_ids):
        fail("Native component license list is invalid.")
    return " AND ".join(sorted(set(license_ids)))


def checked_source_offer(version: str, source_offer: str) -> str:
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Native inventory version must be immutable non-SNAPSHOT SemVer.")
    expected = (
        f"https://github.com/SuvioMedia/KMediaVlc/releases/download/v{version}/"
        f"kmedia-vlc-{version}-corresponding-source.tar.gz"
    )
    if source_offer != expected:
        fail("Native inventory source offer does not match the exact release version.")
    return source_offer


def require_staged_file(staging: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        fail(f"Unsafe staged runtime path: {relative!r}")
    parts = relative.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        fail(f"Unsafe staged runtime path: {relative!r}")
    path = staging.joinpath(*parts)
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        fail(f"Native staging omits a real non-empty file: {relative}")
    try:
        path.resolve(strict=True).relative_to(staging)
    except (OSError, ValueError):
        fail(f"Native staging file escapes its root: {relative}")
    return path


def file_license(entry: dict, playback: dict, binary: dict) -> str:
    licenses = [BASE_LICENSE]
    component_ids = entry.get("sourceComponents")
    if not isinstance(component_ids, list) or component_ids != sorted(set(component_ids)):
        fail(f"Native source component list is not canonical: {entry.get('path')}")
    for component_id in component_ids:
        component = binary["components"].get(component_id)
        if not isinstance(component, dict):
            fail(f"Native file references an unknown component: {component_id}")
        component_licenses = component.get("licenseSpdx")
        if not isinstance(component_licenses, list):
            fail(f"Native component licenses are invalid: {component_id}")
        licenses.extend(component_licenses)
    module = entry.get("module")
    if module is not None:
        direct = playback.get("additionalDirectSourceLicenses", {}).get(module, [])
        binary_direct = binary.get("moduleAdditionalLicenses", {}).get(module, [])
        if direct != binary_direct:
            fail(f"Direct-source license policies disagree for module {module}.")
        licenses.extend(direct)
    return canonical_expression(licenses)


def create(
    root: Path,
    staging: Path,
    report_path: Path,
    output: Path,
    target: str,
    version: str,
    source_offer: str,
    allow_audit_candidate: bool = False,
) -> dict:
    root = root.resolve(strict=True)
    staging = staging.resolve(strict=True)
    report_path = report_path.resolve(strict=True)
    output = output.absolute()
    if target not in TARGETS:
        fail(f"Unsupported POSIX runtime target: {target}")
    if staging.is_symlink() or not staging.is_dir():
        fail("Native staging must be a real directory.")
    if any(path.is_symlink() for path in staging.rglob("*")):
        fail("Native staging must not contain symbolic links.")
    if report_path.is_symlink() or not report_path.is_file():
        fail("Native staging report must be a real file.")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        fail("Native inventory output must be a new file in a real directory.")
    source_offer = checked_source_offer(version, source_offer)
    playback, binary = load_policies(root, target, allow_audit_candidate)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    expected_audit_candidate = (
        playback["reviewStatus"] != "approved" or binary["reviewStatus"] != "approved"
    )
    if (
        not isinstance(report, dict)
        or report.get("schemaVersion") != 1
        or report.get("target") != target
        or report.get("vlcRevision") != PINNED_REVISION
        or report.get("reviewStatus") != playback["reviewStatus"]
        or report.get("binaryReviewStatus") != binary["reviewStatus"]
        or report.get("auditCandidate") is not expected_audit_candidate
    ):
        fail("Native staging report is not bound to the selected policies.")
    reported_files = report.get("files")
    if not isinstance(reported_files, list) or not reported_files:
        fail("Native staging report contains no files.")

    files: list[dict] = []
    reported_paths: set[str] = set()
    for entry in reported_files:
        if not isinstance(entry, dict):
            fail("Native staging report file entry is invalid.")
        relative = entry.get("path")
        path = require_staged_file(staging, relative)
        if relative in reported_paths:
            fail(f"Native staging report contains a duplicate file: {relative}")
        reported_paths.add(relative)
        if entry.get("size") != path.stat().st_size or entry.get("sha256") != sha256(path):
            fail(f"Native staging report hash differs from the staged file: {relative}")
        reported_role = entry.get("role")
        role = "DEPENDENCY" if reported_role == "SUPPORT" else reported_role
        if role not in {"BRIDGE", "LIBVLC", "CORE", "PLUGIN", "DEPENDENCY", "DATA"}:
            fail(f"Native staging report contains an unsupported role: {reported_role}")
        files.append(
            {
                "path": relative,
                "component": "kmediavlc" if role == "BRIDGE" else "videolan-vlc",
                "licenseSpdx": file_license(entry, playback, binary),
                "role": role,
                "source": source_offer,
                "linkage": "NONE" if role == "DATA" else "DYNAMIC",
            }
        )

    actual_paths = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != reported_paths:
        fail(
            "Native staging differs from its closed report; "
            f"missing={sorted(reported_paths - actual_paths)}, "
            f"extra={sorted(actual_paths - reported_paths)}"
        )

    audit_destination = staging / AUDIT_NAME
    shutil.copyfile(report_path, audit_destination)
    files.append(
        {
            "path": AUDIT_NAME,
            "component": "kmediavlc",
            "licenseSpdx": BASE_LICENSE,
            "role": "LEGAL",
            "source": source_offer,
            "linkage": "NONE",
        }
    )
    inventory = {
        "schemaVersion": 1,
        "provenance": "source-build",
        "libvlcVersion": PINNED_VERSION,
        "libvlcRevision": PINNED_REVISION,
        "target": target,
        "gplComponents": False,
        "nonfreeComponents": False,
        "frameDeliveryModes": ["GPU_PUSH", "CPU_PULL"],
        "renderEngines": [TARGETS[target]["engine"]],
        "pluginDirectory": "lib/vlc/plugins",
        "hdr10Metadata": TARGETS[target]["hdr10"],
        "files": files,
    }
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-offer", required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    arguments = parser.parse_args()
    inventory = create(
        arguments.root,
        arguments.staging,
        arguments.report,
        arguments.output,
        arguments.target,
        arguments.version,
        arguments.source_offer,
        arguments.allow_audit_candidate,
    )
    print(f"Created closed {arguments.target} inventory with {len(inventory['files'])} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
