#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


PINNED_REVISION = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
PINNED_VERSION = "4.0.0-dev"
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
BASE_LICENSE = "LGPL-2.1-or-later"
AUDIT_NAME = "BINARY-COMPONENTS.json"


def fail(message: str) -> None:
    raise ValueError(message)


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail(f"JSON policy root must be an object: {path}")
    return value


def load_policies(root: Path, allow_audit_candidate: bool) -> tuple[dict, dict, list[str]]:
    playback = load_json(root / "compliance/policy/windows-x86_64-playback-modules.json")
    binary = load_json(root / "compliance/policy/windows-x86_64-binary-components.json")
    for policy in (playback, binary):
        if (
            policy.get("schemaVersion") != 1
            or policy.get("target") != "windows-x86_64"
            or policy.get("vlcRevision") != PINNED_REVISION
        ):
            fail("Windows native inventory policy identity is invalid.")
    if (
        playback.get("reviewStatus") != "approved"
        or binary.get("reviewStatus") != "approved"
    ) and not allow_audit_candidate:
        fail("Windows native inventory has not completed source and link review.")
    families = playback.get("modulesByFamily")
    if not isinstance(families, dict):
        fail("Windows playback module policy is empty.")
    modules = [name for family in sorted(families) for name in families[family]]
    if len(modules) != 90 or len(set(modules)) != len(modules):
        fail("Windows playback module policy does not contain exactly 90 modules.")
    components = binary.get("components")
    module_components = binary.get("moduleComponents")
    if not isinstance(components, dict) or not isinstance(module_components, dict):
        fail("Windows binary component policy is empty.")
    if (
        playback.get("coreAdditionalDirectSourceLicenses") != ["MIT"]
        or binary.get("coreAdditionalLicenses")
        != playback.get("coreAdditionalDirectSourceLicenses")
    ):
        fail("Windows core direct-source license policies are incomplete or disagree.")
    if not set(module_components).issubset(modules):
        fail("Windows binary components reference an unselected module.")
    return playback, binary, modules


def canonical_expression(license_ids: list[str]) -> str:
    if not license_ids or any(not isinstance(value, str) or not value for value in license_ids):
        fail("Binary component license list is invalid.")
    return " AND ".join(sorted(set(license_ids)))


def module_licenses(module: str, playback: dict, binary: dict) -> tuple[str, list[str]]:
    component_ids = binary["moduleComponents"].get(module, [])
    licenses = [BASE_LICENSE]
    licenses.extend(binary.get("moduleAdditionalLicenses", {}).get(module, []))
    for component_id in component_ids:
        component = binary["components"].get(component_id)
        if not isinstance(component, dict):
            fail(f"Unknown binary component for module {module}: {component_id}")
        licenses.extend(component.get("licenseSpdx", []))
    direct = playback.get("additionalDirectSourceLicenses", {}).get(module, [])
    if sorted(direct) != sorted(binary.get("moduleAdditionalLicenses", {}).get(module, [])):
        fail(f"Direct-source license policies disagree for module {module}.")
    return canonical_expression(licenses), component_ids


def require_real_file(staging: Path, relative: str) -> Path:
    path = staging.joinpath(*relative.split("/"))
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        fail(f"Windows native staging omits a real non-empty file: {relative}")
    try:
        path.resolve(strict=True).relative_to(staging)
    except (OSError, ValueError):
        fail(f"Windows native staging file escapes its root: {relative}")
    return path


def create(
    root: Path,
    staging: Path,
    output: Path,
    version: str,
    source_offer: str,
    allow_audit_candidate: bool = False,
) -> dict:
    root = root.resolve(strict=True)
    staging = staging.resolve(strict=True)
    output = output.absolute()
    if staging.is_symlink() or not staging.is_dir():
        fail("Windows native staging must be a real directory.")
    if any(path.is_symlink() for path in staging.rglob("*")):
        fail("Windows native staging must not contain symbolic links.")
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        fail("Windows inventory output must be a new file in a real directory.")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Windows inventory version must be immutable non-SNAPSHOT SemVer.")
    expected_offer = (
        f"https://github.com/SuvioMedia/KMediaVlc/releases/download/v{version}/"
        f"kmedia-vlc-{version}-corresponding-source.tar.gz"
    )
    if source_offer != expected_offer:
        fail("Windows inventory source offer does not match the exact release version.")
    playback, binary, modules = load_policies(root, allow_audit_candidate)
    if (staging / AUDIT_NAME).exists():
        fail("Windows binary component audit output already exists in staging.")

    runtime_entries: list[dict] = []
    files: list[dict] = []

    def add(relative: str, component: str, license_spdx: str, role: str, linkage: str) -> None:
        path = require_real_file(staging, relative)
        files.append(
            {
                "path": relative,
                "component": component,
                "licenseSpdx": license_spdx,
                "role": role,
                "source": source_offer,
                "linkage": linkage,
            }
        )
        runtime_entries.append(
            {
                "path": relative,
                "sha256": sha256(path),
                "size": path.stat().st_size,
                "licenseSpdx": license_spdx,
                "sourceComponents": [],
            }
        )

    add(
        "bin/kmediavlc_bridge.dll",
        "kmediavlc",
        "LGPL-2.1-or-later",
        "BRIDGE",
        "DYNAMIC",
    )
    add("bin/libvlc.dll", "videolan-vlc", BASE_LICENSE, "LIBVLC", "DYNAMIC")
    core_licenses = [BASE_LICENSE]
    core_licenses.extend(binary["coreAdditionalLicenses"])
    for component_id in binary.get("coreComponents", []):
        core_licenses.extend(binary["components"][component_id]["licenseSpdx"])
    add(
        "bin/libvlccore-9.dll",
        "videolan-vlc",
        canonical_expression(core_licenses),
        "CORE",
        "DYNAMIC",
    )
    runtime_entries[-1]["sourceComponents"] = binary.get("coreComponents", [])
    for module in sorted(modules):
        relative = f"lib/vlc/plugins/lib{module}_plugin.dll"
        expression, component_ids = module_licenses(module, playback, binary)
        add(relative, "videolan-vlc", expression, "PLUGIN", "DYNAMIC")
        runtime_entries[-1]["sourceComponents"] = component_ids
    add(
        "lib/vlc/plugins/plugins.dat",
        "videolan-vlc",
        BASE_LICENSE,
        "DATA",
        "NONE",
    )
    add(
        "SHA256SUMS",
        "kmediavlc",
        "LGPL-2.1-or-later",
        "DATA",
        "NONE",
    )

    audit = {
        "schemaVersion": 1,
        "target": "windows-x86_64",
        "vlcRevision": PINNED_REVISION,
        "playbackReviewStatus": playback["reviewStatus"],
        "binaryReviewStatus": binary["reviewStatus"],
        "components": binary["components"],
        "runtimeFiles": runtime_entries,
    }
    audit_path = staging / AUDIT_NAME
    with audit_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    files.append(
        {
            "path": AUDIT_NAME,
            "component": "kmediavlc",
            "licenseSpdx": "LGPL-2.1-or-later",
            "role": "LEGAL",
            "source": source_offer,
            "linkage": "NONE",
        }
    )

    expected = {entry["path"] for entry in files}
    actual = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual != expected:
        fail(
            "Windows native staging inventory differs from the closed policy; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )
    inventory = {
        "schemaVersion": 1,
        "provenance": "source-build",
        "libvlcVersion": PINNED_VERSION,
        "libvlcRevision": PINNED_REVISION,
        "target": "windows-x86_64",
        "gplComponents": False,
        "nonfreeComponents": False,
        "frameDeliveryModes": ["GPU_PUSH", "CPU_PULL"],
        "renderEngines": ["D3D11"],
        "pluginDirectory": "lib/vlc/plugins",
        "hdr10Metadata": True,
        "files": files,
    }
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    return inventory


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-offer", required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    arguments = parser.parse_args()
    inventory = create(
        arguments.root,
        arguments.staging,
        arguments.output,
        arguments.version,
        arguments.source_offer,
        arguments.allow_audit_candidate,
    )
    print(f"Created closed Windows inventory with {len(inventory['files'])} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
