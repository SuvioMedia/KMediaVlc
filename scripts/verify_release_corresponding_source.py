#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import importlib.util
import json
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


PACKAGER_PATH = Path(__file__).with_name("package_release_corresponding_source.py")
SPEC = importlib.util.spec_from_file_location("kmediavlc_release_source_packager", PACKAGER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Cannot load release corresponding-source packager.")
PACKAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGER)


def fail(message: str) -> None:
    raise ValueError(message)


def safe_members(archive: tarfile.TarFile) -> dict[PurePosixPath, tarfile.TarInfo]:
    values: dict[PurePosixPath, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if (
            not name
            or name.startswith("/")
            or "\\" in name
            or any(part in {"", ".", ".."} for part in path.parts)
            or not path.is_relative_to(PACKAGER.ROOT)
            or not (member.isfile() or member.isdir())
            or member.issym()
            or member.islnk()
            or path in values
        ):
            fail(f"Release corresponding source contains an unsafe member: {member.name!r}")
        values[path] = member
    return values


def compare_streams(
    expected_archive: tarfile.TarFile,
    expected: tarfile.TarInfo,
    actual_archive: tarfile.TarFile,
    actual: tarfile.TarInfo,
    label: str,
) -> None:
    if expected.size != actual.size:
        fail(f"Release corresponding source size differs: {label}")
    expected_stream = expected_archive.extractfile(expected)
    actual_stream = actual_archive.extractfile(actual)
    if expected_stream is None or actual_stream is None:
        fail(f"Release corresponding source member is unreadable: {label}")
    with expected_stream, actual_stream:
        if PACKAGER.digest_stream(expected_stream) != PACKAGER.digest_stream(actual_stream):
            fail(f"Release corresponding source bytes differ: {label}")


def verify(
    root: Path,
    vlc: Path,
    contrib_directories: list[Path],
    archive_path: Path,
    version: str,
    release_commit: str,
    runtime_commit: str,
) -> str:
    root = PACKAGER.real_repository(root, release_commit)
    vlc = PACKAGER.real_repository(vlc, PACKAGER.VLC_REVISION, PACKAGER.VLC_REVISION)
    archive_path = archive_path.resolve(strict=True)
    if archive_path.is_symlink() or not archive_path.is_file():
        fail("Release corresponding-source archive must be a real file.")
    required = PACKAGER.required_archives(root)
    contrib = PACKAGER.source_inputs(contrib_directories, required)
    selected_hashes = {name: PACKAGER.digest(path) for name, path in contrib.items()}

    with tempfile.TemporaryDirectory(prefix="kmediavlc-verify-release-source-") as temporary:
        temporary_root = Path(temporary)
        kmedia_tar = temporary_root / "kmediavlc.tar"
        vlc_tar = temporary_root / "vlc.tar"
        PACKAGER.git_archive(root, release_commit, kmedia_tar)
        PACKAGER.git_archive(vlc, PACKAGER.VLC_REVISION, vlc_tar)
        with (
            tarfile.open(kmedia_tar, "r:") as kmedia_source,
            tarfile.open(vlc_tar, "r:") as vlc_source,
            tarfile.open(archive_path, "r:gz") as actual_source,
        ):
            kmedia = PACKAGER.safe_git_members(kmedia_source, PACKAGER.ROOT / "kmediavlc")
            vlc_files = PACKAGER.safe_git_members(vlc_source, PACKAGER.ROOT / "vlc")
            actual = safe_members(actual_source)
            file_members = {path: member for path, member in actual.items() if member.isfile()}
            expected_files = (
                set(kmedia)
                | set(vlc_files)
                | {PACKAGER.ROOT / "contrib-tarballs" / name for name in required}
                | {PACKAGER.MANIFEST, PACKAGER.CHECKSUMS}
            )
            if set(file_members) != expected_files:
                fail("Release corresponding-source file inventory is incomplete or has extras.")
            for path, member in kmedia.items():
                compare_streams(kmedia_source, member, actual_source, file_members[path], path.as_posix())
            for path, member in vlc_files.items():
                compare_streams(vlc_source, member, actual_source, file_members[path], path.as_posix())
            for name in required:
                path = PACKAGER.ROOT / "contrib-tarballs" / name
                stream = actual_source.extractfile(file_members[path])
                if stream is None:
                    fail(f"Release corresponding source archive is unreadable: {name}")
                with stream:
                    if PACKAGER.digest_stream(stream) != selected_hashes[name]:
                        fail(f"Release corresponding source archive differs: {name}")
            manifest_stream = actual_source.extractfile(file_members[PACKAGER.MANIFEST])
            checksum_stream = actual_source.extractfile(file_members[PACKAGER.CHECKSUMS])
            if manifest_stream is None or checksum_stream is None:
                fail("Release corresponding-source generated metadata is unreadable.")
            with manifest_stream:
                manifest = json.load(manifest_stream)
            expected_manifest = {
                "schemaVersion": 1,
                "target": "desktop-matrix",
                "releaseVersion": version,
                "releaseCommit": release_commit,
                "runtimeCommit": runtime_commit,
                "vlcRevision": PACKAGER.VLC_REVISION,
                "targets": [
                    "linux-aarch64",
                    "linux-x86_64",
                    "macos-aarch64",
                    "windows-x86_64",
                ],
                "kmediaVlcFileCount": len(kmedia),
                "vlcFileCount": len(vlc_files),
                "selectedContribSha256": selected_hashes,
            }
            if manifest != expected_manifest:
                fail("Release corresponding-source manifest differs from exact inputs.")
            with checksum_stream:
                checksums = checksum_stream.read().decode("ascii")
            expected_checksums = "".join(
                f"{selected_hashes[name]}  {name}\n" for name in required
            )
            if checksums != expected_checksums:
                fail("Release corresponding-source checksum list differs.")
    return PACKAGER.digest(archive_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vlc", type=Path, required=True)
    parser.add_argument("--contrib", type=Path, action="append", required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--runtime-commit", required=True)
    arguments = parser.parse_args()
    value = verify(
        arguments.root,
        arguments.vlc,
        arguments.contrib,
        arguments.archive,
        arguments.version,
        arguments.release_commit,
        arguments.runtime_commit,
    )
    print(f"Verified release corresponding source: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
