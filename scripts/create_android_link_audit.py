#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath


VLC_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
LIBVLCJNI_REVISION = "a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21"
NDK_REVISION = "29.0.14206865"
SUPPORTED_TARGETS = {
    "arm64-v8a": "aarch64-linux-android",
    "armeabi-v7a": "arm-linux-androideabi",
}
LGPL_TEXT = (
    "Licensed under the terms of the GNU Lesser General Public License, "
    "version 2.1 or later."
)
GPL_TEXT = "Licensed under the terms of the GNU General Public License, version 2 or later."
MODULE_ENTRY = re.compile(r"^\s*vlc_entry__([a-z0-9_]+),\s*$")
ARCHIVE_INPUT = re.compile(r"^(?P<archive>/[^\n]*?\.a)\((?P<member>[^()\n]+)\):")
NEEDED_ENTRY = re.compile(r"Shared library: \[([^\]\r\n]+)\]")
LOAD_ENTRY = re.compile(r"^\s*LOAD\s+.*\s+(0x[0-9a-fA-F]+)\s*$")
SAFE_MODULE = re.compile(r"[a-z0-9_]+")
SAFE_NEEDED = re.compile(r"lib[A-Za-z0-9_+.-]+\.so")
REQUIRED_EXPORTS = {
    "JNI_OnLoad",
    "libvlc_get_changeset",
    "libvlc_get_version",
    "libvlc_new",
    "libvlc_video_set_output_callbacks",
}
FORBIDDEN_NEEDED = {"libc++_shared.so", "libvlcjni.so"}


def fail(message: str) -> None:
    raise ValueError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def real_file(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        fail(f"{description} must be a real non-empty file.")
    return path.resolve(strict=True)


def real_directory(path: Path, description: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        fail(f"{description} must be a real directory.")
    resolved = path.resolve(strict=True)
    if any(character.isspace() for character in str(resolved)):
        fail(f"{description} path must not contain whitespace.")
    return resolved


def relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        fail(f"Path escapes its expected audit root: {path.name}")
    raise AssertionError("unreachable")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def required_modules(root: Path) -> set[str]:
    recipe = read_json(root / "build-recipes/android.json")
    values = recipe.get("requiredPlaybackModules")
    if (
        not isinstance(values, list)
        or values != sorted(set(values))
        or any(not isinstance(value, str) or not SAFE_MODULE.fullmatch(value) for value in values)
    ):
        fail("Android required playback modules are not a closed sorted list.")
    return set(values)


def libvlcjni_patch(root: Path) -> tuple[str, Path]:
    recipe = read_json(root / "build-recipes/android.json")
    value = recipe.get("libvlcjniPatch")
    if not isinstance(value, str):
        fail("Android libvlcjni patch path is missing.")
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() != value or ".." in relative.parts:
        fail("Android libvlcjni patch path is not canonical.")
    patch = real_file(root.joinpath(*relative.parts), "Android libvlcjni policy patch")
    relative_to(patch, root)
    return value, patch


def parse_module_manifest(path: Path) -> list[str]:
    text = real_file(path, "Generated VLC module manifest").read_text(encoding="utf-8")
    inside = False
    found: list[str] = []
    for line in text.splitlines():
        if line.strip() == "const void *vlc_static_modules[] = {":
            if inside:
                fail("Generated VLC module manifest contains duplicate arrays.")
            inside = True
            continue
        if not inside:
            continue
        if line.strip() == "NULL":
            inside = False
            break
        match = MODULE_ENTRY.fullmatch(line)
        if match is None:
            fail("Generated VLC module manifest contains an unsafe entry.")
        found.append(match.group(1))
    if inside or not found or len(found) != len(set(found)):
        fail("Generated VLC module manifest is empty or duplicated.")
    return sorted(found)


def parse_link_map(path: Path) -> dict[Path, list[str]]:
    archives: dict[Path, set[str]] = {}
    with real_file(path, "Android libvlc linker map").open(
        "r", encoding="utf-8", errors="strict"
    ) as source:
        for line in source:
            for token in line.split():
                match = ARCHIVE_INPUT.match(token)
                if match is None:
                    continue
                archive = Path(match.group("archive"))
                if not archive.is_absolute():
                    fail("Android linker map contains a relative static archive.")
                archive = real_file(archive, "Linked static archive")
                member = match.group("member")
                if (
                    not member
                    or len(member) > 512
                    or any(ord(character) < 0x20 or ord(character) > 0x7E for character in member)
                ):
                    fail("Android linker map contains an unsafe archive member.")
                archives.setdefault(archive, set()).add(member)
    if not archives or len(archives) > 2048:
        fail("Android linker map has no bounded static archive graph.")
    return {path: sorted(members) for path, members in sorted(archives.items(), key=lambda item: str(item[0]))}


def run_tool(executable: Path, *arguments: str) -> str:
    if not executable.is_absolute():
        fail("Android NDK audit tool path must be absolute.")
    try:
        resolved = executable.resolve(strict=True)
    except OSError as error:
        raise ValueError("Android NDK audit tool is missing.") from error
    if not resolved.is_file():
        fail("Android NDK audit tool must resolve to a file.")
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="strict",
        )
    except (OSError, subprocess.CalledProcessError, UnicodeError) as error:
        raise ValueError("Android NDK audit tool failed without trusted output.") from error
    return completed.stdout


def verify_lgpl_strings(text: str, description: str) -> None:
    lines = set(text.splitlines())
    if LGPL_TEXT not in lines or GPL_TEXT in lines:
        fail(f"{description} does not have the closed LGPL module marker.")


def verify_final_license_strings(text: str) -> bool:
    lines = set(text.splitlines())
    if GPL_TEXT in lines:
        fail("Final Android libvlc.so retains a forbidden GPL module marker.")
    return LGPL_TEXT in lines


def parse_needed(text: str) -> list[str]:
    values = sorted(set(NEEDED_ENTRY.findall(text)))
    if (
        not values
        or any(not SAFE_NEEDED.fullmatch(value) for value in values)
        or FORBIDDEN_NEEDED.intersection(values)
    ):
        fail("Android libvlc has an invalid or forbidden DT_NEEDED graph.")
    return values


def verify_load_alignment(text: str) -> int:
    values = [int(match.group(1), 16) for line in text.splitlines() if (match := LOAD_ENTRY.match(line))]
    if not values or any(value != 0x4000 for value in values):
        fail("Android libvlc LOAD segments are not exactly 16 KiB aligned.")
    return 0x4000


def parse_exports(text: str) -> list[str]:
    values = {
        line.split()[-1]
        for line in text.splitlines()
        if line.split() and not line.split()[-1].endswith(":")
    }
    missing = REQUIRED_EXPORTS - values
    if missing:
        fail(f"Android libvlc omits required exports: {sorted(missing)}")
    return sorted(REQUIRED_EXPORTS)


def classify_archive(
    archive: Path,
    build_directory: Path,
    plugin_directory: Path,
    contrib_directory: Path,
    ndk_directory: Path,
    core_archives: dict[Path, str],
) -> tuple[str, str]:
    if archive in core_archives:
        return "VLC_CORE", f"vlc-build/{core_archives[archive]}"
    try:
        relative = archive.relative_to(plugin_directory).as_posix()
        return "VLC_MODULE", f"vlc-build/install/lib/vlc/plugins/{relative}"
    except ValueError:
        pass
    try:
        relative = archive.relative_to(contrib_directory).as_posix()
        return "CONTRIB", f"vlc-contrib/{relative}"
    except ValueError:
        pass
    try:
        relative = archive.relative_to(ndk_directory).as_posix()
        return "NDK_TOOLCHAIN", f"ndk/{relative}"
    except ValueError:
        pass
    try:
        relative = archive.relative_to(build_directory).as_posix()
    except ValueError:
        relative = None
    if relative is not None:
        fail(f"Unclassified VLC build archive entered libvlc.so: {relative}")
    fail(f"Static archive entered libvlc.so from outside the closed roots: {archive.name}")
    raise AssertionError("unreachable")


def create(
    root: Path,
    vlc_source: Path,
    ndk_directory: Path,
    abi: str,
    libvlc: Path,
    link_map: Path,
    readelf: Path,
    nm: Path,
    strings: Path,
    output: Path,
) -> dict:
    if abi not in SUPPORTED_TARGETS:
        fail("Android link audit target ABI is unsupported.")
    root = real_directory(root, "KMediaVlc source root")
    vlc_source = real_directory(vlc_source, "VLC source root")
    ndk_directory = real_directory(ndk_directory, "Android NDK root")
    libvlc = real_file(libvlc, "Source-built Android libvlc.so")
    link_map = real_file(link_map, "Android libvlc linker map")
    output = output.absolute()
    if output.exists() or output.is_symlink() or not output.parent.is_dir() or output.parent.is_symlink():
        fail("Android link audit output must be a new file in a real directory.")

    tuple_name = SUPPORTED_TARGETS[abi]
    build_directory = real_directory(
        vlc_source / f"build-android-{tuple_name}", "VLC Android build directory"
    )
    plugin_directory = real_directory(
        build_directory / "install/lib/vlc/plugins", "VLC Android module directory"
    )
    contrib_directory = real_directory(
        vlc_source / f"contrib/{tuple_name}", "VLC Android contrib directory"
    )
    module_manifest = build_directory / "ndk/libvlcjni-modules.c"
    modules = parse_module_manifest(module_manifest)
    required = required_modules(root)
    patch_path, patch_file = libvlcjni_patch(root)
    if not required.issubset(modules):
        fail(f"Android libvlc omits required playback modules: {sorted(required - set(modules))}")

    module_archives = {
        path.resolve(strict=True): path.name[len("lib") : -len("_plugin.a")]
        for path in plugin_directory.glob("lib*_plugin.a")
        if path.is_file() and not path.is_symlink()
    }
    if set(module_archives.values()) != set(modules) or len(module_archives) != len(modules):
        fail("Generated Android module array differs from its static archive directory.")

    core_relative = {
        "lib/.libs/libvlc.a": "lib/.libs/libvlc.a",
        "src/.libs/libvlccore.a": "src/.libs/libvlccore.a",
        "compat/.libs/libcompat.a": "compat/.libs/libcompat.a",
    }
    core_archives = {
        real_file(build_directory / relative, "VLC core static archive"): canonical
        for relative, canonical in core_relative.items()
    }
    linked = parse_link_map(link_map)
    if not set(module_archives).issubset(linked):
        missing = sorted(module_archives[path] for path in set(module_archives) - set(linked))
        fail(f"Android linker map omits selected VLC modules: {missing}")
    if not set(core_archives).issubset(linked):
        fail("Android linker map omits one or more VLC core archives.")

    archive_entries = []
    module_entries = []
    for archive, members in linked.items():
        kind, canonical_path = classify_archive(
            archive,
            build_directory,
            plugin_directory,
            contrib_directory,
            ndk_directory,
            core_archives,
        )
        entry = {
            "kind": kind,
            "path": canonical_path,
            "sha256": sha256(archive),
            "size": archive.stat().st_size,
            "linkedObjects": members,
        }
        archive_entries.append(entry)
        if kind == "VLC_MODULE":
            module = module_archives.get(archive)
            if module is None:
                fail("An unselected VLC module archive entered libvlc.so.")
            verify_lgpl_strings(run_tool(strings, str(archive)), f"VLC module {module}")
            module_entries.append(
                {
                    "name": module,
                    "archiveSha256": entry["sha256"],
                    "licenseSpdx": "LGPL-2.1-or-later",
                    "linkedObjects": members,
                }
            )
    if not any(entry["kind"] == "CONTRIB" for entry in archive_entries):
        fail("Android libvlc has no audited static contrib archives.")
    if not any(entry["kind"] == "NDK_TOOLCHAIN" for entry in archive_entries):
        fail("Android libvlc has no audited static NDK runtime archives.")
    archive_entries.sort(key=lambda entry: (entry["kind"], entry["path"]))
    module_entries.sort(key=lambda entry: entry["name"])
    if [entry["name"] for entry in module_entries] != modules:
        fail("Android module link evidence is not canonical.")

    final_lgpl_marker_retained = verify_final_license_strings(
        run_tool(strings, str(libvlc))
    )
    needed = parse_needed(run_tool(readelf, "-d", str(libvlc)))
    alignment = verify_load_alignment(run_tool(readelf, "-l", str(libvlc)))
    exports = parse_exports(run_tool(nm, "-D", "--defined-only", str(libvlc)))
    audit = {
        "schemaVersion": 1,
        "target": f"android-{abi}",
        "abi": abi,
        "androidApi": 21,
        "vlcRevision": VLC_REVISION,
        "libvlcjniRevision": LIBVLCJNI_REVISION,
        "ndkRevision": NDK_REVISION,
        "reviewStatus": "candidate-unreviewed-static-components",
        "libvlc": {
            "sha256": sha256(libvlc),
            "size": libvlc.stat().st_size,
            "loadAlignment": alignment,
            "needed": needed,
            "requiredExports": exports,
            "declaredVlcLicenseSpdx": "LGPL-2.1-or-later",
            "effectiveLicenseSpdx": None,
            "lgplModuleMarkerRetained": final_lgpl_marker_retained,
        },
        "modules": module_entries,
        "staticArchives": archive_entries,
        "evidence": {
            "libvlcjniPatch": {
                "path": patch_path,
                "sha256": sha256(patch_file),
            },
            "linkMapSha256": sha256(link_map),
            "moduleManifestSha256": sha256(module_manifest),
        },
    }
    with output.open("x", encoding="utf-8", newline="\n") as target:
        target.write(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vlc-source", type=Path, required=True)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--abi", choices=sorted(SUPPORTED_TARGETS), required=True)
    parser.add_argument("--libvlc", type=Path, required=True)
    parser.add_argument("--link-map", type=Path, required=True)
    parser.add_argument("--readelf", type=Path, required=True)
    parser.add_argument("--nm", type=Path, required=True)
    parser.add_argument("--strings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    audit = create(
        arguments.root,
        arguments.vlc_source,
        arguments.ndk,
        arguments.abi,
        arguments.libvlc,
        arguments.link_map,
        arguments.readelf,
        arguments.nm,
        arguments.strings,
        arguments.output,
    )
    print(
        f"Created {audit['target']} link audit with {len(audit['modules'])} modules and "
        f"{len(audit['staticArchives'])} static archives."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
