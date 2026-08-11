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


PINNED_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
TARGET_MACHINES = {
    "linux-x86_64": "Advanced Micro Devices X86-64",
    "linux-aarch64": "AArch64",
}
MODULE_NAME = re.compile(r"[a-z0-9_]+")
SUPPORT_LIBRARY_NAME = re.compile(r"libvlc_[a-z0-9_]+\.so")
VERSION_TOKEN = re.compile(r"\b(GLIBCXX|GLIBC|CXXABI)_([0-9]+(?:\.[0-9]+)*)\b")
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
EXPECTED_SELECTED_PLUGIN_COUNT = 85
MINIMUM_RAW_PLUGIN_COUNT = 80
MAXIMUM_RAW_PLUGIN_COUNT = 350


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
        fail(f"Required Linux source-build file is missing or unsafe: {relative}")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        fail(f"Linux source-build file escapes its install root: {relative}")
    return candidate


def resolve_tool(value: Path, label: str) -> Path:
    raw = str(value)
    located = shutil.which(raw) if value.parent == Path(".") else raw
    if located is None:
        fail(f"Required Linux runtime tool is missing: {label}")
    try:
        path = Path(located).resolve(strict=True)
    except OSError:
        fail(f"Required Linux runtime tool is missing or unsafe: {label}")
    if not path.is_file():
        fail(f"Required Linux runtime tool is missing or unsafe: {label}")
    return path


def run_tool(command: list[str], timeout_seconds: int = 180) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        fail(f"Linux runtime tool did not complete: {Path(command[0]).name}: {failure}")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
        fail(f"Linux runtime tool failed: {Path(command[0]).name}: {detail}")
    return result.stdout


def parse_dynamic(output: str) -> tuple[str, list[str], str]:
    sonames = re.findall(r"\(SONAME\).*Library soname: \[([^]]+)]", output)
    needed = re.findall(r"\(NEEDED\).*Shared library: \[([^]]+)]", output)
    runpaths = re.findall(r"\(RUNPATH\).*Library runpath: \[([^]]*)]", output)
    if len(sonames) != 1 or len(runpaths) != 1 or "(RPATH)" in output:
        fail("Linux ELF dynamic section has an invalid SONAME/RUNPATH contract.")
    return sonames[0], needed, runpaths[0]


def parse_symbol_versions(output: str) -> dict[str, str | None]:
    maxima: dict[str, tuple[int, ...] | None] = {
        "GLIBC": None,
        "GLIBCXX": None,
        "CXXABI": None,
    }
    for family, raw_version in VERSION_TOKEN.findall(output):
        version = tuple(int(part) for part in raw_version.split("."))
        current = maxima[family]
        if current is None or version > current:
            maxima[family] = version
    return {
        family: None if version is None else ".".join(str(part) for part in version)
        for family, version in maxima.items()
    }


def version_at_most(actual: str, ceiling: str) -> bool:
    left = tuple(int(part) for part in actual.split("."))
    right = tuple(int(part) for part in ceiling.split("."))
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) <= right + (0,) * (width - len(right))


def load_policy(
    root: Path,
    target: str,
    allow_audit_candidate: bool,
) -> tuple[dict, dict, list[tuple[str, str]]]:
    playback = json.loads(
        (root / "compliance/policy/linux-playback-modules.json").read_text(encoding="utf-8")
    )
    if (
        playback.get("schemaVersion") != 1
        or playback.get("targets") != list(TARGET_MACHINES)
        or playback.get("vlcRevision") != PINNED_REVISION
        or target not in playback.get("targets", [])
    ):
        fail("Unsupported Linux playback module policy.")
    if playback.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("Linux playback module dependencies have not completed review.")
    if playback.get("primaryLicenseSpdx") != "LGPL-2.1-or-later":
        fail("Linux playback modules must retain their reviewed primary license.")
    families = playback.get("modulesByFamily")
    if not isinstance(families, dict) or set(families) != ALLOWED_FAMILIES:
        fail("Linux playback module families are incomplete or overbroad.")
    modules: list[tuple[str, str]] = []
    seen: set[str] = set()
    for family in sorted(families):
        names = families[family]
        if not isinstance(names, list) or names != sorted(names) or not names:
            fail(f"Linux playback module family is not a closed sorted list: {family}")
        for name in names:
            if not isinstance(name, str) or not MODULE_NAME.fullmatch(name) or name in seen:
                fail(f"Invalid or duplicate Linux playback module: {name!r}")
            seen.add(name)
            modules.append((family, name))
    if len(modules) != EXPECTED_SELECTED_PLUGIN_COUNT:
        fail("Linux playback module count differs from the closed staging contract.")
    additional = playback.get("additionalDirectSourceLicenses")
    if not isinstance(additional, dict) or not set(additional).issubset(seen):
        fail("Additional direct-source licenses reference an unknown Linux module.")

    binary = json.loads(
        (root / "compliance/policy/linux-binary-components.json").read_text(encoding="utf-8")
    )
    if (
        binary.get("schemaVersion") != 1
        or binary.get("targets") != list(TARGET_MACHINES)
        or binary.get("vlcRevision") != PINNED_REVISION
        or target not in binary.get("targets", [])
    ):
        fail("Unsupported Linux binary component policy.")
    if binary.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("Linux binary link inputs have not completed review.")
    components = binary.get("components")
    module_components = binary.get("moduleComponents")
    core_components = binary.get("coreComponents")
    support_libraries = binary.get("runtimeSupportLibraries")
    allowed_system = binary.get("allowedSystemDependencies")
    allowed_system_by_target = binary.get("allowedSystemDependenciesByTarget")
    ceilings = binary.get("maximumSymbolVersions")
    if (
        not isinstance(components, dict)
        or not isinstance(module_components, dict)
        or not isinstance(core_components, list)
        or not isinstance(support_libraries, dict)
        or list(support_libraries) != sorted(support_libraries)
        or not isinstance(allowed_system, list)
        or allowed_system != sorted(set(allowed_system))
        or not isinstance(allowed_system_by_target, dict)
        or set(allowed_system_by_target) != set(TARGET_MACHINES)
        or any(
            not isinstance(dependencies, list)
            or dependencies != sorted(set(dependencies))
            or set(dependencies) & set(allowed_system)
            for dependencies in allowed_system_by_target.values()
        )
        or not isinstance(ceilings, dict)
        or set(ceilings) != {"GLIBC", "GLIBCXX", "CXXABI"}
        or not set(module_components).issubset(seen)
        or binary.get("buildOnlyContribPackages") != []
    ):
        fail("Linux binary component closure is invalid.")
    for filename, support in support_libraries.items():
        if (
            not SUPPORT_LIBRARY_NAME.fullmatch(filename)
            or not isinstance(support, dict)
            or set(support) != {"licenseSpdx", "requiredByModules", "sourceFiles"}
            or support["licenseSpdx"] != ["LGPL-2.1-or-later"]
            or not isinstance(support["requiredByModules"], list)
            or support["requiredByModules"] != sorted(set(support["requiredByModules"]))
            or not support["requiredByModules"]
            or not set(support["requiredByModules"]).issubset(seen)
            or not isinstance(support["sourceFiles"], list)
            or support["sourceFiles"] != sorted(set(support["sourceFiles"]))
            or not support["sourceFiles"]
            or any(
                not isinstance(source, str)
                or source.startswith("/")
                or ".." in source.split("/")
                or not source.endswith(".c")
                for source in support["sourceFiles"]
            )
        ):
            fail(f"Linux runtime support library policy is invalid: {filename!r}")
    referenced = set(core_components)
    for component_ids in module_components.values():
        if not isinstance(component_ids, list) or component_ids != sorted(set(component_ids)):
            fail("Linux module component closure is not canonical.")
        referenced.update(component_ids)
    if referenced != set(components):
        fail("Linux binary component policy contains unused or missing components.")
    if binary.get("moduleAdditionalLicenses") != additional:
        fail("Linux direct-source license mappings disagree across policies.")
    return playback, binary, modules


def copy_file(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "path": destination,
        "size": destination.stat().st_size,
        "sha256": sha256(destination),
    }


def expected_soname(role: str, filename: str) -> str:
    if role == "BRIDGE":
        return "libkmediavlc_bridge.so"
    if role == "LIBVLC":
        return "libvlc.so.12"
    if role == "CORE":
        return "libvlccore.so.9"
    if role == "SUPPORT":
        return filename
    if role == "PLUGIN":
        return filename
    fail(f"Unsupported Linux ELF role: {role}")


def expected_runpath(role: str) -> str:
    return "$ORIGIN/../../../bin" if role == "PLUGIN" else "$ORIGIN"


def relocate_elf(path: Path, role: str, patchelf: Path, strip: Path) -> None:
    run_tool([str(strip), "--strip-unneeded", str(path)])
    run_tool(
        [
            str(patchelf),
            "--set-soname",
            expected_soname(role, path.name),
            "--set-rpath",
            expected_runpath(role),
            str(path),
        ]
    )


def audit_elf(
    path: Path,
    role: str,
    target: str,
    readelf: Path,
    required_private_dependencies: set[str],
    allowed_system_dependencies: set[str],
    ceilings: dict[str, str],
) -> dict:
    header = run_tool([str(readelf), "-h", str(path)])
    elf_class = re.search(r"^\s*Class:\s+(\S+)\s*$", header, re.MULTILINE)
    machine = re.search(r"^\s*Machine:\s+(.+?)\s*$", header, re.MULTILINE)
    if elf_class is None or elf_class.group(1) != "ELF64":
        fail(f"Linux runtime file is not ELF64: {path.name}")
    if machine is None or machine.group(1) != TARGET_MACHINES[target]:
        fail(f"Linux runtime architecture mismatch: {path.name}")

    dynamic = run_tool([str(readelf), "-dW", str(path)])
    soname, dependencies, runpath = parse_dynamic(dynamic)
    if soname != expected_soname(role, path.name):
        fail(f"Linux runtime SONAME is not application-private: {path.name}")
    if runpath != expected_runpath(role):
        fail(f"Linux runtime RUNPATH is not application-private: {path.name}")
    if any("/" in dependency for dependency in dependencies):
        fail(f"Linux runtime contains an absolute DT_NEEDED entry: {path.name}")
    core_count = dependencies.count("libvlccore.so.9")
    if role in {"LIBVLC", "SUPPORT"}:
        if core_count != 1:
            fail(f"Linux runtime core dependency is not closed: {path.name}")
    elif role == "PLUGIN":
        # --as-needed legitimately removes this edge from self-contained
        # modules such as float_mixer. When present, it must still name the
        # single application-private core accepted below.
        if core_count > 1:
            fail(f"Linux runtime core dependency is not closed: {path.name}")
    elif core_count != 0:
        fail(f"Unexpected Linux runtime core dependency: {path.name}")
    for dependency in sorted(required_private_dependencies):
        if dependencies.count(dependency) != 1:
            fail(f"Linux runtime private dependency is not closed: {path.name}")
    private_dependencies = {"libvlccore.so.9"} | required_private_dependencies
    forbidden = sorted(
        dependency
        for dependency in dependencies
        if dependency not in private_dependencies
        and dependency not in allowed_system_dependencies
    )
    if forbidden:
        fail(f"Linux runtime contains an external ELF dependency: {path.name}: {forbidden}")

    program_headers = run_tool([str(readelf), "-lW", str(path)])
    stack_lines = [line for line in program_headers.splitlines() if "GNU_STACK" in line]
    if len(stack_lines) != 1 or re.search(r"\bRWE\b", stack_lines[0]):
        fail(f"Linux runtime has an executable or missing GNU stack contract: {path.name}")
    if "GNU_RELRO" not in program_headers:
        fail(f"Linux runtime lacks GNU_RELRO: {path.name}")
    notes = run_tool([str(readelf), "-nW", str(path)])
    build_ids = re.findall(r"Build ID:\s*([0-9a-f]+)", notes)
    if len(build_ids) != 1 or len(build_ids[0]) < 32:
        fail(f"Linux runtime lacks one usable GNU build ID: {path.name}")

    versions = parse_symbol_versions(run_tool([str(readelf), "--version-info", str(path)]))
    for family, actual in versions.items():
        if actual is not None and not version_at_most(actual, ceilings[family]):
            fail(
                f"Linux runtime exceeds the {family} symbol ceiling: "
                f"{path.name}: {actual} > {ceilings[family]}"
            )
    return {
        "elfClass": "ELF64",
        "machine": TARGET_MACHINES[target],
        "soname": soname,
        "runpath": runpath,
        "dependencies": dependencies,
        "requiredPrivateDependencies": sorted(required_private_dependencies),
        "maximumSymbolVersions": versions,
        "buildId": build_ids[0],
        "gnuRelro": True,
        "executableStack": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--target", choices=list(TARGET_MACHINES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    parser.add_argument("--patchelf", type=Path, default=Path("patchelf"))
    parser.add_argument("--readelf", type=Path, default=Path("readelf"))
    parser.add_argument("--strip", type=Path, default=Path("strip"))
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    install = args.install.resolve(strict=True)
    bridge = args.bridge.resolve(strict=True)
    output = args.output.resolve()
    report = args.report.resolve()
    if output.exists():
        fail("Linux runtime staging output must not already exist.")
    if report.exists():
        fail("Linux runtime staging report must not already exist.")
    if bridge.is_symlink() or not bridge.is_file() or bridge.stat().st_size == 0:
        fail("The Linux bridge input is missing or unsafe.")
    patchelf = resolve_tool(args.patchelf, "patchelf")
    readelf = resolve_tool(args.readelf, "readelf")
    strip = resolve_tool(args.strip, "strip")

    playback, binary, modules = load_policy(root, args.target, args.allow_audit_candidate)
    copied: list[dict] = []
    elf_files: list[tuple[Path, str, set[str]]] = []
    fixed_files = [
        (require_plain_file(install, "lib/libvlc.so"), "bin/libvlc.so.12", "LIBVLC"),
        (
            require_plain_file(install, "lib/libvlccore.so.9.0.0"),
            "bin/libvlccore.so.9",
            "CORE",
        ),
        (bridge, "bin/libkmediavlc_bridge.so", "BRIDGE"),
    ]
    fixed_files.extend(
        (
            require_plain_file(install, f"lib/{filename}"),
            f"bin/{filename}",
            "SUPPORT",
        )
        for filename in binary["runtimeSupportLibraries"]
    )
    for source, relative, role in fixed_files:
        destination = output.joinpath(*relative.split("/"))
        result = copy_file(source, destination)
        source_components = binary["coreComponents"] if role == "CORE" else []
        entry = {
            **result,
            "path": relative,
            "role": role,
            "sourceComponents": source_components,
        }
        if role == "SUPPORT":
            entry.update(binary["runtimeSupportLibraries"][source.name])
        copied.append(entry)
        elf_files.append((destination, role, set()))

    plugin_root = install / "lib/vlc/plugins"
    plugin_destination = output / "lib/vlc/plugins"
    selected_names: list[str] = []
    for family, name in modules:
        filename = f"lib{name}_plugin.so"
        source = require_plain_file(plugin_root, filename)
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
                "sourceComponents": binary["moduleComponents"].get(name, []),
            }
        )
        required_support = {
            filename
            for filename, support in binary["runtimeSupportLibraries"].items()
            if name in support["requiredByModules"]
        }
        elf_files.append((destination, "PLUGIN", required_support))
        selected_names.append(name)

    raw_plugins = [
        path
        for path in plugin_root.rglob("lib*_plugin.so")
        if path.is_file() and not path.is_symlink()
    ]
    if not MINIMUM_RAW_PLUGIN_COUNT <= len(raw_plugins) <= MAXIMUM_RAW_PLUGIN_COUNT:
        fail("The raw Linux source-build plugin count is outside its audit-candidate bound.")

    audits: dict[str, dict] = {}
    for path, role, required_private_dependencies in elf_files:
        relocate_elf(path, role, patchelf, strip)
        relative = path.relative_to(output).as_posix()
        audits[relative] = audit_elf(
            path,
            role,
            args.target,
            readelf,
            required_private_dependencies,
            set(binary["allowedSystemDependencies"])
            | set(binary["allowedSystemDependenciesByTarget"][args.target]),
            binary["maximumSymbolVersions"],
        )
    if not any(
        audit["maximumSymbolVersions"]["GLIBC"] is not None
        for audit in audits.values()
    ):
        fail("Linux runtime lacks an auditable aggregate GLIBC symbol baseline.")

    cache_generator = require_plain_file(install, "libexec/vlc/vlc-cache-gen")
    try:
        cache_result = subprocess.run(
            [str(cache_generator), str(plugin_destination)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            env={
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin",
                "LD_LIBRARY_PATH": str(install / "lib"),
            },
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as failure:
        fail(f"Linux plugin cache generator did not complete: {failure}")
    if cache_result.returncode != 0:
        detail = cache_result.stderr.strip() or cache_result.stdout.strip() or "no diagnostic"
        fail(f"Linux plugin cache generator failed: {detail}")
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

    expected_paths = {entry["path"] for entry in copied}
    actual_paths = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    if actual_paths != expected_paths or any(path.is_symlink() for path in output.rglob("*")):
        fail("Linux staged runtime contains an uninventoryed file or symbolic link.")
    for entry in copied:
        staged = output.joinpath(*entry["path"].split("/"))
        entry["size"] = staged.stat().st_size
        entry["sha256"] = sha256(staged)

    report.parent.mkdir(parents=True, exist_ok=True)
    report_text = (
        json.dumps(
            {
                "schemaVersion": 1,
                "target": args.target,
                "vlcRevision": PINNED_REVISION,
                "reviewStatus": playback["reviewStatus"],
                "binaryReviewStatus": binary["reviewStatus"],
                "selectedPluginCount": len(selected_names),
                "rawPluginCount": len(raw_plugins),
                "excludedPluginCount": len(raw_plugins) - len(selected_names),
                "frameTransport": "DMA_BUF",
                "gpuPushEvidence": "pending-render-node-and-explicit-fence-test",
                "vrConsumerEvidence": "pending-kmediaplayer-projection-acceptance",
                "auditCandidate": True,
                "files": copied,
                "elf": audits,
                "components": binary["components"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with report.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(report_text)
    print(
        f"Staged {len(selected_names)} closed Linux playback plugins "
        f"from {len(raw_plugins)} candidates for {args.target}."
    )


if __name__ == "__main__":
    main()
