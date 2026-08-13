#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Validate and package the closed KMediaVlc Maven Central coordinate."""

from __future__ import annotations

import argparse
import hashlib
import re
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath


GROUP = Path("io/github/shusek")
ARTIFACTS = {
    "kmedia-vlc-runtime-android": {
        "primaryExtension": "aar",
        "classifiers": [
            ("sources", "jar"),
            ("javadoc", "jar"),
            ("corresponding-source", "tar.gz"),
            ("android-ndk-source", "tar.gz"),
        ],
    },
    "kmedia-vlc-runtime-desktop": {
        "primaryExtension": "jar",
        "classifiers": [
            ("sources", "jar"),
            ("javadoc", "jar"),
            ("corresponding-source", "tar.gz"),
        ],
    },
    "kmedia-vlc-runtime-ios": {
        "primaryExtension": "zip",
        "classifiers": [
            ("sources", "jar"),
            ("javadoc", "jar"),
            ("corresponding-source", "tar.gz"),
        ],
    },
}
ARTIFACT_SETS = {
    "multiplatform": (
        "kmedia-vlc-runtime-android",
        "kmedia-vlc-runtime-desktop",
    ),
    "ios": ("kmedia-vlc-runtime-ios",),
}
GENERATED_CHECKSUM_SUFFIXES = (".md5", ".sha1", ".sha256", ".sha512")
SEMVER_NUMBER = r"(?:0|[1-9][0-9]*)"
SEMVER_PRERELEASE_IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
SEMVER = re.compile(
    rf"{SEMVER_NUMBER}\.{SEMVER_NUMBER}\.{SEMVER_NUMBER}"
    rf"(?:-{SEMVER_PRERELEASE_IDENTIFIER}(?:\.{SEMVER_PRERELEASE_IDENTIFIER})*)?"
)


def validate_version(version: str) -> None:
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        raise ValueError("version must be immutable non-SNAPSHOT SemVer")


def digest(path: Path, algorithm: str) -> str:
    value = hashlib.new(algorithm)
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def ensure_real_staging(staging: Path) -> None:
    if not staging.is_dir() or staging.is_symlink():
        raise ValueError("staging must be a real directory")
    if any(path.is_symlink() for path in staging.rglob("*")):
        raise ValueError("staging must not contain symbolic links")


def selected_artifacts(artifact_set: str) -> tuple[str, ...]:
    try:
        return ARTIFACT_SETS[artifact_set]
    except KeyError as error:
        raise ValueError(f"unsupported Maven artifact set: {artifact_set}") from error


def required_files(
    staging: Path,
    version: str,
    artifact_set: str = "multiplatform",
) -> list[Path]:
    validate_version(version)
    ensure_real_staging(staging)
    expected: list[Path] = []
    for artifact in selected_artifacts(artifact_set):
        contract = ARTIFACTS[artifact]
        directory = staging / GROUP / artifact / version
        prefix = f"{artifact}-{version}"
        expected.extend(
            [
                directory / f"{prefix}.{contract['primaryExtension']}",
                directory / f"{prefix}.pom",
                *(
                    directory / f"{prefix}-{classifier}.{extension}"
                    for classifier, extension in contract["classifiers"]
                ),
            ]
        )
    for path in expected:
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Maven staging omits a real required artifact: {path}")
    return sorted(expected)


def base_files(
    staging: Path,
    version: str,
    artifact_set: str = "multiplatform",
) -> list[Path]:
    expected = required_files(staging, version, artifact_set)
    actual = {
        path
        for path in staging.rglob("*")
        if path.is_file()
        and not path.name.endswith((".asc", ".md5", ".sha1"))
    }
    if actual != set(expected):
        raise ValueError("Maven staging inventory differs from the closed KMediaVlc contract")
    return expected


def normalize(arguments: argparse.Namespace) -> None:
    """Remove only Gradle metadata and generated checksum sidecars."""
    staging = arguments.staging.absolute()
    artifact_set = getattr(arguments, "artifact_set", "multiplatform")
    bases = required_files(staging, arguments.version, artifact_set)
    generated: set[Path] = set()
    artifact_roots = []
    for artifact in selected_artifacts(artifact_set):
        artifact_root = staging / GROUP / artifact
        artifact_roots.append(artifact_root)
        directory = artifact_root / arguments.version
        prefix = f"{artifact}-{arguments.version}"
        generated.update(
            {
                directory / f"{prefix}.module",
                artifact_root / "maven-metadata.xml",
            }
        )
    for base in (*bases, *tuple(generated)):
        if any(base.is_relative_to(artifact_root) for artifact_root in artifact_roots):
            for suffix in GENERATED_CHECKSUM_SUFFIXES:
                generated.add(base.with_name(base.name + suffix))
    for path in sorted(generated):
        if path.is_symlink():
            raise ValueError(f"refusing to normalize a generated symlink: {path}")
        if path.is_file():
            path.unlink()
    base_files(staging, arguments.version, artifact_set)


def checksums(arguments: argparse.Namespace) -> None:
    staging = arguments.staging.absolute()
    artifact_set = getattr(arguments, "artifact_set", "multiplatform")
    for path in base_files(staging, arguments.version, artifact_set):
        for algorithm in ("md5", "sha1"):
            sidecar = path.with_name(f"{path.name}.{algorithm}")
            with sidecar.open("w", encoding="ascii", newline="\n") as handle:
                handle.write(digest(path, algorithm) + "\n")


def package(arguments: argparse.Namespace) -> None:
    staging = arguments.staging.absolute()
    artifact_set = getattr(arguments, "artifact_set", "multiplatform")
    bases = base_files(staging, arguments.version, artifact_set)
    expected = set(bases)
    for base in bases:
        for suffix in (".asc", ".md5", ".sha1"):
            sidecar = base.with_name(base.name + suffix)
            if sidecar.is_symlink() or not sidecar.is_file() or sidecar.stat().st_size == 0:
                raise ValueError(f"Maven Central sidecar is missing: {sidecar}")
            expected.add(sidecar)
    actual = {path for path in staging.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError("signed Maven staging inventory is not closed")
    if arguments.output.is_symlink():
        raise ValueError("bundle output already exists")
    output = arguments.output.absolute()
    if output.exists():
        raise ValueError("bundle output already exists")
    date = time.gmtime(max(arguments.epoch, 315532800))[:6]
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(expected):
            relative = PurePosixPath(path.relative_to(staging).as_posix())
            info = zipfile.ZipInfo(relative.as_posix(), date_time=date)
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    normalizer = commands.add_parser("normalize")
    normalizer.add_argument("--staging", type=Path, required=True)
    normalizer.add_argument("--version", required=True)
    normalizer.add_argument(
        "--artifact-set", choices=sorted(ARTIFACT_SETS), default="multiplatform"
    )
    normalizer.set_defaults(function=normalize)

    checksum = commands.add_parser("checksums")
    checksum.add_argument("--staging", type=Path, required=True)
    checksum.add_argument("--version", required=True)
    checksum.add_argument(
        "--artifact-set", choices=sorted(ARTIFACT_SETS), default="multiplatform"
    )
    checksum.set_defaults(function=checksums)

    pack = commands.add_parser("package")
    pack.add_argument("--staging", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--version", required=True)
    pack.add_argument("--epoch", type=int, required=True)
    pack.add_argument(
        "--artifact-set", choices=sorted(ARTIFACT_SETS), default="multiplatform"
    )
    pack.set_defaults(function=package)

    arguments = parser.parse_args()
    arguments.function(arguments)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
