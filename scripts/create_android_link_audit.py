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
ABI_TOOLCHAIN_NAMES = {
    "arm64-v8a": {
        "runtimeArch": "aarch64",
        "builtinsArch": "aarch64",
        "targetTuple": "aarch64-linux-android",
    },
    "armeabi-v7a": {
        "runtimeArch": "arm",
        "builtinsArch": "arm",
        "targetTuple": "arm-linux-androideabi",
    },
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
SAFE_COMPONENT = re.compile(r"[a-z0-9][a-z0-9-]+")
SAFE_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]+")
SAFE_SOURCE_ARCHIVE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)")
SAFE_CONTRIB_ARCHIVE = re.compile(r"vlc-contrib/lib/lib[A-Za-z0-9_+.-]+\.a")
SAFE_HOST_TAG = re.compile(r"(?:darwin|linux)-x86_64")
EXPECTED_NDK_TEMPLATES = {
    "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
    "libclang_rt.builtins-{builtinsArch}-android.a",
    "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
    "{runtimeArch}/libunwind.a",
    "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
    "libc++_static.a",
    "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
    "libc++abi.a",
}
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


def static_component_policy(root: Path) -> dict:
    policy = read_json(root / "compliance/policy/android-static-components.json")
    expected_keys = {
        "schemaVersion",
        "target",
        "vlcRevision",
        "ndkRevision",
        "reviewStatus",
        "contribComponents",
        "contribArchives",
        "ndkComponents",
        "ndkArchiveTemplates",
    }
    if set(policy) != expected_keys or policy.get("schemaVersion") != 1:
        fail("Android static component policy fields are not closed.")
    if (
        policy.get("target") != "android-arm"
        or policy.get("vlcRevision") != VLC_REVISION
        or policy.get("ndkRevision") != NDK_REVISION
        or policy.get("reviewStatus")
        != "source-mapped-license-and-notice-review-pending"
    ):
        fail("Android static component policy identity or review state is invalid.")

    components = policy.get("contribComponents")
    if not isinstance(components, dict) or list(components) != sorted(components) or not components:
        fail("Android contrib components must be a non-empty sorted closed map.")
    for component_id, component in components.items():
        if not SAFE_COMPONENT.fullmatch(component_id) or not isinstance(component, dict):
            fail(f"Android contrib component is unsafe: {component_id!r}")
        if set(component) != {"version", "sourceArchives"}:
            fail(f"Android contrib component fields are not closed: {component_id}")
        if not isinstance(component["version"], str) or not SAFE_VERSION.fullmatch(
            component["version"]
        ):
            fail(f"Android contrib component version is unsafe: {component_id}")
        sources = component["sourceArchives"]
        if (
            not isinstance(sources, list)
            or sources != sorted(set(sources))
            or not sources
            or any(
                not isinstance(source, str) or not SAFE_SOURCE_ARCHIVE.fullmatch(source)
                for source in sources
            )
        ):
            fail(f"Android contrib source archives are not canonical: {component_id}")

    contrib_archives = policy.get("contribArchives")
    if (
        not isinstance(contrib_archives, dict)
        or list(contrib_archives) != sorted(contrib_archives)
        or len(contrib_archives) != 62
    ):
        fail("Android contrib archive map must contain the exact sorted 62-archive graph.")
    for archive, component_id in contrib_archives.items():
        if not SAFE_CONTRIB_ARCHIVE.fullmatch(archive) or component_id not in components:
            fail(f"Android contrib archive mapping is unsafe: {archive!r}")
    if set(contrib_archives.values()) != set(components):
        fail("Android contrib component policy contains unused or missing components.")

    ndk_components = policy.get("ndkComponents")
    if not isinstance(ndk_components, dict) or list(ndk_components) != sorted(ndk_components):
        fail("Android NDK components must be a sorted closed map.")
    for component_id, component in ndk_components.items():
        if not SAFE_COMPONENT.fullmatch(component_id) or not isinstance(component, dict):
            fail(f"Android NDK component is unsafe: {component_id!r}")
        if set(component) != {"version", "evidenceFiles", "sourceStatus"}:
            fail(f"Android NDK component fields are not closed: {component_id}")
        if component["version"] != NDK_REVISION:
            fail(f"Android NDK component version is invalid: {component_id}")
        if component["evidenceFiles"] != ["NOTICE", "NOTICE.toolchain", "source.properties"]:
            fail(f"Android NDK evidence files are incomplete: {component_id}")
        if component["sourceStatus"] != "pending-corresponding-source-map":
            fail(f"Android NDK source review state is invalid: {component_id}")

    ndk_templates = policy.get("ndkArchiveTemplates")
    if (
        not isinstance(ndk_templates, dict)
        or list(ndk_templates) != sorted(ndk_templates)
        or set(ndk_templates) != EXPECTED_NDK_TEMPLATES
        or set(ndk_templates.values()) != set(ndk_components)
    ):
        fail("Android NDK archive templates are not the exact closed runtime graph.")
    return policy


def expanded_ndk_archive_components(ndk_directory: Path, abi: str, policy: dict) -> dict[str, str]:
    prebuilt = real_directory(
        ndk_directory / "toolchains/llvm/prebuilt", "Android NDK host toolchain directory"
    )
    host_tags = sorted(
        path.name for path in prebuilt.iterdir() if path.is_dir() and not path.is_symlink()
    )
    if len(host_tags) != 1 or not SAFE_HOST_TAG.fullmatch(host_tags[0]):
        fail("Android NDK must contain exactly one supported host toolchain.")
    values = {"hostTag": host_tags[0], **ABI_TOOLCHAIN_NAMES[abi]}
    return {
        template.format_map(values): component
        for template, component in policy["ndkArchiveTemplates"].items()
    }


def static_component_evidence(
    vlc_source: Path,
    ndk_directory: Path,
    policy: dict,
    contrib_component_ids: set[str],
    ndk_component_ids: set[str],
) -> list[dict]:
    tarballs = real_directory(vlc_source / "contrib/tarballs", "VLC contrib tarball directory")
    entries: list[dict] = []
    for component_id in sorted(contrib_component_ids):
        component = policy["contribComponents"][component_id]
        source_entries = []
        for source_name in component["sourceArchives"]:
            source = real_file(tarballs / source_name, f"VLC contrib source archive {source_name}")
            if source.parent != tarballs:
                fail("Android contrib source archive escaped its closed tarball directory.")
            source_entries.append(
                {
                    "path": f"vlc-contrib-tarballs/{source_name}",
                    "sha256": sha256(source),
                    "size": source.stat().st_size,
                }
            )
        entries.append(
            {
                "id": component_id,
                "kind": "VLC_CONTRIB",
                "version": component["version"],
                "sourceArchives": source_entries,
            }
        )

    for component_id in sorted(ndk_component_ids):
        component = policy["ndkComponents"][component_id]
        evidence_files = []
        for relative in component["evidenceFiles"]:
            evidence = real_file(ndk_directory / relative, f"Android NDK evidence file {relative}")
            if evidence.parent != ndk_directory:
                fail("Android NDK evidence file escaped its closed root.")
            evidence_files.append(
                {
                    "path": f"ndk/{relative}",
                    "sha256": sha256(evidence),
                    "size": evidence.stat().st_size,
                }
            )
        entries.append(
            {
                "id": component_id,
                "kind": "NDK_TOOLCHAIN",
                "version": component["version"],
                "sourceStatus": component["sourceStatus"],
                "evidenceFiles": evidence_files,
            }
        )
    return sorted(entries, key=lambda entry: entry["id"])


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
    component_policy = static_component_policy(root)
    component_policy_path = real_file(
        root / "compliance/policy/android-static-components.json",
        "Android static component policy",
    )
    expected_ndk_archives = expanded_ndk_archive_components(
        ndk_directory, abi, component_policy
    )
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
    linked_contrib_archives: set[str] = set()
    linked_ndk_archives: set[str] = set()
    contrib_component_ids: set[str] = set()
    ndk_component_ids: set[str] = set()
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
        if kind == "CONTRIB":
            component_id = component_policy["contribArchives"].get(canonical_path)
            if component_id is None:
                fail(f"Unmapped Android contrib archive entered libvlc.so: {canonical_path}")
            entry["component"] = component_id
            linked_contrib_archives.add(canonical_path)
            contrib_component_ids.add(component_id)
        elif kind == "NDK_TOOLCHAIN":
            component_id = expected_ndk_archives.get(canonical_path)
            if component_id is None:
                fail(f"Unmapped Android NDK archive entered libvlc.so: {canonical_path}")
            entry["component"] = component_id
            linked_ndk_archives.add(canonical_path)
            ndk_component_ids.add(component_id)
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
    expected_contrib_archives = set(component_policy["contribArchives"])
    if linked_contrib_archives != expected_contrib_archives:
        fail(
            "Android contrib link graph differs from the exact source-mapped policy: "
            f"missing={sorted(expected_contrib_archives - linked_contrib_archives)}, "
            f"unexpected={sorted(linked_contrib_archives - expected_contrib_archives)}"
        )
    if linked_ndk_archives != set(expected_ndk_archives):
        fail(
            "Android NDK link graph differs from the exact source-mapped policy: "
            f"missing={sorted(set(expected_ndk_archives) - linked_ndk_archives)}, "
            f"unexpected={sorted(linked_ndk_archives - set(expected_ndk_archives))}"
        )
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
    component_entries = static_component_evidence(
        vlc_source,
        ndk_directory,
        component_policy,
        contrib_component_ids,
        ndk_component_ids,
    )
    audit = {
        "schemaVersion": 1,
        "target": f"android-{abi}",
        "abi": abi,
        "androidApi": 21,
        "vlcRevision": VLC_REVISION,
        "libvlcjniRevision": LIBVLCJNI_REVISION,
        "ndkRevision": NDK_REVISION,
        "reviewStatus": "candidate-source-mapped-license-review-pending",
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
        "staticComponents": component_entries,
        "evidence": {
            "libvlcjniPatch": {
                "path": patch_path,
                "sha256": sha256(patch_file),
            },
            "linkMapSha256": sha256(link_map),
            "moduleManifestSha256": sha256(module_manifest),
            "staticComponentPolicy": {
                "path": "compliance/policy/android-static-components.json",
                "sha256": sha256(component_policy_path),
            },
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
