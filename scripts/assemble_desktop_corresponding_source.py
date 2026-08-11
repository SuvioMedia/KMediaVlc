#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
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
ARCHIVE_ROOT = PurePosixPath("corresponding-source")
MANIFEST_PATH = ARCHIVE_ROOT / "SOURCE-MANIFEST.json"
CHECKSUM_PATH = ARCHIVE_ROOT / "SELECTED-CONTRIB-SHA256SUMS"
TARGETS = ["linux-aarch64", "linux-x86_64", "macos-aarch64", "windows-x86_64"]
POLICIES = {
    "linux": "compliance/policy/linux-binary-components.json",
    "macos-aarch64": "compliance/policy/macos-aarch64-binary-components.json",
    "windows-x86_64": "compliance/policy/windows-x86_64-binary-components.json",
}


def load_module(name: str, path: Path):
    specification = importlib.util.spec_from_file_location(name, path)
    if specification is None or specification.loader is None:
        raise RuntimeError(f"Cannot load source verifier: {path}")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def fail(message: str) -> None:
    raise ValueError(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def digest_stream(stream: object) -> str:
    value = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def load_policies(root: Path) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    policies: dict[str, dict] = {}
    components: dict[str, dict] = {}
    archives: set[str] = set()
    for platform, relative in POLICIES.items():
        policy = json.loads((root / relative).read_text(encoding="utf-8"))
        declared = policy.get("targets") if platform == "linux" else [policy.get("target")]
        expected = ["linux-x86_64", "linux-aarch64"] if platform == "linux" else [platform]
        platform_components = policy.get("components")
        if (
            policy.get("schemaVersion") != 1
            or policy.get("vlcRevision") != PINNED_REVISION
            or declared != expected
            or policy.get("reviewStatus") != "approved"
            or not isinstance(platform_components, dict)
            or not platform_components
        ):
            fail(f"Desktop source policy is not approved: {relative}")
        policies[platform] = policy
        for component_id, component in platform_components.items():
            previous = components.setdefault(component_id, component)
            if previous != component:
                fail(f"Desktop component terms disagree across platforms: {component_id}")
            archive = component.get("sourceArchive")
            if not isinstance(archive, str) or PurePosixPath(archive).name != archive:
                fail(f"Desktop component source archive is unsafe: {component_id}")
            archives.add(archive)
    return policies, components, sorted(archives)


def checked_supplements(paths: list[Path]) -> dict[str, list[Path]]:
    if not paths:
        fail("Desktop corresponding source requires retained POSIX contrib inputs.")
    values: dict[str, list[Path]] = {}
    for raw in paths:
        directory = raw.resolve(strict=True)
        if raw.is_symlink() or not directory.is_dir():
            fail("Supplemental contrib source input must be a real directory.")
        for path in directory.iterdir():
            if path.name == "SHA256SUMS":
                continue
            if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
                fail(f"Supplemental contrib source input is unsafe: {path}")
            values.setdefault(path.name, []).append(path)
    for name, copies in values.items():
        hashes = {digest(path) for path in copies}
        if len(hashes) != 1:
            fail(f"Supplemental contrib source bytes disagree across audits: {name}")
    return values


def tar_info(path: PurePosixPath, epoch: int, *, directory: bool = False, size: int = 0):
    info = tarfile.TarInfo(path.as_posix() + ("/" if directory else ""))
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


def assemble(
    root: Path,
    base_archive: Path,
    supplemental_contrib: list[Path],
    output: Path,
    version: str,
    tested_commit: str,
    epoch: int,
) -> str:
    root = root.resolve(strict=True)
    base_archive = base_archive.resolve(strict=True)
    output = output.absolute()
    if base_archive.is_symlink() or not base_archive.is_file():
        fail("Base Windows corresponding source must be a real file.")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        fail("Desktop corresponding-source output must be a new file.")
    if not COMMIT.fullmatch(tested_commit):
        fail("Desktop corresponding source requires an exact tested commit.")
    if not SEMVER.fullmatch(version) or "SNAPSHOT" in version.upper():
        fail("Desktop corresponding source requires immutable non-SNAPSHOT SemVer.")

    base_verifier = load_module(
        "kmediavlc_base_source_verifier",
        root / "scripts/verify_corresponding_source_archive.py",
    )
    base_verifier.verify(root, base_archive, version, tested_commit)
    policies, components, required_archives = load_policies(root)
    supplements = checked_supplements(supplemental_contrib)

    with tarfile.open(base_archive, "r:gz") as source:
        members: dict[PurePosixPath, tarfile.TarInfo] = {}
        for member in source.getmembers():
            path = base_verifier.safe_member(member)
            if path in members:
                fail(f"Base corresponding source contains a duplicate member: {path}")
            members[path] = member
        base_contrib = {
            path.name: path
            for path, member in members.items()
            if member.isfile() and path.parent == ARCHIVE_ROOT / "contrib-tarballs"
        }
        selected_sources: dict[str, tuple[str, object]] = {}
        selected_hashes: dict[str, str] = {}
        for archive in required_archives:
            if archive in base_contrib:
                member = members[base_contrib[archive]]
                stream = source.extractfile(member)
                if stream is None:
                    fail(f"Cannot read base contrib source: {archive}")
                with stream:
                    selected_hashes[archive] = digest_stream(stream)
                selected_sources[archive] = ("base", member)
            else:
                copies = supplements.get(archive, [])
                if not copies:
                    fail(f"Desktop corresponding source omits contrib source: {archive}")
                selected_hashes[archive] = digest(copies[0])
                selected_sources[archive] = ("file", copies[0])
            if any(digest(path) != selected_hashes[archive] for path in supplements.get(archive, [])):
                fail(f"Base and POSIX contrib source bytes disagree: {archive}")

        retained = {
            path: member
            for path, member in members.items()
            if member.isfile()
            and path not in {MANIFEST_PATH, CHECKSUM_PATH}
            and path.parent != ARCHIVE_ROOT / "contrib-tarballs"
        }
        manifest = {
            "schemaVersion": 1,
            "target": "desktop-matrix",
            "targets": TARGETS,
            "releaseVersion": version,
            "testedCommit": tested_commit,
            "vlcRevision": PINNED_REVISION,
            "platformReviewStatus": {
                platform: policy["reviewStatus"] for platform, policy in sorted(policies.items())
            },
            "components": components,
            "selectedContribSha256": selected_hashes,
            "baseWindowsSourceSha256": digest(base_archive),
        }
        generated = {
            MANIFEST_PATH: (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
            CHECKSUM_PATH: "".join(
                f"{selected_hashes[name]}  {name}\n" for name in required_archives
            ).encode("ascii"),
        }
        file_paths = set(retained) | set(generated) | {
            ARCHIVE_ROOT / "contrib-tarballs" / name for name in required_archives
        }
        directories = {ARCHIVE_ROOT}
        for path in file_paths:
            directories.update(parent for parent in path.parents if parent.parts)

        with output.open("xb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                fileobj=raw,
                compresslevel=9,
                mtime=max(epoch, 315532800),
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as target:
                    for directory in sorted(
                        directories,
                        key=lambda value: (len(value.parts), value.as_posix()),
                    ):
                        target.addfile(tar_info(directory, epoch, directory=True))
                    for path in sorted(retained):
                        member = retained[path]
                        stream = source.extractfile(member)
                        if stream is None:
                            fail(f"Cannot reopen base source member: {path}")
                        with stream:
                            target.addfile(tar_info(path, epoch, size=member.size), stream)
                    for archive in required_archives:
                        path = ARCHIVE_ROOT / "contrib-tarballs" / archive
                        kind, value = selected_sources[archive]
                        if kind == "base":
                            stream = source.extractfile(value)
                            if stream is None:
                                fail(f"Cannot reopen base contrib source: {archive}")
                            with stream:
                                target.addfile(tar_info(path, epoch, size=value.size), stream)
                        else:
                            file_path = value
                            with file_path.open("rb") as stream:
                                target.addfile(
                                    tar_info(path, epoch, size=file_path.stat().st_size),
                                    stream,
                                )
                    for path in sorted(generated):
                        data = generated[path]
                        target.addfile(tar_info(path, epoch, size=len(data)), io.BytesIO(data))
    return digest(output)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base-archive", type=Path, required=True)
    parser.add_argument("--supplemental-contrib", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--tested-commit", required=True)
    parser.add_argument("--epoch", type=int, required=True)
    arguments = parser.parse_args()
    sha256 = assemble(
        arguments.root,
        arguments.base_archive,
        arguments.supplemental_contrib,
        arguments.output,
        arguments.version,
        arguments.tested_commit,
        arguments.epoch,
    )
    print(f"{sha256}  {arguments.output.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
