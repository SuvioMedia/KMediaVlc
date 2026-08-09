#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath


SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_FILES = 200
MAX_FILE_SIZE = 300 * 1024 * 1024
MAX_TOTAL_SIZE = 600 * 1024 * 1024


def fail(message: str) -> None:
    raise ValueError(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def safe_name(name: str) -> PurePosixPath:
    if name.endswith("/"):
        name = name[:-1]
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        fail(f"Unsafe Windows candidate path: {name!r}")
    return path


def load_inventory(path: Path) -> tuple[dict, set[str]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schemaVersion") != 1 or value.get("target") != "windows-x86_64":
        fail("Windows candidate inventory has an unsupported identity.")
    files = value.get("files")
    if not isinstance(files, list) or len(files) != 96:
        fail("Windows candidate inventory must contain exactly 96 files.")
    names = {entry.get("path") for entry in files if isinstance(entry, dict)}
    if len(names) != 96 or any(not isinstance(name, str) for name in names):
        fail("Windows candidate inventory paths are invalid or duplicated.")
    for name in names:
        if safe_name(name).as_posix() != name:
            fail(f"Windows candidate inventory path is not canonical: {name!r}")
    return value, names


def verify_runtime_checksums(output: Path, expected_names: set[str]) -> None:
    checksum_path = output / "SHA256SUMS"
    lines = checksum_path.read_text(encoding="ascii").splitlines()
    expected_runtime = expected_names - {"SHA256SUMS", "BINARY-COMPONENTS.json"}
    parsed: dict[str, str] = {}
    for line in lines:
        if len(line) < 67 or line[64:66] != "  ":
            fail("Windows runtime checksum line is malformed.")
        checksum, name = line[:64], line[66:]
        if not SHA256.fullmatch(checksum) or name in parsed or safe_name(name).as_posix() != name:
            fail("Windows runtime checksum entry is invalid or duplicated.")
        parsed[name] = checksum
    if set(parsed) != expected_runtime:
        fail("Windows runtime checksum inventory is incomplete or overbroad.")
    for name, checksum in parsed.items():
        if digest(output.joinpath(*PurePosixPath(name).parts)) != checksum:
            fail(f"Windows runtime checksum mismatch: {name}")


def verify_component_audit(output: Path, expected_names: set[str], allow_audit_candidate: bool) -> None:
    audit = json.loads((output / "BINARY-COMPONENTS.json").read_text(encoding="utf-8"))
    if audit.get("schemaVersion") != 1 or audit.get("target") != "windows-x86_64":
        fail("Windows binary component audit has an unsupported identity.")
    if not allow_audit_candidate and (
        audit.get("playbackReviewStatus") != "approved"
        or audit.get("binaryReviewStatus") != "approved"
    ):
        fail("Windows binary component audit has not completed review.")
    files = audit.get("runtimeFiles")
    expected_audited = expected_names - {"BINARY-COMPONENTS.json"}
    if not isinstance(files, list) or len(files) != len(expected_audited):
        fail("Windows binary component audit file count is invalid.")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            fail("Windows binary component audit entry is invalid.")
        name = entry.get("path")
        checksum = entry.get("sha256")
        size = entry.get("size")
        if (
            not isinstance(name, str)
            or not isinstance(checksum, str)
            or name in seen
            or name not in expected_audited
            or not SHA256.fullmatch(checksum)
        ):
            fail("Windows binary component audit path or hash is invalid.")
        path = output.joinpath(*PurePosixPath(name).parts)
        if not isinstance(size, int) or size != path.stat().st_size or checksum != digest(path):
            fail(f"Windows binary component audit mismatch: {name}")
        seen.add(name)
    if seen != expected_audited:
        fail("Windows binary component audit is incomplete.")


def extract(
    archive: Path,
    inventory_path: Path,
    output: Path,
    allow_audit_candidate: bool = False,
) -> None:
    archive = archive.resolve(strict=True)
    inventory_path = inventory_path.resolve(strict=True)
    output = output.absolute()
    if archive.is_symlink() or not archive.is_file():
        fail("Windows candidate archive must be a real file.")
    if inventory_path.is_symlink() or not inventory_path.is_file():
        fail("Windows candidate inventory must be a real file.")
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        fail("Windows candidate output must be a new directory below a real parent.")
    _, expected_names = load_inventory(inventory_path)

    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if not members or len(members) > MAX_FILES:
            fail("Windows candidate archive member count is invalid.")
        files: dict[str, zipfile.ZipInfo] = {}
        total = 0
        for member in members:
            path = safe_name(member.filename)
            if member.is_dir():
                continue
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode) or member.flag_bits & 1:
                fail(f"Windows candidate contains a link or encrypted file: {path}")
            if member.file_size <= 0 or member.file_size > MAX_FILE_SIZE:
                fail(f"Windows candidate contains an empty or oversized file: {path}")
            name = path.as_posix()
            if name in files:
                fail(f"Windows candidate contains a duplicate file: {name}")
            files[name] = member
            total += member.file_size
        if total > MAX_TOTAL_SIZE or set(files) != expected_names:
            fail("Windows candidate archive differs from its closed inventory.")

        output.mkdir()
        for name in sorted(files):
            destination = output.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.open(files[name], "r") as input_stream, destination.open("xb") as target:
                copied = 0
                while True:
                    block = input_stream.read(1024 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    target.write(block)
            if copied != files[name].file_size:
                fail(f"Windows candidate file was truncated: {name}")

    verify_runtime_checksums(output, expected_names)
    verify_component_audit(output, expected_names, allow_audit_candidate)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    arguments = parser.parse_args()
    extract(
        arguments.archive,
        arguments.inventory,
        arguments.output,
        arguments.allow_audit_candidate,
    )
    print("Extracted and hash-verified the closed Windows candidate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
