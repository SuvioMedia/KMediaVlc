#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
from pathlib import Path


STATUS = "automatic-forbidden-license-scan-passed"


def fail(message: str) -> None:
    raise ValueError(message)


def load_scanner(root: Path):
    path = root / "scripts/verify_fast_release_licenses.py"
    specification = importlib.util.spec_from_file_location("kmediavlc_fast_license_scan", path)
    if specification is None or specification.loader is None:
        fail("Cannot load the automatic release license scanner.")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_ndk_verifier(root: Path):
    path = root / "scripts/verify_android_ndk_source_archive.py"
    specification = importlib.util.spec_from_file_location("kmediavlc_ndk_source_verifier", path)
    if specification is None or specification.loader is None:
        fail("Cannot load the Android NDK source verifier.")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def promote(
    root: Path,
    payload: Path,
    output: Path,
    *,
    ndk_source_archive: Path | None = None,
    llvm_project: Path | None = None,
    llvm_android: Path | None = None,
    version: str | None = None,
    tested_commit: str | None = None,
) -> None:
    root = root.resolve(strict=True)
    payload = payload.resolve(strict=True)
    output = output.absolute()
    if payload.is_symlink() or not payload.is_dir():
        fail("Android candidate payload must be a real directory.")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        fail("Promoted Android payload output must be a new directory.")
    for path in payload.rglob("*"):
        if path.is_symlink():
            fail("Android candidate payload contains a symbolic path.")

    scanner = load_scanner(root)
    scanner.verify(root, [payload], [])
    source_arguments = (ndk_source_archive, llvm_project, llvm_android, version, tested_commit)
    source_verified = all(value is not None for value in source_arguments)
    if any(value is not None for value in source_arguments) and not source_verified:
        fail("Android NDK source verification inputs must be supplied together.")
    if source_verified:
        load_ndk_verifier(root).verify(
            root,
            ndk_source_archive,
            llvm_project,
            llvm_android,
            version,
            tested_commit,
        )

    runtime_path = payload / "android-runtime.properties"
    legal_path = payload / "legal/android-static-legal.json"
    runtime = runtime_path.read_text(encoding="ascii")
    if runtime.count("releaseEligible=false") != 1 or "releaseEligible=true" in runtime:
        fail("Android candidate runtime eligibility state is invalid.")
    legal = json.loads(legal_path.read_text(encoding="utf-8"))
    if (
        legal.get("reviewStatus") != "candidate-linked-member-review-pending"
        or legal.get("effectiveLicenseSpdx") is not None
    ):
        fail("Android candidate legal state is not eligible for automatic scanning.")
    inventory = legal.get("candidateLicenseInventorySpdx")
    components = legal.get("components")
    if not isinstance(inventory, list) or not inventory or not isinstance(components, list):
        fail("Android candidate license inventory is incomplete.")
    if any(component.get("licenseReviewStatus") != "pending-linked-member-review" for component in components):
        fail("Android candidate component state is inconsistent.")

    shutil.copytree(payload, output)
    (output / "android-runtime.properties").write_text(
        runtime.replace("releaseEligible=false", "releaseEligible=true"),
        encoding="ascii",
    )
    legal["reviewStatus"] = STATUS
    legal["effectiveLicenseSpdx"] = " AND ".join(inventory)
    legal["automaticLicenseScan"] = {
        "forbiddenPrefixes": list(scanner.FORBIDDEN_PREFIXES),
        "result": "passed",
        "scanner": "scripts/verify_fast_release_licenses.py",
    }
    for component in components:
        component["licenseReviewStatus"] = STATUS
        if component.get("id") == "android-ndk-llvm-runtime" and source_verified:
            component["sourceStatus"] = "corresponding-source-mapped"
    (output / "legal/android-static-legal.json").write_text(
        json.dumps(legal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    scanner.verify(root, [output], [])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ndk-source-archive", type=Path, required=True)
    parser.add_argument("--llvm-project", type=Path, required=True)
    parser.add_argument("--llvm-android", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tested-commit", required=True)
    arguments = parser.parse_args()
    promote(
        arguments.root,
        arguments.payload,
        arguments.output,
        ndk_source_archive=arguments.ndk_source_archive,
        llvm_project=arguments.llvm_project,
        llvm_android=arguments.llvm_android,
        version=arguments.version,
        tested_commit=arguments.tested_commit,
    )
    print(f"Promoted Android payload after automatic forbidden-license scan: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
