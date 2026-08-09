#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Prune and deterministically package the reviewed corresponding source."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath


PINNED_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
COMMIT = re.compile(r"[0-9a-f]{40}")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
ROOT = PurePosixPath("corresponding-source")
MAX_MEMBERS = 200_000
MAX_FILE_SIZE = 900 * 1024 * 1024


def fail(message: str) -> None:
    raise ValueError(message)


def digest_stream(stream: object) -> str:
    value = hashlib.sha256()
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            return value.hexdigest()
        value.update(chunk)


def safe_member(member: tarfile.TarInfo) -> PurePosixPath:
    name = member.name.rstrip("/")
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or not path.is_relative_to(ROOT)
    ):
        fail(f"Unsafe corresponding-source member: {member.name!r}")
    if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
        fail(f"Corresponding source contains a link or special file: {member.name!r}")
    if member.size < 0 or member.size > MAX_FILE_SIZE:
        fail(f"Corresponding-source member is oversized: {member.name!r}")
    return path


def load_policy(root: Path, allow_audit_candidate: bool) -> tuple[dict, list[str]]:
    policy_path = root / "compliance/policy/windows-x86_64-binary-components.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    if (
        policy.get("schemaVersion") != 1
        or policy.get("target") != "windows-x86_64"
        or policy.get("vlcRevision") != PINNED_REVISION
    ):
        fail("Unsupported Windows binary component policy.")
    if policy.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("Windows binary link inputs have not completed review.")
    components = policy.get("components")
    if not isinstance(components, dict) or not components:
        fail("Windows binary component policy is empty.")
    archives = sorted({component.get("sourceArchive") for component in components.values()})
    if any(not isinstance(name, str) or PurePosixPath(name).name != name for name in archives):
        fail("Windows binary component policy contains an unsafe source archive.")
    return policy, archives


def tar_info(name: PurePosixPath, epoch: int, *, directory: bool = False, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name.as_posix() + ("/" if directory else ""))
    info.mtime = max(epoch, 315532800)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode = 0o755 if directory else 0o644
    info.size = 0 if directory else size
    if directory:
        info.type = tarfile.DIRTYPE
    return info


def package(
    root: Path,
    candidate: Path,
    output: Path,
    tested_commit: str,
    version: str,
    epoch: int,
    allow_audit_candidate: bool = False,
) -> str:
    root = root.resolve(strict=True)
    candidate = candidate.resolve(strict=True)
    output = output.absolute()
    if candidate.is_symlink() or not candidate.is_file():
        fail("Corresponding-source candidate must be a real file.")
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        fail("Corresponding-source output must be a new file in a real directory.")
    if not COMMIT.fullmatch(tested_commit):
        fail("Corresponding source requires an exact tested KMediaVlc commit.")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Corresponding source requires immutable non-SNAPSHOT SemVer.")
    policy, selected_archives = load_policy(root, allow_audit_candidate)
    archive_paths = {
        ROOT / "contrib-tarballs" / archive: archive for archive in selected_archives
    }

    with tarfile.open(candidate, "r:gz") as source:
        members = source.getmembers()
        if not members or len(members) > MAX_MEMBERS:
            fail("Corresponding-source candidate member count is invalid.")
        by_path: dict[PurePosixPath, tarfile.TarInfo] = {}
        for member in members:
            path = safe_member(member)
            if path in by_path:
                fail(f"Duplicate corresponding-source member: {path}")
            by_path[path] = member

        selected: dict[PurePosixPath, tarfile.TarInfo] = {}
        for path, member in by_path.items():
            relative = path.relative_to(ROOT)
            if not relative.parts or member.isdir():
                continue
            if relative.parts[0] in {"kmediavlc", "vlc"}:
                selected[path] = member
            elif path == ROOT / "BUILD-TOOLCHAIN.txt" or path in archive_paths:
                selected[path] = member

        for required in (
            ROOT / "kmediavlc" / "build.gradle.kts",
            ROOT / "vlc" / "meson.build",
            ROOT / "BUILD-TOOLCHAIN.txt",
            *archive_paths,
        ):
            if required not in selected:
                fail(f"Corresponding-source candidate omits required input: {required}")

        archive_hashes: dict[str, str] = {}
        for path, archive in archive_paths.items():
            stream = source.extractfile(selected[path])
            if stream is None:
                fail(f"Could not read selected contrib source: {archive}")
            with stream:
                archive_hashes[archive] = digest_stream(stream)

        manifest = {
            "schemaVersion": 1,
            "target": policy["target"],
            "releaseVersion": version,
            "testedCommit": tested_commit,
            "vlcRevision": policy["vlcRevision"],
            "toolchainImage": policy["toolchainImage"],
            "componentReviewStatus": policy["reviewStatus"],
            "components": policy["components"],
            "selectedContribSha256": archive_hashes,
        }
        generated = {
            ROOT / "SOURCE-MANIFEST.json": (
                json.dumps(manifest, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8"),
            ROOT / "SELECTED-CONTRIB-SHA256SUMS": "".join(
                f"{archive_hashes[name]}  {name}\n" for name in sorted(archive_hashes)
            ).encode("ascii"),
        }
        directories = {ROOT}
        for path in (*selected, *generated):
            directories.update(parent for parent in path.parents if parent.parts)

        with output.open("xb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=max(epoch, 315532800)
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as target:
                    for directory in sorted(directories, key=lambda item: (len(item.parts), item.as_posix())):
                        target.addfile(tar_info(directory, epoch, directory=True))
                    for path in sorted(selected):
                        member = selected[path]
                        stream = source.extractfile(member)
                        if stream is None:
                            fail(f"Could not reopen corresponding-source member: {path}")
                        with stream:
                            target.addfile(tar_info(path, epoch, size=member.size), stream)
                    for path in sorted(generated):
                        data = generated[path]
                        target.addfile(tar_info(path, epoch, size=len(data)), io.BytesIO(data))

    with output.open("rb") as packaged:
        return digest_stream(packaged)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tested-commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    arguments = parser.parse_args()
    sha256 = package(
        arguments.root,
        arguments.candidate,
        arguments.output,
        arguments.tested_commit,
        arguments.version,
        arguments.epoch,
        arguments.allow_audit_candidate,
    )
    print(f"{sha256}  {arguments.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
