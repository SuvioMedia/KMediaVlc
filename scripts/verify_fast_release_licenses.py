#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path


FORBIDDEN_PREFIXES = ("GPL-", "AGPL-", "LicenseRef-NonFree", "unknown")
SPDX_KEYS = {
    "allowedLicenseSpdx",
    "candidateLicenseInventorySpdx",
    "candidateLicenseSpdx",
    "declaredVlcLicenseSpdx",
    "effectiveLicenseSpdx",
    "licenseSpdx",
    "primaryLicenseSpdx",
}
POLICIES = (
    "compliance/policy/release-policy.json",
    "compliance/policy/windows-x86_64-playback-modules.json",
    "compliance/policy/windows-x86_64-binary-components.json",
    "compliance/policy/linux-playback-modules.json",
    "compliance/policy/linux-binary-components.json",
    "compliance/policy/macos-aarch64-playback-modules.json",
    "compliance/policy/macos-aarch64-binary-components.json",
    "compliance/policy/android-static-components.json",
)


def fail(message: str) -> None:
    raise ValueError(message)


def identifiers(value: object, label: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for key, child in value.items():
            result.extend(identifiers(child, f"{label}.{key}"))
        return result
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    for expression in values:
        if not isinstance(expression, str) or not expression.strip():
            fail(f"Empty or non-text SPDX value at {label}.")
        for item in re.split(r"\s+(?:AND|OR)\s+", expression):
            identifier = item.strip().strip("()")
            if not identifier:
                fail(f"Malformed SPDX expression at {label}.")
            result.append(identifier)
    return result


def scan_json(value: object, label: str, found: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        if value.get("gplComponents") is True:
            fail(f"GPL components are enabled at {label}.")
        if value.get("nonfreeComponents") is True:
            fail(f"Nonfree components are enabled at {label}.")
        for key, child in value.items():
            child_label = f"{label}.{key}"
            if key in SPDX_KEYS:
                found.extend((identifier, child_label) for identifier in identifiers(child, child_label))
            else:
                scan_json(child, child_label, found)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_json(child, f"{label}[{index}]", found)


def scan_properties(text: str, label: str, found: list[tuple[str, str]]) -> None:
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key in {"gplComponents", "nonfreeComponents"} and value != "false":
            fail(f"Forbidden component flag is not false at {label}:{number}.")
        if key.endswith("licenseSpdx"):
            location = f"{label}:{number}"
            found.extend((identifier, location) for identifier in identifiers(value, location))


def scan_file(path: Path, found: list[tuple[str, str]]) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        fail(f"License scan input must be a real non-empty file: {path}")
    if path.suffix == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"Invalid JSON license input: {path}") from error
        scan_json(value, str(path), found)
    elif path.suffix == ".properties":
        scan_properties(path.read_text(encoding="iso-8859-1"), str(path), found)


def scan_archive(path: Path, found: list[tuple[str, str]]) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        fail(f"Release archive must be a real non-empty file: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or not info.filename.endswith((".json", ".properties")):
                    continue
                if info.file_size > 4_000_000:
                    fail(f"License metadata is unexpectedly large: {path}!/{info.filename}")
                data = archive.read(info)
                label = f"{path}!/{info.filename}"
                if info.filename.endswith(".json"):
                    scan_json(json.loads(data.decode("utf-8")), label, found)
                else:
                    scan_properties(data.decode("iso-8859-1"), label, found)
    except (OSError, UnicodeError, ValueError, zipfile.BadZipFile) as error:
        raise ValueError(f"Invalid release archive: {path}") from error


def verify(root: Path, inputs: list[Path], archives: list[Path]) -> int:
    root = root.resolve(strict=True)
    found: list[tuple[str, str]] = []
    for relative in POLICIES:
        scan_file(root / relative, found)
    for raw in inputs:
        path = raw.resolve(strict=True)
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix in {".json", ".properties"}:
                    scan_file(child, found)
        else:
            scan_file(path, found)
    for archive in archives:
        scan_archive(archive.resolve(strict=True), found)
    forbidden = [
        (identifier, label)
        for identifier, label in found
        if identifier.startswith(FORBIDDEN_PREFIXES)
    ]
    if forbidden:
        identifier, label = forbidden[0]
        fail(f"Forbidden license {identifier!r} found at {label}.")
    if len(found) < 20:
        fail("Automatic license scan found too little SPDX evidence.")
    return len(found)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input", type=Path, action="append", default=[])
    parser.add_argument("--archive", type=Path, action="append", default=[])
    arguments = parser.parse_args()
    count = verify(arguments.root, arguments.input, arguments.archive)
    print(
        f"Fast release license scan passed: {count} SPDX references; "
        "no GPL, AGPL, nonfree, or unknown identifier."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
