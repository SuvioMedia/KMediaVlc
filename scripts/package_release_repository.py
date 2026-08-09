#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create the deterministic Maven repository asset used by a GitHub release."""

from __future__ import annotations

import argparse
import gzip
import tarfile
from pathlib import Path, PurePosixPath

import build_maven_central_bundle as central


def tar_info(name: str, epoch: int, directory: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name + ("/" if directory else ""))
    info.mtime = max(epoch, 315532800)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o755 if directory else 0o644
    if directory:
        info.type = tarfile.DIRTYPE
    return info


def package(staging: Path, version: str, epoch: int, output: Path) -> str:
    staging = staging.absolute()
    files = central.base_files(staging, version)
    if output.is_symlink() or output.exists():
        raise ValueError("release repository output already exists")
    output = output.absolute()
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise ValueError("release repository output parent must be a real directory")

    directories = {PurePosixPath("maven")}
    relative_files: list[tuple[Path, PurePosixPath]] = []
    for path in files:
        relative = PurePosixPath("maven") / PurePosixPath(path.relative_to(staging).as_posix())
        relative_files.append((path, relative))
        directories.update(relative.parents)

    with output.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=max(epoch, 315532800)) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for directory in sorted(
                    (path for path in directories if path.parts and path.as_posix() != "."),
                    key=lambda path: (len(path.parts), path.as_posix()),
                ):
                    archive.addfile(tar_info(directory.as_posix(), epoch, directory=True))
                for source, relative in sorted(relative_files, key=lambda item: item[1].as_posix()):
                    info = tar_info(relative.as_posix(), epoch)
                    info.size = source.stat().st_size
                    with source.open("rb") as payload:
                        archive.addfile(info, payload)

    return central.digest(output, "sha256")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    sha256 = package(arguments.staging, arguments.version, arguments.epoch, arguments.output)
    print(f"{sha256}  {arguments.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
