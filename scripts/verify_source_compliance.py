# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PINNED_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
PINNED_VERSION = "4.0.0-dev"
ALLOWED_LICENSES = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "LicenseRef-KMediaVlc-Proprietary",
    "LGPL-2.0-or-later",
    "LGPL-2.1-or-later",
    "LGPL-3.0-or-later",
    "MIT",
    "MPL-2.0",
    "Zlib",
}
FORBIDDEN_BINARY_SUFFIXES = {
    ".a",
    ".dll",
    ".dylib",
    ".exe",
    ".lib",
    ".o",
    ".obj",
    ".so",
}
SPDX_EXTENSIONS = {".c", ".cpp", ".h", ".java", ".kt", ".kts", ".md", ".py", ".sh"}
IGNORED_PARTS = {".git", ".gradle", ".idea", ".vlc-source", "build", "__pycache__"}


def fail(message: str) -> None:
    raise SystemExit(message)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as failure:
        fail(f"Invalid JSON file {path}: {failure}")
    if not isinstance(value, dict):
        fail(f"JSON root must be an object: {path}")
    return value


def verify_spdx(root: Path) -> None:
    missing: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() not in SPDX_EXTENSIONS:
            continue
        head = path.read_text(encoding="utf-8", errors="strict")[:4096]
        if "SPDX-License-Identifier:" not in head:
            missing.append(relative.as_posix())
    if missing:
        fail("Files without SPDX identifiers: " + ", ".join(sorted(missing)))


def verify_no_native_payload(root: Path) -> None:
    forbidden: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in IGNORED_PARTS for part in relative.parts):
            continue
        lower_name = path.name.lower()
        if path.suffix.lower() in FORBIDDEN_BINARY_SUFFIXES or ".so." in lower_name:
            forbidden.append(relative.as_posix())
    if forbidden:
        fail("Checked-in native payload is forbidden: " + ", ".join(sorted(forbidden)))


def verify_policy(root: Path) -> None:
    component = load_json(root / "compliance/components/vlc.json")
    policy = load_json(root / "compliance/policy/release-policy.json")
    if component.get("revision") != PINNED_REVISION:
        fail("VLC component revision does not match the pinned bridge ABI.")
    if component.get("version") != PINNED_VERSION:
        fail("VLC component version does not match the pinned bridge ABI.")
    if component.get("stockNightlyReleaseEligible") is not False:
        fail("Stock VLC nightlies must remain release-ineligible.")
    if component.get("releaseRequiresPerBinaryLicenseInventory") is not True:
        fail("Per-binary license inventory must be mandatory.")
    if policy.get("libvlcAbiMajor") != 4 or policy.get("bridgeAbiVersion") != 2:
        fail("Release policy ABI pins are invalid.")
    if sorted(policy.get("requiredFrameDeliveryModes", [])) != ["CPU_PULL", "GPU_PUSH"]:
        fail("Release policy must require both CPU_PULL and GPU_PUSH.")
    if policy.get("allowStockNightly") is not False:
        fail("Release policy must reject stock nightly payloads.")
    allowed = policy.get("allowedLicenseSpdx")
    if not isinstance(allowed, list) or set(allowed) != ALLOWED_LICENSES or len(allowed) != len(ALLOWED_LICENSES):
        fail("Allowed license inventory must match the audited runtime allowlist exactly.")
    forbidden_prefixes = policy.get("forbiddenLicensePrefixes")
    if forbidden_prefixes != ["GPL-", "AGPL-", "LicenseRef-NonFree", "unknown"]:
        fail("Forbidden license prefix policy is incomplete.")
    for expression in allowed:
        if not isinstance(expression, str) or expression.startswith(tuple(forbidden_prefixes)):
            fail(f"Forbidden copyleft expression in bundled policy: {expression!r}")

    playback = load_json(root / "compliance/policy/windows-x86_64-playback-modules.json")
    if playback.get("schemaVersion") != 1 or playback.get("target") != "windows-x86_64":
        fail("Windows playback module policy has an unsupported identity.")
    if playback.get("vlcRevision") != PINNED_REVISION:
        fail("Windows playback module policy targets a different VLC revision.")
    if playback.get("reviewStatus") not in {"pending-meson-dependency-audit", "approved"}:
        fail("Windows playback module policy has an invalid review state.")
    if playback.get("primaryLicenseSpdx") != "LGPL-2.1-or-later":
        fail("Windows playback modules lost their reviewed primary license.")
    families = playback.get("modulesByFamily")
    expected_families = {
        "access", "access/http", "audio_filter", "audio_mixer", "audio_output",
        "codec", "demux", "hw/d3d11", "keystore", "logger", "misc", "packetizer",
        "stream_filter", "text_renderer", "video_chroma", "video_output",
        "video_output/win32",
    }
    if not isinstance(families, dict) or set(families) != expected_families:
        fail("Windows playback module families are incomplete or overbroad.")
    modules: list[str] = []
    for family, names in families.items():
        if not isinstance(names, list) or names != sorted(names) or not names:
            fail(f"Windows playback module family is not a closed sorted list: {family}")
        modules.extend(names)
    if (
        len(modules) != 90
        or len(set(modules)) != len(modules)
        or any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name) for name in modules)
    ):
        fail("Windows playback module allowlist must contain 90 unique safe modules.")
    if set(families).intersection(policy.get("forbiddenPluginFamilies", [])):
        fail("Windows playback allowlist contains a forbidden plugin family.")
    expected_additional = {
        "mkv": ["MIT"],
        "opus": ["BSD-3-Clause"],
        "ts": ["BSD-3-Clause"],
    }
    if playback.get("additionalDirectSourceLicenses") != expected_additional:
        fail("Windows playback direct-source license exceptions changed without review.")

    recipe = load_json(root / "build-recipes/windows.json")
    if recipe.get("vlcRevision") != PINNED_REVISION:
        fail("Windows build recipe does not match the pinned VLC revision.")
    if recipe.get("publicationTargets") != ["windows-x86_64", "windows-aarch64"]:
        fail("The initial publication target matrix must remain Windows-only.")
    if recipe.get("auditToolchainImage") != (
        "registry.videolan.org/vlc-debian-llvm-ucrt:20260611225331"
    ):
        fail("Windows VLC audit must use the toolchain selected by the pinned upstream revision.")
    if recipe.get("compiler") != "LLVM-MinGW-UCRT":
        fail("Windows VLC binaries must use the pinned LLVM/MinGW UCRT compiler.")
    if recipe.get("libVlcCoreAbiMajor") != 9:
        fail("The pinned VLC 4 build must retain its exact internal core ABI dependency.")
    if recipe.get("wineUse") != "Meson-cross-executable-sanity-only":
        fail("Wine must remain limited to Meson's cross-executable sanity probe.")
    if recipe.get("nativeValidationRunner") != "windows-2022":
        fail("Source-built Windows binaries must be loaded and tested on a native Windows runner.")
    arguments = recipe.get("libVlcBuildArguments")
    if not isinstance(arguments, list) or not all(flag in arguments for flag in ["-r", "-u", "-z", "-g", "l", "-m"]):
        fail("Windows VLC recipe is missing required release/UCRT/headless/GPL-disabled flags.")
    if recipe.get("usesPrebuiltContribs") is not False:
        fail("Release recipe must build contribs from their verified source inputs.")
    if recipe.get("mesonInstallTags") != ["runtime"] or recipe.get("stripInstalledTargets") is not True:
        fail("Windows release install must contain only stripped Meson runtime targets.")
    if recipe.get("requiresPerFileInventory") is not True or recipe.get("forbidsStockNightly") is not True:
        fail("Windows release recipe weakened the inventory or nightly prohibition.")
    builder = (root / "scripts/build_vlc_windows.sh").read_text(encoding="utf-8")
    install_markers = [
        'meson_executable="$source_directory/extras/tools/build/bin/meson"',
        '"$meson_executable" install',
        '--tags runtime',
        '--strip',
        'win64-ucrt-meson',
        'winarm64-ucrt-meson',
        'VLC source build produced an empty install payload',
    ]
    if not all(marker in builder for marker in install_markers):
        fail("Windows VLC recipe does not close the headless Meson install step.")
    audit_workflow = (root / ".github/workflows/native-audit.yml").read_text(encoding="utf-8")
    native_validation_markers = [
        "validate-windows-x86-64:",
        "runs-on: windows-2022",
        "Build the bridge natively with MSVC",
        "-PkmediaVlcNativeBridgePath=$bridge",
        "pinnedVideoLanFixturePublishesCpuPullFrame",
        "hardware HDR evidence remains mandatory",
        ".vlc-source/contrib/python-venv",
        'rm -f "$stamp"',
        "runtime_sha256: ${{ steps.package.outputs.runtime_sha256 }}",
        "EXPECTED_RUNTIME_SHA256",
        "Stage the closed Windows playback candidate",
        "--allow-audit-candidate",
        "Generate the plugin cache for the closed candidate",
        "windows-x86_64-candidate",
        "KMEDIAVLC_TEST_PLUGIN_CACHE",
        "49b960ac28ae13153ba8e62e3fceb50408564c21f25fc38936e7c8a06b61f2db",
        "pinnedChromiumHdr10FixturePublishesFp16D3D11Frame",
        "pinnedChromiumHttpsFixturePublishesCpuPullFrame",
        "85af8764718f33f0d221e96f31f5d993f364b4a2",
        "intro-targets.json",
        'cp -a "$meson_info"',
        "ninja-commands.txt",
        "ninja-graph.dot",
    ]
    if not all(marker in audit_workflow for marker in native_validation_markers):
        fail("The source-built VLC payload lacks mandatory native Windows validation.")


def verify_pin_occurrences(root: Path) -> None:
    parser = (
        root
        / "runtime-desktop/src/main/java/io/github/shusek/kmediavlc/runtime/desktop/NativePayloadManifest.java"
    ).read_text(encoding="utf-8")
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    if PINNED_REVISION not in parser or PINNED_REVISION not in notices:
        fail("Pinned VLC revision is not consistent across runtime code and notices.")
    if PINNED_VERSION not in parser:
        fail("Pinned VLC version is not enforced by the runtime parser.")


def verify_legal_files(root: Path) -> None:
    required = [
        root / "LICENSE",
        root / "NOTICE",
        root / "THIRD_PARTY_NOTICES.md",
        root / "LICENSES/LGPL-2.1.txt",
        root / "LICENSES/ISC-kmediavlc-client-api.txt",
        root / "gradle/wrapper/LICENSE",
    ]
    missing = [str(path.relative_to(root)) for path in required if not path.is_file() or path.stat().st_size < 100]
    if missing:
        fail("Missing or truncated legal files: " + ", ".join(missing))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    verify_spdx(root)
    verify_no_native_payload(root)
    verify_policy(root)
    verify_pin_occurrences(root)
    verify_legal_files(root)
    print("KMediaVlc source and licensing policy verified.")


if __name__ == "__main__":
    main()
