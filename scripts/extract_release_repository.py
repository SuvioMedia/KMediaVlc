#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Safely extract the hash-bound Maven repository release asset."""

from __future__ import annotations

import argparse
import shutil
import tarfile
from pathlib import Path, PurePosixPath


MAX_MEMBERS = 128
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


def safe_relative_path(name: str) -> PurePosixPath:
    if not name or "\\" in name or name.startswith("/") or name.endswith("/") or "//" in name:
        raise ValueError(f"unsafe release archive path: {name!r}")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe release archive path: {name!r}")
    if not path.parts or path.parts[0] != "maven":
        raise ValueError("release archive must have exactly one maven/ root")
    return PurePosixPath(*path.parts[1:])


def extract(archive: Path, output: Path) -> None:
    if archive.is_symlink():
        raise ValueError("release archive must be a real file")
    archive = archive.resolve(strict=True)
    if not archive.is_file():
        raise ValueError("release archive must be a real file")
    if output.is_symlink():
        raise ValueError("output must be an empty real directory")
    output = output.resolve(strict=True)
    if not output.is_dir() or any(output.iterdir()):
        raise ValueError("output must be an empty real directory")

    seen: set[PurePosixPath] = set()
    total = 0
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            raise ValueError("release archive member count is invalid")
        for member in members:
            name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
            relative = safe_relative_path(name)
            if not relative.parts:
                if not member.isdir():
                    raise ValueError("maven archive root must be a directory")
                continue
            if relative in seen:
                raise ValueError(f"duplicate release archive path: {relative}")
            seen.add(relative)
            if not member.isdir() and not member.isfile():
                raise ValueError(f"release archive contains a link or special file: {relative}")
            if member.size < 0 or total > MAX_UNCOMPRESSED_BYTES - member.size:
                raise ValueError("release archive is oversized")
            total += member.size

        for member in members:
            name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
            relative = safe_relative_path(name)
            if not relative.parts:
                continue
            destination = output.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                destination.mkdir(exist_ok=False)
                continue
            payload = source.extractfile(member)
            if payload is None:
                raise ValueError(f"release archive file cannot be read: {relative}")
            with destination.open("xb") as target:
                shutil.copyfileobj(payload, target, length=1024 * 1024)
            if destination.stat().st_size != member.size:
                raise ValueError(f"release archive file was truncated: {relative}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    extract(arguments.archive, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
