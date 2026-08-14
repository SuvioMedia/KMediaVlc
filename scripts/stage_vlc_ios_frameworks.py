#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Package one audited libVLC iOS slice as app-embeddable frameworks."""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


PINNED_REVISION = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
EXPECTED_MINIMUM_IOS = "16.2"
EXPECTED_RAW_PLUGIN_COUNT = 286
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
TARGETS = {
    "ios-arm64": {
        "architecture": "arm64",
        "minimumOs": EXPECTED_MINIMUM_IOS,
        "otoolPlatform": "2",
        "platform": "IOS",
        "simulator": False,
    },
    "ios-simulator-arm64": {
        "architecture": "arm64",
        "minimumOs": EXPECTED_MINIMUM_IOS,
        "otoolPlatform": "7",
        "platform": "IOSSIMULATOR",
        "simulator": True,
    },
}
SYSTEM_DEPENDENCY_PREFIXES = ("/System/Library/", "/usr/lib/")
CORE_FRAMEWORK = "KMediaVlcCore"
CORE_INSTALL_NAME = f"@rpath/{CORE_FRAMEWORK}.framework/{CORE_FRAMEWORK}"
FIXED_FRAMEWORKS = {
    "BRIDGE": "KMediaVlc",
    "LIBVLC": "KMediaVlcLibVlc",
    "CORE": CORE_FRAMEWORK,
}


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
        fail(f"Required iOS source-build file is missing or unsafe: {relative}")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        fail(f"iOS source-build file escapes its install root: {relative}")
    return candidate


def load_policy(root: Path, allow_audit_candidate: bool) -> tuple[dict, list[tuple[str, str]]]:
    policy = json.loads(
        (root / "compliance/policy/ios-playback-modules.json").read_text(encoding="utf-8")
    )
    if policy.get("schemaVersion") != 1 or policy.get("targets") != sorted(TARGETS):
        fail("Unsupported iOS playback module policy.")
    if policy.get("vlcRevision") != PINNED_REVISION:
        fail("iOS playback modules target a different VLC revision.")
    if policy.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("iOS playback module dependencies have not completed review.")
    if policy.get("primaryLicenseSpdx") != "LGPL-2.1-or-later":
        fail("iOS playback modules must retain their reviewed primary license.")
    families = policy.get("modulesByFamily")
    if not isinstance(families, dict) or set(families) != ALLOWED_FAMILIES:
        fail("iOS playback module families are incomplete or overbroad.")
    modules: list[tuple[str, str]] = []
    seen: set[str] = set()
    for family in sorted(families):
        names = families[family]
        if not isinstance(names, list) or names != sorted(names) or not names:
            fail(f"iOS playback module family is not a closed sorted list: {family}")
        for name in names:
            if not isinstance(name, str) or not MODULE_NAME.fullmatch(name) or name in seen:
                fail(f"Invalid or duplicate iOS playback module: {name!r}")
            seen.add(name)
            modules.append((family, name))
    recipe = json.loads((root / "build-recipes/ios.json").read_text(encoding="utf-8"))
    if recipe.get("stagedPluginCount") != len(modules):
        fail("iOS playback module policy differs from the source-build recipe.")
    additional = policy.get("additionalDirectSourceLicenses")
    if not isinstance(additional, dict) or not set(additional).issubset(seen):
        fail("iOS direct-source licenses reference an unknown module.")
    return policy, modules


def load_binary_policy(
    root: Path,
    modules: list[tuple[str, str]],
    allow_audit_candidate: bool,
) -> dict:
    policy = json.loads(
        (root / "compliance/policy/ios-binary-components.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        policy.get("schemaVersion") != 1
        or policy.get("targets") != sorted(TARGETS)
        or policy.get("vlcRevision") != PINNED_REVISION
    ):
        fail("Unsupported iOS binary component policy.")
    if policy.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("iOS binary link inputs have not completed review.")
    components = policy.get("components")
    module_components = policy.get("moduleComponents")
    core_components = policy.get("coreComponents")
    selected = {name for _, name in modules}
    if (
        not isinstance(components, dict)
        or not isinstance(module_components, dict)
        or not isinstance(core_components, list)
        or not set(module_components).issubset(selected)
        or policy.get("buildOnlyContribPackages") != []
    ):
        fail("iOS binary component closure is invalid.")
    referenced = set(core_components)
    for module, component_ids in module_components.items():
        if (
            not isinstance(component_ids, list)
            or component_ids != sorted(set(component_ids))
            or any(component_id not in components for component_id in component_ids)
        ):
            fail(f"iOS module component closure is invalid: {module}")
        referenced.update(component_ids)
    if referenced != set(components):
        fail("iOS binary component policy contains unused or missing components.")
    return policy


def run_tool(command: list[str], timeout_seconds: int = 120) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        fail(f"iOS runtime tool did not complete: {Path(command[0]).name}: {failure}")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        fail(f"iOS runtime tool failed: {Path(command[0]).name}: {detail}")
    return result.stdout


def parse_install_name(output: str) -> str:
    values = [line.strip() for line in output.splitlines()[1:] if line.strip()]
    if len(values) != 1:
        fail("otool returned an invalid Mach-O install name.")
    return values[0]


def parse_otool_dependencies(output: str) -> list[str]:
    dependencies: list[str] = []
    for line in output.splitlines()[1:]:
        match = re.match(r"\s+([^\s]+)\s+\(compatibility version ", line)
        if match:
            dependencies.append(match.group(1))
    if not dependencies:
        fail("otool returned no Mach-O dependency records.")
    return dependencies


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


def executable_for_plugin(module: str) -> str:
    if not MODULE_NAME.fullmatch(module):
        fail(f"Unsafe iOS plugin module name: {module!r}")
    return f"lib{module}_plugin"


def expected_install_name(role: str, executable: str) -> str:
    if role == "PLUGIN":
        return f"@rpath/{executable}.framework/{executable}"
    framework = FIXED_FRAMEWORKS.get(role)
    if framework is None or executable != framework:
        fail(f"Unsupported iOS framework role: {role}")
    return f"@rpath/{framework}.framework/{framework}"


def relocate_macho(path: Path, role: str, executable: str, install_name_tool: Path) -> None:
    command = [
        str(install_name_tool),
        "-id",
        expected_install_name(role, executable),
    ]
    if role in {"LIBVLC", "PLUGIN"}:
        command.extend(["-change", "@rpath/libvlccore.dylib", CORE_INSTALL_NAME])
    command.append(str(path))
    run_tool(command)


def audit_macho(
    path: Path,
    role: str,
    executable: str,
    target: dict,
    otool: Path,
    lipo: Path,
) -> dict:
    architectures = run_tool([str(lipo), "-archs", str(path)]).strip().split()
    if architectures != ["arm64"]:
        fail(f"iOS runtime file is not exactly arm64: {path.name}: {architectures}")
    install_name = parse_install_name(run_tool([str(otool), "-D", str(path)]))
    expected_name = expected_install_name(role, executable)
    if install_name != expected_name:
        fail(f"iOS framework install name is not application-private: {path.name}")
    dependencies = parse_otool_dependencies(run_tool([str(otool), "-L", str(path)]))
    non_self = [dependency for dependency in dependencies if dependency != install_name]
    expected_internal = [CORE_INSTALL_NAME] if role in {"LIBVLC", "PLUGIN"} else []
    actual_internal = [dependency for dependency in non_self if dependency.startswith("@rpath/")]
    if actual_internal != expected_internal:
        fail(f"iOS framework dependency graph is not closed: {path.name}: {actual_internal}")
    external = [dependency for dependency in non_self if dependency not in actual_internal]
    forbidden = [
        dependency
        for dependency in external
        if not dependency.startswith(SYSTEM_DEPENDENCY_PREFIXES)
    ]
    if forbidden:
        fail(f"iOS framework contains an external Mach-O dependency: {path.name}: {forbidden}")
    layout = run_tool([str(otool), "-l", str(path)])
    if "cmd LC_RPATH" in layout:
        fail(f"iOS framework contains an uncontrolled LC_RPATH: {path.name}")
    versions = parse_build_versions(layout)
    expected_version = [(target["otoolPlatform"], EXPECTED_MINIMUM_IOS)]
    if versions != expected_version:
        fail(f"iOS framework platform/deployment target is invalid: {path.name}: {versions}")
    return {
        "architectures": architectures,
        "dependencies": dependencies,
        "installName": install_name,
        "minimumIos": EXPECTED_MINIMUM_IOS,
        "platform": target["platform"],
    }


def copy_headers(sources: Iterable[Path], destination: Path) -> str:
    names: list[str] = []
    for source in sorted(sources, key=lambda path: path.name):
        if source.is_symlink() or not source.is_file() or source.suffix != ".h":
            fail(f"iOS framework header inventory contains an unsafe entry: {source}")
        if source.name in names:
            fail(f"iOS framework header inventory contains a duplicate: {source.name}")
        shutil.copyfile(source, destination / source.name)
        names.append(source.name)
    if not names:
        fail("iOS framework header inventory is empty.")
    return "vlc.h" if "vlc.h" in names else names[0]


def write_framework_metadata(
    framework: Path,
    executable: str,
    target: dict,
    header_sources: Iterable[Path] | None,
) -> list[str]:
    headers = framework / "Headers"
    modules = framework / "Modules"
    headers.mkdir()
    modules.mkdir()
    if header_sources is None:
        umbrella = f"{executable}.h"
        (headers / umbrella).write_text("#pragma once\n", encoding="utf-8")
    else:
        umbrella = copy_headers(header_sources, headers)
    (modules / "module.modulemap").write_text(
        f"framework module {executable} {{\n"
        f"  umbrella header \"{umbrella}\"\n"
        "  export *\n"
        "  module * { export * }\n"
        "}\n",
        encoding="utf-8",
    )
    bundle_suffix = executable.lower().replace("_", "-")
    metadata = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleExecutable": executable,
        "CFBundleIdentifier": f"io.github.shusek.kmediavlc.{bundle_suffix}",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": executable,
        "CFBundlePackageType": "FMWK",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        "MinimumOSVersion": target["minimumOs"],
    }
    with (framework / "Info.plist").open("wb") as output:
        plistlib.dump(metadata, output, sort_keys=True)
    return sorted(path.name for path in headers.iterdir())


def stage_framework(
    source: Path,
    output: Path,
    role: str,
    executable: str,
    target: dict,
    install_name_tool: Path,
    otool: Path,
    lipo: Path,
    header_sources: Iterable[Path] | None,
) -> dict:
    framework = output / f"{executable}.framework"
    framework.mkdir()
    binary = framework / executable
    shutil.copyfile(source, binary)
    binary.chmod(0o755)
    relocate_macho(binary, role, executable, install_name_tool)
    headers = write_framework_metadata(framework, executable, target, header_sources)
    audit = audit_macho(binary, role, executable, target, otool, lipo)
    return {
        "binary": executable,
        "framework": framework.name,
        "headers": headers,
        "role": role,
        "sha256": sha256(binary),
        "size": binary.stat().st_size,
        "machO": audit,
    }


def stage(
    root: Path,
    install: Path,
    bridge: Path,
    target_name: str,
    output: Path,
    report: Path,
    allow_audit_candidate: bool,
    install_name_tool: Path,
    otool: Path,
    lipo: Path,
) -> dict:
    if target_name not in TARGETS:
        fail(f"Unsupported iOS target: {target_name}")
    if output.exists() or output.is_symlink():
        fail("iOS framework output must not already exist.")
    if report.exists() or report.is_symlink():
        fail("iOS framework report must not already exist.")
    output.parent.resolve(strict=True)
    report.parent.resolve(strict=True)
    if output == report or output in report.parents:
        fail("iOS framework report must remain outside the framework directory.")
    if bridge.is_symlink() or not bridge.is_file() or bridge.stat().st_size == 0:
        fail("The iOS bridge input is missing or unsafe.")
    for tool in (install_name_tool, otool, lipo):
        if tool.is_symlink() or not tool.is_file():
            fail(f"Required iOS runtime tool is missing or unsafe: {tool}")

    policy, modules = load_policy(root, allow_audit_candidate)
    binary_policy = load_binary_policy(root, modules, allow_audit_candidate)
    recipe = json.loads((root / "build-recipes/ios.json").read_text(encoding="utf-8"))
    if recipe.get("rawSourceBuildPluginCount") != EXPECTED_RAW_PLUGIN_COUNT:
        fail("The iOS source-build recipe does not pin its raw plugin inventory.")
    raw_plugins = list((install / "lib/vlc/plugins").rglob("lib*_plugin.dylib"))
    if len(raw_plugins) != EXPECTED_RAW_PLUGIN_COUNT:
        fail("The raw source-build plugin set differs from the closed iOS recipe.")

    temporary = Path(tempfile.mkdtemp(prefix=".kmediavlc-ios-frameworks-", dir=output.parent))
    try:
        target = TARGETS[target_name]
        records: list[dict] = []
        bridge_headers = [require_plain_file(root, "native/include/kmediavlc_client.h")]
        vlc_header_root = install / "include/vlc"
        if vlc_header_root.is_symlink() or not vlc_header_root.is_dir():
            fail("The installed iOS libVLC headers are missing or unsafe.")
        # The installed plugins/ subdirectory is an SDK for VLC module authors,
        # not part of libVLC's public client ABI. Export only the top-level
        # headers installed by lib/Makefile.am.
        vlc_headers = list(vlc_header_root.glob("*.h"))
        fixed = [
            (bridge, "BRIDGE", FIXED_FRAMEWORKS["BRIDGE"], bridge_headers),
            (
                require_plain_file(install, "lib/libvlc.dylib"),
                "LIBVLC",
                FIXED_FRAMEWORKS["LIBVLC"],
                vlc_headers,
            ),
            (
                require_plain_file(install, "lib/libvlccore.dylib"),
                "CORE",
                FIXED_FRAMEWORKS["CORE"],
                None,
            ),
        ]
        for source, role, executable, headers in fixed:
            record = stage_framework(
                source,
                temporary,
                role,
                executable,
                target,
                install_name_tool,
                otool,
                lipo,
                headers,
            )
            record["sourceComponents"] = (
                binary_policy["coreComponents"] if role == "CORE" else []
            )
            records.append(record)

        plugin_root = install / "lib/vlc/plugins"
        for family, module in modules:
            executable = executable_for_plugin(module)
            source = require_plain_file(plugin_root, f"{family}/{executable}.dylib")
            record = stage_framework(
                source,
                temporary,
                "PLUGIN",
                executable,
                target,
                install_name_tool,
                otool,
                lipo,
                None,
            )
            record["family"] = family
            record["module"] = module
            record["sourceComponents"] = binary_policy["moduleComponents"].get(
                module, []
            )
            records.append(record)

        expected_framework_count = len(modules) + len(FIXED_FRAMEWORKS)
        if len(records) != expected_framework_count:
            fail("The iOS framework inventory is incomplete.")
        inventory = {
            "schemaVersion": 1,
            "target": target_name,
            "architecture": target["architecture"],
            "minimumIos": target["minimumOs"],
            "simulator": target["simulator"],
            "vlcRevision": PINNED_REVISION,
            "reviewStatus": policy["reviewStatus"],
            "binaryReviewStatus": binary_policy["reviewStatus"],
            "rawPluginCount": len(raw_plugins),
            "selectedPluginCount": len(modules),
            "excludedPluginCount": len(raw_plugins) - len(modules),
            "frameworkCount": len(records),
            "frameworks": records,
            "components": binary_policy["components"],
        }
        report.write_text(
            json.dumps(inventory, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.rename(output)
        return inventory
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        if report.exists() and not report.is_symlink():
            report.unlink()
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    parser.add_argument(
        "--install-name-tool", type=Path, default=Path("/usr/bin/install_name_tool")
    )
    parser.add_argument("--otool", type=Path, default=Path("/usr/bin/otool"))
    parser.add_argument("--lipo", type=Path, default=Path("/usr/bin/lipo"))
    args = parser.parse_args()
    inventory = stage(
        args.root.resolve(strict=True),
        args.install.resolve(strict=True),
        args.bridge.resolve(strict=True),
        args.target,
        args.output.absolute(),
        args.report.absolute(),
        args.allow_audit_candidate,
        args.install_name_tool,
        args.otool,
        args.lipo,
    )
    print(
        f"Staged {inventory['selectedPluginCount']} closed iOS playback plugins "
        f"as {inventory['frameworkCount']} dynamic frameworks."
    )


if __name__ == "__main__":
    main()
