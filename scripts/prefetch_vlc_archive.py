#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


SHA512 = re.compile(r"[0-9a-f]{128}")
ARCHIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


def fail(message: str) -> None:
    raise ValueError(message)


def expected_digest(manifest: Path, archive: str) -> str:
    if manifest.is_symlink() or not manifest.is_file():
        fail("VLC checksum manifest must be a real file.")
    matches: list[str] = []
    for line in manifest.read_text(encoding="ascii").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        digest, filename = fields
        if filename.removeprefix("*") == archive:
            matches.append(digest)
    if len(matches) != 1 or not SHA512.fullmatch(matches[0]):
        fail(f"VLC checksum manifest does not bind exactly one SHA-512 for {archive}.")
    return matches[0]


def sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        fail("VLC archive mirrors must be credential-free HTTPS URLs without query data.")
    return value


def download(url: str, output: Path) -> bool:
    result = subprocess.run(
        [
            "curl",
            "--fail",
            "--location",
            "--retry",
            "3",
            "--retry-all-errors",
            "--connect-timeout",
            "30",
            "--max-time",
            "300",
            "--silent",
            "--show-error",
            "--output",
            str(output),
            "--",
            url,
        ],
        check=False,
    )
    return result.returncode == 0


def prefetch(
    *, manifest: Path, archive: str, destination_directory: Path, urls: list[str],
    bundle_url: str | None = None, bundle_member: str | None = None,
) -> Path:
    if not ARCHIVE.fullmatch(archive) or Path(archive).name != archive:
        fail("VLC archive name must be one safe basename.")
    if (
        destination_directory.is_symlink()
        or not destination_directory.is_dir()
        or not (urls or bundle_url)
    ):
        fail("VLC archive destination must be a real directory with at least one mirror.")
    if shutil.which("curl") is None:
        fail("curl is required to prefetch a VLC source archive.")

    destination_directory = destination_directory.resolve(strict=True)
    expected = expected_digest(manifest.resolve(strict=True), archive)
    mirrors = [validate_url(url) for url in urls]
    if (bundle_url is None) != (bundle_member is None):
        fail("A source bundle requires both its HTTPS URL and exact member path.")
    if bundle_url is not None:
        validate_url(bundle_url)
        member_path = PurePosixPath(bundle_member)
        if (
            member_path.is_absolute() or ".." in member_path.parts
            or member_path.as_posix() != bundle_member
            or member_path.name != archive
        ):
            fail("Source bundle member must be a canonical relative archive path.")
    destination = destination_directory / archive
    if destination.is_symlink():
        fail(f"Existing VLC archive is a symlink: {archive}.")
    if destination.exists():
        if not destination.is_file() or sha512(destination) != expected:
            fail(f"Existing VLC archive does not match its pinned SHA-512: {archive}.")
        print(f"Using checksum-verified VLC archive: {archive}")
        return destination

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive}.", suffix=".download", dir=destination_directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        for mirror in mirrors:
            temporary.write_bytes(b"")
            if not download(mirror, temporary):
                continue
            if sha512(temporary) != expected:
                fail(f"Downloaded VLC archive failed its pinned SHA-512: {archive}.")
            temporary.chmod(0o644)
            os.replace(temporary, destination)
            print(f"Prefetched checksum-verified VLC archive: {archive}")
            return destination
        if bundle_url is not None:
            # The original VLC SHA-512 binds the inner archive, even if the
            # surrounding source bundle or its hosting service changes.
            with tempfile.TemporaryDirectory(prefix=".vlc-source-", dir=destination_directory) as work:
                bundle = Path(work) / "corresponding-source.tar.gz"
                if not download(bundle_url, bundle):
                    fail(f"Source bundle download failed for VLC archive: {archive}.")
                extract_bundle_member(bundle, bundle_member, temporary)
            if sha512(temporary) != expected:
                fail(f"Bundled VLC archive failed its pinned SHA-512: {archive}.")
            temporary.chmod(0o644)
            os.replace(temporary, destination)
            print(f"Recovered checksum-verified VLC archive from source bundle: {archive}")
            return destination
        fail(f"Every HTTPS mirror failed for VLC archive: {archive}.")
    finally:
        temporary.unlink(missing_ok=True)


def extract_bundle_member(bundle: Path, member_name: str, output: Path) -> None:
    matches = 0
    with tarfile.open(bundle, mode="r|gz") as source:
        for member in source:
            if member.name != member_name:
                continue
            matches += 1
            if matches != 1 or not member.isfile() or member.size > 256 * 1024 * 1024:
                fail("Source bundle archive member must be one bounded regular file.")
            reader = source.extractfile(member)
            if reader is None:
                fail("Source bundle archive member is unreadable.")
            # Never extract paths, symlinks or permissions from the outer bundle.
            with reader, output.open("wb") as target:
                shutil.copyfileobj(reader, target, length=1024 * 1024)
    if matches != 1:
        fail("Source bundle does not contain the exact pinned archive member.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prefetch one pinned VLC archive through checksum-verified mirrors."
    )
    parser.add_argument("--checksum-manifest", type=Path, required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--destination-directory", type=Path, required=True)
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--source-bundle-url")
    parser.add_argument("--source-bundle-member")
    arguments = parser.parse_args()
    try:
        prefetch(
            manifest=arguments.checksum_manifest,
            archive=arguments.archive,
            destination_directory=arguments.destination_directory,
            urls=arguments.url,
            bundle_url=arguments.source_bundle_url,
            bundle_member=arguments.source_bundle_member,
        )
    except (OSError, UnicodeError, ValueError, tarfile.TarError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
