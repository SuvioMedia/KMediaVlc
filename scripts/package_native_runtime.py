# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


PINNED_VERSION = "4.0.0-dev"
PINNED_REVISION = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
BRIDGE_ABI_VERSION = 2
ALLOWED_TARGETS = {
    "windows-x86_64": {"D3D11"},
    "windows-aarch64": {"D3D11"},
    "macos-aarch64": {"OPENGL"},
    "linux-x86_64": {"GLES2"},
    "linux-aarch64": {"GLES2"},
}
PLATFORM_REVIEW_POLICIES = {
    "windows-x86_64": (
        "compliance/policy/windows-x86_64-playback-modules.json",
        "compliance/policy/windows-x86_64-binary-components.json",
    ),
    "macos-aarch64": (
        "compliance/policy/macos-aarch64-playback-modules.json",
        "compliance/policy/macos-aarch64-binary-components.json",
    ),
    "linux-x86_64": (
        "compliance/policy/linux-playback-modules.json",
        "compliance/policy/linux-binary-components.json",
    ),
    "linux-aarch64": (
        "compliance/policy/linux-playback-modules.json",
        "compliance/policy/linux-binary-components.json",
    ),
}
REQUIRED_ROLES = {"BRIDGE", "LIBVLC", "CORE", "PLUGIN"}
COMMIT_REVISION = re.compile(r"[0-9a-f]{40}")


def fail(message: str) -> None:
    raise SystemExit(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_path(raw: str) -> PurePosixPath:
    if not raw or "\\" in raw or raw.startswith("/") or "//" in raw:
        fail(f"Unsafe inventory path: {raw!r}")
    path = PurePosixPath(raw)
    if any(part in {"", ".", ".."} for part in path.parts):
        fail(f"Unsafe inventory path: {raw!r}")
    return path


def validate_source_reference(source: object, label: str) -> str:
    if not isinstance(source, str) or not source or "\\" in source:
        fail(f"Invalid corresponding-source reference for {label}")
    parsed = urlparse(source)
    if parsed.scheme:
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            fail(f"Invalid corresponding-source URL for {label}")
    else:
        safe_path(source)
    return source


def validate_recipe_revision(revision: str) -> str:
    if not COMMIT_REVISION.fullmatch(revision):
        fail("Recipe revision must be an exact lowercase forty-character Git commit.")
    return revision


def load_policy(root: Path) -> dict:
    policy = json.loads((root / "compliance/policy/release-policy.json").read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        fail("Release policy root must be an object.")
    return policy


def load_inventory(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail("Component inventory root must be an object.")
    return value


def require_approved_platform_review(root: Path, target: str) -> None:
    policy_paths = PLATFORM_REVIEW_POLICIES.get(target)
    if policy_paths is None:
        fail(f"No closed publication policy exists for native target {target}.")
    for relative in policy_paths:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            fail(f"Native target policy is missing or unsafe: {relative}")
        policy = load_inventory(path)
        if policy.get("schemaVersion") != 1 or policy.get("vlcRevision") != PINNED_REVISION:
            fail(f"Native target policy identity is invalid: {relative}")
        declared_target = policy.get("target")
        declared_targets = policy.get("targets")
        if declared_target != target and (
            not isinstance(declared_targets, list) or target not in declared_targets
        ):
            fail(f"Native target policy does not cover {target}: {relative}")
        if policy.get("reviewStatus") != "approved":
            fail(f"Native target policy review is not approved: {relative}")


def validate_license_expression(expression: object, allowed_licenses: set[str]) -> str:
    if not isinstance(expression, str) or not expression:
        fail("License expression must be a non-empty canonical SPDX string.")
    identifiers = expression.split(" AND ")
    if identifiers != sorted(set(identifiers)):
        fail(f"License expression is not a sorted conjunction: {expression!r}")
    unknown = [identifier for identifier in identifiers if identifier not in allowed_licenses]
    if unknown:
        fail(f"Forbidden or unknown license expression {expression!r}")
    return expression


def validate_inventory(
    inventory: dict,
    policy: dict,
    target: str,
    staging: Path,
    inventory_path: Path | None = None,
) -> list[dict]:
    staging = staging.resolve(strict=True)
    if policy.get("bridgeAbiVersion") != BRIDGE_ABI_VERSION:
        fail("Release policy does not match the native bridge ABI.")
    if inventory.get("schemaVersion") != 1:
        fail("Unsupported component inventory schema.")
    if inventory.get("provenance") != "source-build":
        fail("Only a source-built runtime is release eligible; stock nightlies are forbidden.")
    if inventory.get("libvlcVersion") != PINNED_VERSION or inventory.get("libvlcRevision") != PINNED_REVISION:
        fail("Component inventory does not match the pinned libVLC revision.")
    if inventory.get("target") != target:
        fail("Component inventory targets a different platform.")
    if inventory.get("gplComponents") is not False or inventory.get("nonfreeComponents") is not False:
        fail("GPL or nonfree components are forbidden in the bundled runtime.")
    modes = set(inventory.get("frameDeliveryModes", []))
    if modes != {"GPU_PUSH", "CPU_PULL"}:
        fail("Runtime must expose exactly GPU_PUSH and CPU_PULL.")
    engines = set(inventory.get("renderEngines", []))
    if engines != ALLOWED_TARGETS[target]:
        fail("Runtime render engines do not exactly match the platform policy.")

    files = inventory.get("files")
    if not isinstance(files, list) or not files:
        fail("Component inventory must contain files.")
    allowed_licenses = set(policy["allowedLicenseSpdx"])
    forbidden_families = set(policy["forbiddenPluginFamilies"])
    roles: set[str] = set()
    inventoried: set[str] = set()
    validated: list[dict] = []
    for entry in files:
        if not isinstance(entry, dict):
            fail("Inventory file entry must be an object.")
        required = {"path", "component", "licenseSpdx", "role", "source", "linkage"}
        if set(entry) != required:
            fail(f"Inventory file fields are not exact: {entry!r}")
        relative = safe_path(entry["path"])
        relative_text = relative.as_posix()
        if relative_text in inventoried:
            fail(f"Duplicate inventory path: {relative_text}")
        inventoried.add(relative_text)
        source = validate_source_reference(entry["source"], relative_text)
        license_spdx = validate_license_expression(entry["licenseSpdx"], allowed_licenses)
        role = entry["role"]
        if role not in {"BRIDGE", "LIBVLC", "CORE", "PLUGIN", "DEPENDENCY", "DATA", "LEGAL"}:
            fail(f"Unknown file role for {relative_text}")
        roles.add(role)
        linkage = entry["linkage"]
        expected_linkage = "NONE" if role in {"DATA", "LEGAL"} else "DYNAMIC"
        if linkage != expected_linkage:
            fail(f"Unsafe linkage {linkage!r} for {relative_text}; expected {expected_linkage}")
        if role == "PLUGIN" and relative.parts[1:2] and relative.parts[1] in forbidden_families:
            fail(f"Forbidden plugin family in payload: {relative_text}")
        source_path = staging.joinpath(*relative.parts)
        current_path = staging
        unsafe_link = False
        for part in relative.parts:
            current_path /= part
            if current_path.is_symlink():
                unsafe_link = True
                break
        if unsafe_link or not source_path.is_file():
            fail(f"Inventoried runtime file is missing or unsafe: {relative_text}")
        try:
            source_path.resolve(strict=True).relative_to(staging)
        except (OSError, ValueError):
            fail(f"Inventoried runtime file escapes staging: {relative_text}")
        validated.append(
            {
                **entry,
                "path": relative_text,
                "size": source_path.stat().st_size,
                "sha256": sha256(source_path),
            }
        )
    if not REQUIRED_ROLES.issubset(roles):
        fail(f"Runtime role inventory is incomplete: {sorted(roles)}")

    excluded_inventory = inventory_path.resolve() if inventory_path is not None else None
    actual = {
        path.relative_to(staging).as_posix()
        for path in staging.rglob("*")
        if path.is_file()
        and (excluded_inventory is None or path.resolve() != excluded_inventory)
    }
    if actual != inventoried:
        missing = sorted(inventoried - actual)
        extra = sorted(actual - inventoried)
        fail(f"Runtime inventory mismatch; missing={missing}, extra={extra}")
    return validated


def manifest_text(
    inventory: dict,
    files: list[dict],
    runtime_id: str,
    source_offer: str,
    recipe_revision: str,
) -> str:
    bridge = next(entry["path"] for entry in files if entry["role"] == "BRIDGE")
    libvlc = next(entry["path"] for entry in files if entry["role"] == "LIBVLC")
    lines = [
        "schemaVersion=1",
        f"target={inventory['target']}",
        "releaseEligible=true",
        "stockNightly=false",
        "gplComponents=false",
        "nonfreeComponents=false",
        "libvlc.abiMajor=4",
        f"libvlc.version={PINNED_VERSION}",
        f"libvlc.revision={PINNED_REVISION}",
        f"bridge.abiVersion={BRIDGE_ABI_VERSION}",
        f"runtimeId={runtime_id}",
        f"recipeRevision={recipe_revision}",
        f"sourceOffer={source_offer}",
        f"frameDeliveryModes={','.join(inventory['frameDeliveryModes'])}",
        f"renderEngines={','.join(inventory['renderEngines'])}",
        f"hdr10Metadata={str(bool(inventory.get('hdr10Metadata', False))).lower()}",
        f"bridge.path={bridge}",
        f"libvlc.path={libvlc}",
        f"plugins.path={inventory['pluginDirectory']}",
        f"file.count={len(files)}",
    ]
    for index, entry in enumerate(files):
        prefix = f"file.{index}."
        lines.extend(
            [
                f"{prefix}path={entry['path']}",
                f"{prefix}size={entry['size']}",
                f"{prefix}sha256={entry['sha256']}",
                f"{prefix}component={entry['component']}",
                f"{prefix}licenseSpdx={entry['licenseSpdx']}",
                f"{prefix}role={entry['role']}",
                f"{prefix}source={entry['source']}",
                f"{prefix}linkage={entry['linkage']}",
            ]
        )
    return "\n".join(lines) + "\n"


def package(
    root: Path,
    staging: Path,
    inventory_path: Path,
    target: str,
    source_offer: str,
    recipe_revision: str,
    output: Path,
) -> str:
    root = root.resolve(strict=True)
    staging = staging.resolve(strict=True)
    inventory_path = inventory_path.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        fail("Output directory must not already exist.")
    if target not in ALLOWED_TARGETS:
        fail(f"Unsupported native target: {target}")
    policy = load_policy(root)
    inventory = load_inventory(inventory_path)
    require_approved_platform_review(root, target)
    source_offer = validate_source_reference(source_offer, "release source offer")
    recipe_revision = validate_recipe_revision(recipe_revision)
    files = validate_inventory(inventory, policy, target, staging, inventory_path)
    digest_input = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    runtime_id = "kmediavlc4-" + hashlib.sha256(digest_input).hexdigest()[:16]
    destination = output / "META-INF" / "kmediavlc" / "native" / target
    destination.mkdir(parents=True)
    for entry in files:
        relative = PurePosixPath(entry["path"])
        source = staging.joinpath(*relative.parts)
        packaged_file = destination.joinpath(*relative.parts)
        packaged_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, packaged_file)
    manifest_path = destination / "manifest.properties"
    with manifest_path.open("w", encoding="iso-8859-1", newline="\n") as handle:
        handle.write(manifest_text(inventory, files, runtime_id, source_offer, recipe_revision))
    print(f"Packaged verified KMediaVlc runtime {runtime_id} for {target}")
    return runtime_id


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--target", choices=sorted(ALLOWED_TARGETS), required=True)
    parser.add_argument("--source-offer", required=True)
    parser.add_argument("--recipe-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package(
        args.root,
        args.staging,
        args.inventory,
        args.target,
        args.source_offer,
        args.recipe_revision,
        args.output,
    )


if __name__ == "__main__":
    main()
