#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


PINNED_REVISION = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
MODULE_NAME = re.compile(r"[a-z0-9_]+")
ALLOWED_FAMILIES = {
    "access",
    "audio_filter",
    "audio_mixer",
    "audio_output",
    "codec",
    "demux",
    "keystore",
    "logger",
    "misc",
    "packetizer",
    "stream_filter",
    "text_renderer",
    "video_chroma",
    "video_output",
}
SYSTEM_DEPENDENCY_PREFIXES = ("/System/Library/", "/usr/lib/")
EXPECTED_MINIMUM_MACOS = "14.0"


def fail(message: str) -> None:
    raise SystemExit(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_plain_file(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*relative.split("/"))
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size == 0:
        fail(f"Required source-build file is missing or unsafe: {relative}")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        fail(f"Source-build file escapes its install root: {relative}")
    return candidate


def load_policy(root: Path, allow_audit_candidate: bool) -> tuple[dict, dict, list[tuple[str, str]]]:
    path = root / "compliance/policy/macos-aarch64-playback-modules.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schemaVersion") != 1 or policy.get("target") != "macos-aarch64":
        fail("Unsupported macOS playback module policy.")
    if policy.get("vlcRevision") != PINNED_REVISION:
        fail("macOS playback modules target a different VLC revision.")
    if policy.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("macOS playback module dependencies have not completed review.")
    if policy.get("primaryLicenseSpdx") != "LGPL-2.1-or-later":
        fail("macOS playback modules must retain their reviewed primary license.")
    families = policy.get("modulesByFamily")
    if not isinstance(families, dict) or set(families) != ALLOWED_FAMILIES:
        fail("macOS playback module families are incomplete or overbroad.")
    modules: list[tuple[str, str]] = []
    seen: set[str] = set()
    for family in sorted(families):
        names = families[family]
        if not isinstance(names, list) or names != sorted(names) or not names:
            fail(f"macOS playback module family is not a closed sorted list: {family}")
        for name in names:
            if not isinstance(name, str) or not MODULE_NAME.fullmatch(name) or name in seen:
                fail(f"Invalid or duplicate macOS playback module: {name!r}")
            seen.add(name)
            modules.append((family, name))
    recipe_path = root / "build-recipes/macos.json"
    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    expected_module_count = recipe.get("stagedPluginCount")
    if not isinstance(expected_module_count, int) or len(modules) != expected_module_count:
        fail("macOS playback module policy differs from the source-build recipe.")
    additional = policy.get("additionalDirectSourceLicenses")
    if not isinstance(additional, dict) or not set(additional).issubset(seen):
        fail("Additional direct-source licenses reference an unknown macOS module.")
    binary_path = root / "compliance/policy/macos-aarch64-binary-components.json"
    binary = json.loads(binary_path.read_text(encoding="utf-8"))
    if (
        binary.get("schemaVersion") != 1
        or binary.get("target") != "macos-aarch64"
        or binary.get("vlcRevision") != PINNED_REVISION
    ):
        fail("Unsupported macOS binary component policy.")
    if binary.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("macOS binary link inputs have not completed review.")
    if (
        policy.get("coreAdditionalDirectSourceLicenses") != ["MIT"]
        or binary.get("coreAdditionalLicenses")
        != policy.get("coreAdditionalDirectSourceLicenses")
    ):
        fail("macOS core direct-source license policies are incomplete or disagree.")
    components = binary.get("components")
    module_components = binary.get("moduleComponents")
    core_components = binary.get("coreComponents")
    build_only_components = binary.get("buildOnlyContribPackages")
    if (
        not isinstance(components, dict)
        or not isinstance(module_components, dict)
        or not isinstance(core_components, list)
        or not set(module_components).issubset(seen)
        or build_only_components != ["jinja", "markupsafe"]
    ):
        fail("macOS binary component closure is invalid.")
    referenced = set(core_components)
    for component_ids in module_components.values():
        if not isinstance(component_ids, list):
            fail("macOS module component closure is invalid.")
        referenced.update(component_ids)
    if referenced | set(build_only_components) != set(components):
        fail("macOS binary component policy contains unused or missing components.")
    return policy, binary, modules


def copy_file(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "path": destination,
        "size": destination.stat().st_size,
        "sha256": sha256(destination),
    }


def run_tool(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    timeout_seconds: int = 120,
) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env=environment,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        fail(f"macOS runtime tool did not complete: {Path(command[0]).name}: {failure}")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        fail(f"macOS runtime tool failed: {Path(command[0]).name}: {detail}")
    return result.stdout


def parse_otool_dependencies(output: str) -> list[str]:
    lines = output.splitlines()
    dependencies: list[str] = []
    for line in lines[1:]:
        match = re.match(r"\s+([^\s]+)\s+\(compatibility version ", line)
        if match:
            dependencies.append(match.group(1))
    if not dependencies:
        fail("otool returned no Mach-O dependency records.")
    return dependencies


def parse_install_name(output: str) -> str:
    values = [line.strip() for line in output.splitlines()[1:] if line.strip()]
    if len(values) != 1:
        fail("otool returned an invalid Mach-O install name.")
    return values[0]


def parse_build_versions(output: str) -> list[tuple[str, str]]:
    versions: list[tuple[str, str]] = []
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "cmd LC_BUILD_VERSION":
            continue
        platform = ""
        minimum = ""
        for detail in lines[index + 1 : index + 8]:
            tokens = detail.split()
            if len(tokens) == 2 and tokens[0] == "platform":
                platform = tokens[1]
            if len(tokens) == 2 and tokens[0] == "minos":
                minimum = tokens[1]
        versions.append((platform, minimum))
    return versions


def expected_install_name(role: str, filename: str) -> str:
    if role == "BRIDGE":
        return "@rpath/libkmediavlc_bridge.dylib"
    if role == "LIBVLC":
        return "@rpath/libvlc.12.dylib"
    if role == "CORE":
        return "@rpath/libvlccore.9.dylib"
    if role == "PLUGIN":
        return f"@rpath/{filename}"
    fail(f"Unsupported Mach-O role: {role}")


def expected_private_core_dependency(role: str) -> str | None:
    if role == "LIBVLC":
        return "@loader_path/libvlccore.9.dylib"
    if role == "PLUGIN":
        return "@loader_path/../../../bin/libvlccore.9.dylib"
    return None


def relocate_macho(path: Path, role: str, install_name_tool: Path, codesign: Path) -> None:
    command = [
        str(install_name_tool),
        "-id",
        expected_install_name(role, path.name),
    ]
    core_dependency = expected_private_core_dependency(role)
    if core_dependency is not None:
        command.extend(["-change", "@rpath/libvlccore.dylib", core_dependency])
    command.append(str(path))
    run_tool(command)
    run_tool([str(codesign), "--force", "--sign", "-", "--timestamp=none", str(path)])


def audit_macho(path: Path, role: str, otool: Path, lipo: Path) -> dict:
    architectures = run_tool([str(lipo), "-archs", str(path)]).strip().split()
    if architectures != ["arm64"]:
        fail(f"macOS runtime file is not exactly arm64: {path.name}: {architectures}")

    install_name = parse_install_name(run_tool([str(otool), "-D", str(path)]))
    expected_name = expected_install_name(role, path.name)
    if install_name != expected_name:
        fail(f"macOS runtime install name is not application-private: {path.name}")

    load_output = run_tool([str(otool), "-L", str(path)])
    dependencies = parse_otool_dependencies(load_output)
    expected_core = expected_private_core_dependency(role)
    private_dependencies = [dependency for dependency in dependencies if dependency != install_name]
    if expected_core is not None:
        if private_dependencies.count(expected_core) != 1:
            fail(f"macOS runtime core dependency is not closed: {path.name}")
        private_dependencies.remove(expected_core)
    forbidden = [
        dependency
        for dependency in private_dependencies
        if not dependency.startswith(SYSTEM_DEPENDENCY_PREFIXES)
    ]
    if forbidden:
        fail(f"macOS runtime contains an external Mach-O dependency: {path.name}: {forbidden}")

    layout = run_tool([str(otool), "-l", str(path)])
    if "cmd LC_RPATH" in layout:
        fail(f"macOS runtime contains an uncontrolled LC_RPATH: {path.name}")
    build_versions = parse_build_versions(layout)
    if build_versions != [("1", EXPECTED_MINIMUM_MACOS)]:
        fail(f"macOS runtime deployment target is not exactly 14.0: {path.name}: {build_versions}")
    return {
        "architectures": architectures,
        "installName": install_name,
        "dependencies": dependencies,
        "minimumMacos": EXPECTED_MINIMUM_MACOS,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    parser.add_argument("--install-name-tool", type=Path, default=Path("/usr/bin/install_name_tool"))
    parser.add_argument("--codesign", type=Path, default=Path("/usr/bin/codesign"))
    parser.add_argument("--otool", type=Path, default=Path("/usr/bin/otool"))
    parser.add_argument("--lipo", type=Path, default=Path("/usr/bin/lipo"))
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    install = args.install.resolve(strict=True)
    bridge = args.bridge.resolve(strict=True)
    output = args.output.resolve()
    report = args.report.resolve()
    if output.exists():
        fail("macOS runtime staging output must not already exist.")
    if report.exists():
        fail("macOS runtime staging report must not already exist.")
    if bridge.is_symlink() or not bridge.is_file() or bridge.stat().st_size == 0:
        fail("The macOS bridge input is missing or unsafe.")
    for tool in (args.install_name_tool, args.codesign, args.otool, args.lipo):
        if tool.is_symlink() or not tool.is_file():
            fail(f"Required macOS runtime tool is missing or unsafe: {tool}")

    policy, binary_policy, modules = load_policy(root, args.allow_audit_candidate)
    copied: list[dict] = []
    mach_o_files: list[tuple[Path, str]] = []
    fixed_files = [
        (require_plain_file(install, "lib/libvlc.12.dylib"), "bin/libvlc.12.dylib", "LIBVLC"),
        (require_plain_file(install, "lib/libvlccore.9.dylib"), "bin/libvlccore.9.dylib", "CORE"),
        (bridge, "bin/libkmediavlc_bridge.dylib", "BRIDGE"),
    ]
    for source, relative, role in fixed_files:
        destination = output.joinpath(*relative.split("/"))
        result = copy_file(source, destination)
        source_components = binary_policy["coreComponents"] if role == "CORE" else []
        copied.append(
            {**result, "path": relative, "role": role, "sourceComponents": source_components}
        )
        mach_o_files.append((destination, role))

    plugin_root = install / "lib/vlc/plugins"
    plugin_destination = output / "lib/vlc/plugins"
    selected_names: list[str] = []
    for family, name in modules:
        filename = f"lib{name}_plugin.dylib"
        source = require_plain_file(plugin_root, f"{family}/{filename}")
        relative = f"lib/vlc/plugins/{filename}"
        destination = plugin_destination / filename
        result = copy_file(source, destination)
        copied.append(
            {
                **result,
                "path": relative,
                "role": "PLUGIN",
                "family": family,
                "module": name,
                "sourceComponents": binary_policy["moduleComponents"].get(name, []),
            }
        )
        mach_o_files.append((destination, "PLUGIN"))
        selected_names.append(name)

    raw_plugins = list(plugin_root.rglob("lib*_plugin.dylib"))
    recipe = json.loads((root / "build-recipes/macos.json").read_text(encoding="utf-8"))
    expected_raw_plugin_count = recipe.get("rawSourceBuildPluginCount")
    if not isinstance(expected_raw_plugin_count, int) or len(raw_plugins) != expected_raw_plugin_count:
        fail("The raw source-build plugin set differs from the closed macOS recipe.")

    audits: dict[str, dict] = {}
    for path, role in mach_o_files:
        relocate_macho(path, role, args.install_name_tool, args.codesign)
        relative = path.relative_to(output).as_posix()
        audits[relative] = audit_macho(path, role, args.otool, args.lipo)

    cache_generator = require_plain_file(install, "libexec/vlc/vlc-cache-gen")
    run_tool(
        [str(cache_generator), str(plugin_destination)],
        environment={
            "DYLD_LIBRARY_PATH": str(install / "lib"),
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "TMPDIR": "/tmp",
        },
        timeout_seconds=180,
    )
    cache = require_plain_file(output, "lib/vlc/plugins/plugins.dat")
    copied.append(
        {
            "path": "lib/vlc/plugins/plugins.dat",
            "size": cache.stat().st_size,
            "sha256": sha256(cache),
            "role": "DATA",
            "sourceComponents": [],
        }
    )

    for entry in copied:
        staged = output.joinpath(*entry["path"].split("/"))
        entry["size"] = staged.stat().st_size
        entry["sha256"] = sha256(staged)
    report.parent.mkdir(parents=True, exist_ok=True)
    report_text = (
        json.dumps(
            {
                "schemaVersion": 1,
                "target": policy["target"],
                "vlcRevision": policy["vlcRevision"],
                "reviewStatus": policy["reviewStatus"],
                "binaryReviewStatus": binary_policy["reviewStatus"],
                "auditCandidate": (
                    policy["reviewStatus"] != "approved"
                    or binary_policy["reviewStatus"] != "approved"
                ),
                "selectedPluginCount": len(selected_names),
                "rawPluginCount": len(raw_plugins),
                "excludedPluginCount": len(raw_plugins) - len(selected_names),
                "files": copied,
                "machO": audits,
                "components": binary_policy["components"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with report.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(report_text)
    print(
        f"Staged {len(selected_names)} closed macOS playback plugins "
        f"from {len(raw_plugins)} candidates."
    )


if __name__ == "__main__":
    main()
