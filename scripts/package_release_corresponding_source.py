#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


VLC_REVISION = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
COMMIT = re.compile(r"[0-9a-f]{40}")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
ROOT = PurePosixPath("corresponding-source")
MANIFEST = ROOT / "SOURCE-MANIFEST.json"
CHECKSUMS = ROOT / "SELECTED-CONTRIB-SHA256SUMS"
POLICIES = (
    "compliance/policy/windows-x86_64-binary-components.json",
    "compliance/policy/linux-binary-components.json",
    "compliance/policy/macos-aarch64-binary-components.json",
)
IOS_POLICIES = ("compliance/policy/ios-binary-components.json",)


def fail(message: str) -> None:
    raise ValueError(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def digest_stream(source: object) -> str:
    value = hashlib.sha256()
    for block in iter(lambda: source.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def real_repository(repository: Path, revision: str, expected: str | None = None) -> Path:
    repository = repository.resolve(strict=True)
    if repository.is_symlink() or not repository.is_dir() or not COMMIT.fullmatch(revision):
        fail("Corresponding-source Git input is invalid.")
    actual = git(repository, "rev-parse", f"{revision}^{{commit}}")
    if actual != revision or (expected is not None and actual != expected):
        fail("Corresponding-source Git input has the wrong revision.")
    return repository


def git_archive(repository: Path, revision: str, target: Path) -> None:
    with target.open("xb") as output:
        subprocess.run(
            ["git", "-C", str(repository), "archive", "--format=tar", revision],
            check=True,
            stdout=output,
        )


def safe_git_members(archive: tarfile.TarFile, prefix: PurePosixPath) -> dict[PurePosixPath, tarfile.TarInfo]:
    values: dict[PurePosixPath, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if not name or name.startswith("/") or "\\" in name or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            fail(f"Git archive contains an unsafe path: {member.name!r}")
        if member.isdir():
            continue
        if not member.isfile() or member.issym() or member.islnk() or member.size < 0:
            fail(f"Git archive contains a link or special file: {member.name!r}")
        destination = prefix / path
        if destination in values:
            fail(f"Git archive contains a duplicate file: {destination}")
        values[destination] = member
    if not values:
        fail("Git archive is empty.")
    return values


def required_archives(root: Path, policies: tuple[str, ...] = POLICIES) -> list[str]:
    names: set[str] = set()
    for relative in policies:
        policy = json.loads((root / relative).read_text(encoding="utf-8"))
        components = policy.get("components")
        if not isinstance(components, dict) or not components:
            fail(f"Source policy is incomplete: {relative}")
        for component in components.values():
            name = component.get("sourceArchive") if isinstance(component, dict) else None
            if not isinstance(name, str) or PurePosixPath(name).name != name:
                fail(f"Source policy has an unsafe source archive: {relative}")
            names.add(name)
    return sorted(names)


def source_inputs(directories: list[Path], required: list[str]) -> dict[str, Path]:
    copies: dict[str, list[Path]] = {}
    for raw in directories:
        directory = raw.resolve(strict=True)
        if raw.is_symlink() or not directory.is_dir():
            fail("Contrib source input must be a real directory.")
        for path in directory.iterdir():
            if path.name == "SHA256SUMS":
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                fail(f"Contrib source input is unsafe: {path}")
            copies.setdefault(path.name, []).append(path)
    selected: dict[str, Path] = {}
    for name in required:
        candidates = copies.get(name, [])
        if not candidates:
            fail(f"Corresponding source omits {name}.")
        hashes = {digest(path) for path in candidates}
        if len(hashes) != 1:
            fail(f"Corresponding source inputs disagree for {name}.")
        selected[name] = candidates[0]
    return selected


def tar_info(path: PurePosixPath, epoch: int, *, directory: bool = False, size: int = 0, executable: bool = False) -> tarfile.TarInfo:
    info = tarfile.TarInfo(path.as_posix() + ("/" if directory else ""))
    info.mtime = max(epoch, 315532800)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o755 if directory or executable else 0o644
    info.size = 0 if directory else size
    if directory:
        info.type = tarfile.DIRTYPE
    return info


def package(
    root: Path,
    vlc: Path,
    contrib_directories: list[Path],
    output: Path,
    version: str,
    release_commit: str,
    runtime_commit: str,
    epoch: int,
    target: str = "desktop-matrix",
) -> str:
    root = real_repository(root, release_commit)
    vlc = real_repository(vlc, VLC_REVISION, VLC_REVISION)
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Corresponding source requires immutable non-SNAPSHOT SemVer.")
    if (
        not COMMIT.fullmatch(runtime_commit)
        or git(root, "rev-parse", f"{runtime_commit}^{{commit}}") != runtime_commit
        or subprocess.run(
            ["git", "-C", str(root), "merge-base", "--is-ancestor", runtime_commit, release_commit]
        ).returncode != 0
    ):
        fail("Runtime commit is not an ancestor of the release commit.")
    output = output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        fail("Corresponding-source output must be a new file.")
    if target == "desktop-matrix":
        policies = POLICIES
        targets = [
            "linux-aarch64",
            "linux-x86_64",
            "macos-aarch64",
            "windows-x86_64",
        ]
    elif target == "ios":
        policies = IOS_POLICIES
        targets = ["ios-arm64", "ios-simulator-arm64"]
    else:
        fail(f"Unsupported corresponding-source target: {target}")
    required = required_archives(root, policies)
    contrib = source_inputs(contrib_directories, required)
    selected_hashes = {name: digest(path) for name, path in contrib.items()}

    with tempfile.TemporaryDirectory(prefix="kmediavlc-release-source-") as temporary:
        temporary_root = Path(temporary)
        kmedia_tar = temporary_root / "kmediavlc.tar"
        vlc_tar = temporary_root / "vlc.tar"
        git_archive(root, release_commit, kmedia_tar)
        git_archive(vlc, VLC_REVISION, vlc_tar)
        with tarfile.open(kmedia_tar, "r:") as kmedia_source, tarfile.open(vlc_tar, "r:") as vlc_source:
            kmedia_members = safe_git_members(kmedia_source, ROOT / "kmediavlc")
            vlc_members = safe_git_members(vlc_source, ROOT / "vlc")
            manifest = {
                "schemaVersion": 1,
                "target": target,
                "releaseVersion": version,
                "releaseCommit": release_commit,
                "runtimeCommit": runtime_commit,
                "vlcRevision": VLC_REVISION,
                "targets": targets,
                "kmediaVlcFileCount": len(kmedia_members),
                "vlcFileCount": len(vlc_members),
                "selectedContribSha256": selected_hashes,
            }
            generated = {
                MANIFEST: (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
                CHECKSUMS: "".join(
                    f"{selected_hashes[name]}  {name}\n" for name in required
                ).encode("ascii"),
            }
            contrib_members = {
                ROOT / "contrib-tarballs" / name: path for name, path in contrib.items()
            }
            files = set(kmedia_members) | set(vlc_members) | set(contrib_members) | set(generated)
            directories = {ROOT}
            for path in files:
                directories.update(parent for parent in path.parents if parent.parts)

            with output.open("xb") as raw:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, compresslevel=9,
                    mtime=max(epoch, 315532800),
                ) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as target:
                        for directory in sorted(directories, key=lambda item: (len(item.parts), item.as_posix())):
                            target.addfile(tar_info(directory, epoch, directory=True))
                        for path in sorted(kmedia_members):
                            member = kmedia_members[path]
                            stream = kmedia_source.extractfile(member)
                            if stream is None:
                                fail(f"Cannot read KMediaVlc source member: {path}")
                            with stream:
                                target.addfile(
                                    tar_info(path, epoch, size=member.size, executable=bool(member.mode & 0o111)),
                                    stream,
                                )
                        for path in sorted(vlc_members):
                            member = vlc_members[path]
                            stream = vlc_source.extractfile(member)
                            if stream is None:
                                fail(f"Cannot read VLC source member: {path}")
                            with stream:
                                target.addfile(
                                    tar_info(path, epoch, size=member.size, executable=bool(member.mode & 0o111)),
                                    stream,
                                )
                        for path in sorted(contrib_members):
                            source_path = contrib_members[path]
                            with source_path.open("rb") as stream:
                                target.addfile(tar_info(path, epoch, size=source_path.stat().st_size), stream)
                        for path in sorted(generated):
                            data = generated[path]
                            target.addfile(tar_info(path, epoch, size=len(data)), io.BytesIO(data))
    return digest(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vlc", type=Path, required=True)
    parser.add_argument("--contrib", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--release-commit", required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument(
        "--target",
        choices=("desktop-matrix", "ios"),
        default="desktop-matrix",
    )
    arguments = parser.parse_args()
    value = package(
        arguments.root,
        arguments.vlc,
        arguments.contrib,
        arguments.output,
        arguments.version,
        arguments.release_commit,
        arguments.runtime_commit,
        arguments.epoch,
        arguments.target,
    )
    print(f"{value}  {arguments.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
