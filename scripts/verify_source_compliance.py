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
    "BSL-1.0",
    "FTL",
    "IJG",
    "ISC",
    "LicenseRef-KMediaVlc-Proprietary",
    "LGPL-2.0-or-later",
    "LGPL-2.1-or-later",
    "LGPL-3.0-or-later",
    "Libpng-2.0",
    "MIT",
    "MPL-2.0",
    "TU-Berlin-1.0",
    "Zlib",
}
COMPONENT_NOTICE_FILES = {
    "dav1d": "Dav1d-COPYING.txt",
    "ffmpeg": "FFmpeg-LICENSE.txt",
    "flac": "FLAC-COPYING-XIPH.txt",
    "freetype": "FreeType-FTL.txt",
    "fribidi": "LGPL-2.1.txt",
    "gmp": "LGPL-3.0.txt",
    "gnutls": "LGPL-2.1.txt",
    "gnutls-libtasn1": "LGPL-2.1.txt",
    "gnutls-libunistring": "LGPL-3.0.txt",
    "gsm": "GSM-COPYRIGHT.txt",
    "harfbuzz": "HarfBuzz-COPYING.txt",
    "libass": "libass-COPYING.txt",
    "libdvbpsi": "LGPL-2.1.txt",
    "libebml": "LGPL-2.1.txt",
    "libgcrypt": "LGPL-2.1.txt",
    "libgpg-error": "LGPL-2.1.txt",
    "libiconv": "LGPL-2.1.txt",
    "libjpeg-turbo": "libjpeg-turbo-LICENSE.txt",
    "libmatroska": "LGPL-2.1.txt",
    "libogg": "libogg-COPYING.txt",
    "libpng": "libpng-LICENSE.txt",
    "libssh2": "libssh2-COPYING.txt",
    "libvorbis": "libvorbis-COPYING.txt",
    "libvpx": "libvpx-LICENSE.txt",
    "libxml2": "libxml2-Copyright.txt",
    "nettle": "LGPL-3.0.txt",
    "openjpeg": "OpenJPEG-LICENSE.txt",
    "opus": "Opus-COPYING.txt",
    "soxr": "SoXR-LICENCE.txt",
    "speexdsp": "SpeexDSP-COPYING.txt",
    "utfcpp": "BSL-1.0.txt",
    "zlib": "zlib-LICENSE.txt",
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
SPDX_EXTENSIONS = {".c", ".cpp", ".h", ".java", ".kt", ".kts", ".m", ".md", ".py", ".sh"}
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

    binary = load_json(root / "compliance/policy/windows-x86_64-binary-components.json")
    if (
        binary.get("schemaVersion") != 1
        or binary.get("target") != "windows-x86_64"
        or binary.get("vlcRevision") != PINNED_REVISION
    ):
        fail("Windows binary component policy has an unsupported identity.")
    if binary.get("toolchainImage") != "registry.videolan.org/vlc-debian-llvm-ucrt:20260611225331":
        fail("Windows binary component policy targets a different toolchain.")
    if binary.get("reviewStatus") not in {"pending-link-command-audit", "approved"}:
        fail("Windows binary component policy has an invalid review state.")
    components = binary.get("components")
    if not isinstance(components, dict) or list(components) != sorted(components) or not components:
        fail("Windows binary components must be a non-empty sorted closed map.")
    for component_id, component_policy in components.items():
        if not re.fullmatch(r"[a-z0-9-]+", component_id) or not isinstance(component_policy, dict):
            fail(f"Invalid Windows binary component: {component_id!r}")
        if set(component_policy) != {"version", "licenseSpdx", "sourceArchive"}:
            fail(f"Windows binary component fields are not closed: {component_id}")
        licenses = component_policy["licenseSpdx"]
        if not isinstance(licenses, list) or licenses != sorted(set(licenses)) or not licenses:
            fail(f"Windows binary component licenses are not canonical: {component_id}")
        if any(license_id not in ALLOWED_LICENSES for license_id in licenses):
            fail(f"Windows binary component has an unapproved license: {component_id}")
        if not isinstance(component_policy["version"], str) or not component_policy["version"]:
            fail(f"Windows binary component version is missing: {component_id}")
        if not re.fullmatch(r"[A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)", component_policy["sourceArchive"]):
            fail(f"Windows binary component source archive is unsafe: {component_id}")
    module_components = binary.get("moduleComponents")
    expected_component_modules = {
        "adaptive", "avcodec", "flac", "freetype", "gnutls", "inflate", "jpeg",
        "libass", "mkv", "mp4", "ogg", "opus", "png", "sftp", "soxr",
        "speex_resampler", "swscale", "ts", "vorbis", "xml",
    }
    if not isinstance(module_components, dict) or set(module_components) != expected_component_modules:
        fail("Windows binary module/component closure changed without review.")
    referenced_components: set[str] = set()
    for module, component_ids in module_components.items():
        if module not in modules or component_ids != sorted(set(component_ids)) or not component_ids:
            fail(f"Windows binary module components are not canonical: {module}")
        if any(component_id not in components for component_id in component_ids):
            fail(f"Windows binary module references an unknown component: {module}")
        referenced_components.update(component_ids)
    core_components = binary.get("coreComponents")
    if core_components != sorted(set(core_components or [])) or any(
        component_id not in components for component_id in (core_components or [])
    ):
        fail("Windows core component closure is not canonical.")
    referenced_components.update(core_components)
    if referenced_components != set(components):
        fail("Windows binary component policy contains unused or missing components.")
    if binary.get("moduleAdditionalLicenses") != expected_additional:
        fail("Windows binary direct-source license exceptions changed without review.")

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
    if recipe.get("contribBuildArguments") != ["--disable-sout", "--enable-shout"]:
        fail("Windows playback recipe must exclude encoders while retaining upstream libshout.")
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
        'export CONTRIBFLAGS="--disable-sout --enable-shout"',
        'VLC source build produced an empty install payload',
    ]
    if not all(marker in builder for marker in install_markers):
        fail("Windows VLC recipe does not close the headless Meson install step.")
    bridge_cmake = (root / "native/CMakeLists.txt").read_text(encoding="utf-8")
    if 'MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>DLL"' not in bridge_cmake:
        fail("The Windows bridge must explicitly use the dynamic MSVC runtime.")
    audit_workflow = (root / ".github/workflows/native-audit.yml").read_text(encoding="utf-8")
    native_validation_markers = [
        "validate-windows-x86-64:",
        "runs-on: windows-2022",
        "Build the bridge natively with MSVC",
        "MultiThreadedDLL</RuntimeLibrary>",
        "bridge-link.command.1.tlog",
        "-PkmediaVlcNativeBridgePath=$bridge",
        "pinnedVideoLanFixturePublishesCpuPullFrame",
        "hardware HDR evidence remains mandatory",
        ".vlc-source/contrib/python-venv",
        'rm -f "$stamp"',
        "llvm-ucrt-lgpl-playback-20260611225331-v5",
        "EXACT_CACHE_HIT",
        ".vlc-source/contrib/x86_64-w64-mingw32ucrt",
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
        "toolchain-static-archives-SHA256SUMS",
        "toolchain-licenses",
        "libclang_rt.builtins-x86_64.a",
        "ninja-commands.txt",
        "ninja-graph.dot",
        "vlc-windows-x86_64-link-audit-",
        "candidate_version:",
        "package_corresponding_source.py",
        "create_windows_native_inventory.py",
        ":runtime-desktop:verifyRuntimeJar",
        "bundledRuntimeExtractsAndPublishesCpuPullFrame",
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


def verify_macos_transport_contract(root: Path) -> None:
    playback = load_json(root / "compliance/policy/macos-aarch64-playback-modules.json")
    if (
        playback.get("schemaVersion") != 1
        or playback.get("target") != "macos-aarch64"
        or playback.get("vlcRevision") != PINNED_REVISION
    ):
        fail("macOS playback module policy has an unsupported identity.")
    if playback.get("reviewStatus") not in {
        "pending-mach-o-and-source-license-audit",
        "approved",
    }:
        fail("macOS playback module policy has an invalid review state.")
    if playback.get("primaryLicenseSpdx") != "LGPL-2.1-or-later":
        fail("macOS playback modules lost their reviewed primary license.")
    macos_families = playback.get("modulesByFamily")
    expected_macos_families = {
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
    if not isinstance(macos_families, dict) or set(macos_families) != expected_macos_families:
        fail("macOS playback module families are incomplete or overbroad.")
    macos_modules: list[str] = []
    for family, names in macos_families.items():
        if not isinstance(names, list) or names != sorted(names) or not names:
            fail(f"macOS playback module family is not a closed sorted list: {family}")
        macos_modules.extend(names)
    required_macos_modules = {
        "auhal",
        "avcodec",
        "cvpx",
        "gl",
        "glinterop_cvpx",
        "glinterop_sw",
        "glsampler_builtin",
        "https",
        "libass",
        "mkv",
        "mp4",
        "securetransport",
        "videotoolbox",
        "vgl",
        "vmem",
    }
    if (
        len(macos_modules) != 89
        or len(set(macos_modules)) != 89
        or not required_macos_modules.issubset(macos_modules)
        or any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name) for name in macos_modules)
    ):
        fail("macOS playback allowlist must contain its 89 unique transport modules.")
    expected_additional = {
        "mkv": ["MIT"],
        "opus": ["BSD-3-Clause"],
        "ts": ["BSD-3-Clause"],
    }
    if playback.get("additionalDirectSourceLicenses") != expected_additional:
        fail("macOS playback direct-source license exceptions changed without review.")

    binary = load_json(root / "compliance/policy/macos-aarch64-binary-components.json")
    if (
        binary.get("schemaVersion") != 1
        or binary.get("target") != "macos-aarch64"
        or binary.get("vlcRevision") != PINNED_REVISION
    ):
        fail("macOS binary component policy has an unsupported identity.")
    expected_toolchain = {
        "xcodeVersion": "26.6",
        "xcodeBuild": "17F113",
        "sdkVersion": "26.5",
        "minimumMacos": "14.0",
        "architecture": "arm64",
    }
    if binary.get("toolchain") != expected_toolchain:
        fail("macOS binary component policy targets a different toolchain.")
    if binary.get("reviewStatus") not in {
        "pending-link-command-and-license-audit",
        "approved",
    }:
        fail("macOS binary component policy has an invalid review state.")
    if binary.get("buildOnlyContribPackages") != []:
        fail("macOS playback contrib closure contains unreviewed build-only packages.")
    expected_macos_components = {
        "dav1d", "ffmpeg", "flac", "freetype", "fribidi", "gsm", "harfbuzz",
        "libass", "libdvbpsi", "libebml", "libiconv", "libjpeg-turbo",
        "libmatroska", "libogg", "libpng", "libvorbis", "libvpx", "libxml2",
        "openjpeg", "opus", "soxr", "zlib",
    }
    macos_components = binary.get("components")
    if (
        not isinstance(macos_components, dict)
        or list(macos_components) != sorted(macos_components)
        or set(macos_components) != expected_macos_components
    ):
        fail("macOS binary components must match the closed playback dependency map.")
    for component_id, component_policy in macos_components.items():
        if not re.fullmatch(r"[a-z0-9-]+", component_id) or not isinstance(component_policy, dict):
            fail(f"Invalid macOS binary component: {component_id!r}")
        if set(component_policy) != {"version", "licenseSpdx", "sourceArchive"}:
            fail(f"macOS binary component fields are not closed: {component_id}")
        licenses = component_policy["licenseSpdx"]
        if not isinstance(licenses, list) or licenses != sorted(set(licenses)) or not licenses:
            fail(f"macOS binary component licenses are not canonical: {component_id}")
        if any(license_id not in ALLOWED_LICENSES for license_id in licenses):
            fail(f"macOS binary component has an unapproved license: {component_id}")
        if not isinstance(component_policy["version"], str) or not component_policy["version"]:
            fail(f"macOS binary component version is missing: {component_id}")
        if not re.fullmatch(
            r"[A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)",
            component_policy["sourceArchive"],
        ):
            fail(f"macOS binary component source archive is unsafe: {component_id}")
    macos_module_components = binary.get("moduleComponents")
    expected_macos_component_modules = {
        "avcodec", "dav1d", "flac", "freetype", "inflate", "jpeg", "libass",
        "mkv", "mp4", "ogg", "opus", "packetizer_avparser", "png", "soxr",
        "swscale", "ts", "vorbis", "vpx", "xml",
    }
    if (
        not isinstance(macos_module_components, dict)
        or set(macos_module_components) != expected_macos_component_modules
    ):
        fail("macOS binary module/component closure changed without review.")
    referenced_macos_components: set[str] = set()
    for module, component_ids in macos_module_components.items():
        if module not in macos_modules or component_ids != sorted(set(component_ids)) or not component_ids:
            fail(f"macOS binary module components are not canonical: {module}")
        if any(component_id not in macos_components for component_id in component_ids):
            fail(f"macOS binary module references an unknown component: {module}")
        referenced_macos_components.update(component_ids)
    macos_core_components = binary.get("coreComponents")
    if macos_core_components != ["libiconv"]:
        fail("macOS core component closure changed without review.")
    referenced_macos_components.update(macos_core_components)
    if referenced_macos_components != set(macos_components):
        fail("macOS binary component policy contains unused or missing components.")
    if binary.get("moduleAdditionalLicenses") != expected_additional:
        fail("macOS binary direct-source license exceptions changed without review.")

    selected_contribs = [
        "ass", "dav1d", "dvbpsi", "ebml", "ffmpeg", "flac", "freetype2",
        "fribidi", "harfbuzz", "jpeg", "libxml2", "matroska", "ogg", "opus",
        "png", "soxr", "vorbis", "vpx", "zlib",
    ]
    resolved_contribs = sorted(selected_contribs + ["gsm", "iconv", "openjpeg"])
    recipe = load_json(root / "build-recipes/macos.json")
    expected_build_arguments = [
        "--arch=arm64",
        "--sdk=macosx",
        "--enable-shared",
        "--disable-debug",
        "--config=build-recipes/vlc-apple.conf",
    ]
    if (
        recipe.get("target") != "macos-aarch64"
        or recipe.get("vlcRevision") != PINNED_REVISION
        or recipe.get("minimumOs") != "14.0"
        or recipe.get("buildMode") != "shared"
        or recipe.get("libVlcBuildArguments") != expected_build_arguments
        or recipe.get("libVlcDylibMajor") != 12
        or recipe.get("libVlcCoreDylibMajor") != 9
        or recipe.get("usesPrebuiltContribs") is not False
        or recipe.get("selectedContribPackages") != selected_contribs
        or recipe.get("resolvedContribPackages") != resolved_contribs
        or recipe.get("renderEngine") != "OPENGL"
        or recipe.get("frameTransport") != "IOSURFACE"
        or recipe.get("stagedPluginCount") != 89
        or recipe.get("rawSourceBuildPluginCount") != 285
        or recipe.get("pluginCacheGeneratedAfterRelocation") is not True
        or recipe.get("relocationSignature") != "adhoc-replaced-by-consuming-app-signature"
        or recipe.get("requiresConsumerCodeSigning") is not True
        or recipe.get("candidateReleaseEligible") is not False
        or recipe.get("reviewStatus") != "candidate-source-build-and-link-audit-pending"
    ):
        fail("The pinned macOS source-build recipe is incomplete or release-open.")

    profile = (root / "build-recipes/vlc-apple.conf").read_text(encoding="utf-8")
    contrib_match = re.search(
        r"export VLC_CONTRIB_OPTIONS_BASE=\(\n(?P<body>.*?)\n\)", profile, re.DOTALL
    )
    if contrib_match is None:
        fail("The pinned Apple contrib profile is missing.")
    actual_contrib_options = {
        line.strip()
        for line in contrib_match.group("body").splitlines()
        if line.strip()
    }
    expected_contrib_options = {
        "--disable-all",
        "--disable-gpl",
        "--disable-gnuv3",
        "--disable-sout",
        "--enable-ad-clauses",
        *(f"--enable-{package}" for package in selected_contribs),
    }
    if actual_contrib_options != expected_contrib_options:
        fail("The Apple contrib profile differs from its closed package graph.")
    configure_markers = [
        "--disable-addonmanagermodules",
        "--disable-libplacebo",
        "--disable-macosx",
        "--disable-nfs",
        "--disable-opencv4",
        "--disable-qt",
        "--disable-rav1e",
        "--disable-smb2",
        "--disable-sout",
        "--disable-vulkan",
        "--enable-avformat",
        "--enable-libass",
        "--enable-matroska",
    ]
    if not all(marker in profile for marker in configure_markers):
        fail("The Apple VLC profile does not exclude its broad non-playback graph.")

    builder = (root / "scripts/build_vlc_macos.sh").read_text(encoding="utf-8")
    builder_markers = [
        'readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"',
        "--arch=arm64",
        "--sdk=macosx",
        "--enable-shared",
        "--disable-debug",
        'git -C "$source_directory" status --porcelain --untracked-files=no',
        'make -C "$contrib_directory" list',
        "vlc-macosx-arm64",
    ]
    if not all(marker in builder for marker in builder_markers):
        fail("The macOS VLC build wrapper does not preserve the pinned upstream recipe.")

    stager = (root / "scripts/stage_vlc_macos_runtime.py").read_text(encoding="utf-8")
    stager_markers = [
        '"@loader_path/libvlccore.9.dylib"',
        '"@loader_path/../../../bin/libvlccore.9.dylib"',
        '"DYLD_LIBRARY_PATH": str(install / "lib")',
        '"LC_ALL": "C"',
        '"TMPDIR": "/tmp"',
        '"cmd LC_RPATH"',
        'EXPECTED_MINIMUM_MACOS = "14.0"',
        'architectures != ["arm64"]',
        'timeout_seconds=180',
        'raw_plugins = list(plugin_root.rglob("lib*_plugin.dylib"))',
        'recipe.get("rawSourceBuildPluginCount")',
    ]
    if not all(marker in stager for marker in stager_markers) or "os.environ" in stager:
        fail("The macOS staging recipe does not close relocation, cache, and tool inputs.")

    gradle_build = (root / "build.gradle.kts").read_text(encoding="utf-8")
    bridge_build_markers = [
        'nativeTargetName.get() == "macos-aarch64"',
        '"-DCMAKE_OSX_ARCHITECTURES=arm64"',
        '"-DCMAKE_OSX_DEPLOYMENT_TARGET=14.0"',
    ]
    if not all(marker in gradle_build for marker in bridge_build_markers):
        fail("The macOS bridge build does not pin architecture and deployment target.")

    renderer = (root / "native/src/macos_iosurface_renderer.cpp").read_text(encoding="utf-8")
    renderer_markers = [
        "constexpr std::size_t kSurfaceCount = 4",
        "libvlc_video_engine_opengl",
        "CGLTexImageIOSurface2D",
        "kCVPixelFormatType_32BGRA",
        "kCVPixelFormatType_64RGBAHalf",
        "glFlush();",
        "frame->platform_owner = surface",
        "surface->output_generation",
        "Keep this callback",
        "KMEDIAVLC_IOSURFACE_ID",
    ]
    if not all(marker in renderer for marker in renderer_markers):
        fail("The macOS OpenGL/IOSurface ownership contract is incomplete.")

    cmake = (root / "native/CMakeLists.txt").read_text(encoding="utf-8")
    cmake_markers = [
        "elseif(APPLE)",
        "src/macos_iosurface_renderer.cpp",
        "CoreFoundation REQUIRED",
        "CoreVideo REQUIRED",
        "IOSurface REQUIRED",
        "OpenGL REQUIRED",
        "KMEDIAVLC_BUILD_TEST_FIXTURES",
        "tests/fake_libvlc.cpp",
    ]
    if not all(marker in cmake for marker in cmake_markers):
        fail("The macOS renderer or its hermetic native fixture is not wired into CMake.")

    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    ci_markers = [
        "verify-macos-iosurface:",
        "runs-on: macos-15",
        "kmediaVlcBuildNativeTestFixtures=true",
        "libkmediavlc_fake_libvlc.dylib",
        "real SDR/HDR IOSurfaces",
    ]
    if not all(marker in ci for marker in ci_markers):
        fail("CI must exercise the macOS bridge and real SDR/HDR IOSurface allocations.")

    desktop_build = (root / "runtime-desktop/build.gradle.kts").read_text(encoding="utf-8")
    if (
        'inputs.file(bridgePath).withPropertyName("nativeBridgeTestBinary")' not in desktop_build
        or "outputs.upToDateWhen { !nativeBridgeTestPath.isPresent }" not in desktop_build
        or "Native bridge integration must execute on the current hardware" not in desktop_build
    ):
        fail("Native bridge integration results must never be reused from Gradle caches.")

    fixture = (root / "native/tests/fake_libvlc.cpp").read_text(encoding="utf-8")
    integration = (
        root
        / "runtime-desktop/src/test/java/io/github/shusek/kmediavlc/runtime/desktop/"
        "VlcDesktopPlayerIntegrationTest.java"
    ).read_text(encoding="utf-8")
    if (
        "libvlc_video_set_output_callbacks" not in fixture
        or "libvlc_video_engine_opengl" not in fixture
        or "fakeLibVlcPublishesRealSdrAndHdrMacIosurfaceFrames" not in integration
        or "pinnedVideoLanFixturePublishesAndReplacesRealMacIosurfaceFrames" not in integration
        or "inspectMacIosurfaceFrame" not in integration
    ):
        fail("The pinned macOS callback ABI lacks its real IOSurface integration gate.")

    parser = (
        root
        / "runtime-desktop/src/main/java/io/github/shusek/kmediavlc/runtime/desktop/"
        "NativePayloadManifest.java"
    ).read_text(encoding="utf-8")
    runtime = (
        root
        / "runtime-desktop/src/main/java/io/github/shusek/kmediavlc/runtime/desktop/"
        "VlcDesktopRuntime.java"
    ).read_text(encoding="utf-8")
    documentation = (root / "docs/MACOS.md").read_text(encoding="utf-8")
    if (
        'case "macos-aarch64"' not in parser
        or "Set.of(VlcRenderEngine.OPENGL)" not in parser
        or 'return "macos-aarch64"' not in runtime
        or "not a published" not in documentation
        or "Publication gates still open" not in documentation
    ):
        fail("The macOS target must remain exact-engine and publication-fail-closed.")


def verify_ios_runtime_contract(root: Path) -> None:
    playback = load_json(root / "compliance/policy/ios-playback-modules.json")
    expected_targets = ["ios-arm64", "ios-simulator-arm64"]
    if (
        playback.get("schemaVersion") != 1
        or playback.get("targets") != expected_targets
        or playback.get("vlcRevision") != PINNED_REVISION
    ):
        fail("iOS playback module policy has an unsupported identity.")
    if playback.get("reviewStatus") not in {
        "pending-framework-and-source-license-audit",
        "approved",
    }:
        fail("iOS playback module policy has an invalid review state.")
    if playback.get("primaryLicenseSpdx") != "LGPL-2.1-or-later":
        fail("iOS playback modules lost their reviewed primary license.")
    families = playback.get("modulesByFamily")
    expected_families = {
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
    if not isinstance(families, dict) or set(families) != expected_families:
        fail("iOS playback module families are incomplete or overbroad.")
    modules: list[str] = []
    for family, names in families.items():
        if not isinstance(names, list) or names != sorted(names) or not names:
            fail(f"iOS playback module family is not a closed sorted list: {family}")
        modules.extend(names)
    required_modules = {
        "audiounit_ios",
        "avcodec",
        "cvpx",
        "https",
        "libass",
        "mkv",
        "mp4",
        "securetransport",
        "videotoolbox",
        "vmem",
    }
    if (
        len(modules) != 84
        or len(set(modules)) != 84
        or not required_modules.issubset(modules)
        or "vgl" in modules
        or any(not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name) for name in modules)
    ):
        fail("iOS playback allowlist must contain its 84 unique CPU-pull modules.")
    expected_additional = {
        "mkv": ["MIT"],
        "opus": ["BSD-3-Clause"],
        "ts": ["BSD-3-Clause"],
    }
    if playback.get("additionalDirectSourceLicenses") != expected_additional:
        fail("iOS playback direct-source license exceptions changed without review.")

    binary = load_json(root / "compliance/policy/ios-binary-components.json")
    if (
        binary.get("schemaVersion") != 1
        or binary.get("targets") != expected_targets
        or binary.get("vlcRevision") != PINNED_REVISION
    ):
        fail("iOS binary component policy has an unsupported identity.")
    expected_toolchain = {
        "xcodeVersion": "26.6",
        "xcodeBuild": "17F113",
        "sdkVersion": "26.5",
        "minimumIos": "16.2",
        "architecture": "arm64",
    }
    if binary.get("toolchain") != expected_toolchain:
        fail("iOS binary component policy targets a different toolchain.")
    if binary.get("reviewStatus") not in {
        "pending-link-command-and-license-audit",
        "approved",
    }:
        fail("iOS binary component policy has an invalid review state.")
    if binary.get("buildOnlyContribPackages") != []:
        fail("iOS contrib closure contains an unreviewed build-only package.")
    expected_components = {
        "dav1d", "ffmpeg", "flac", "freetype", "fribidi", "gsm", "harfbuzz",
        "libass", "libdvbpsi", "libebml", "libjpeg-turbo", "libmatroska",
        "libogg", "libpng", "libvorbis", "libvpx", "libxml2", "openjpeg",
        "opus", "soxr", "utfcpp", "zlib",
    }
    components = binary.get("components")
    if (
        not isinstance(components, dict)
        or list(components) != sorted(components)
        or set(components) != expected_components
    ):
        fail("iOS binary components must match the closed playback dependency map.")
    for component_id, component_policy in components.items():
        if not re.fullmatch(r"[a-z0-9-]+", component_id) or not isinstance(component_policy, dict):
            fail(f"Invalid iOS binary component: {component_id!r}")
        if set(component_policy) != {"version", "licenseSpdx", "sourceArchive"}:
            fail(f"iOS binary component fields are not closed: {component_id}")
        licenses = component_policy["licenseSpdx"]
        if not isinstance(licenses, list) or licenses != sorted(set(licenses)) or not licenses:
            fail(f"iOS binary component licenses are not canonical: {component_id}")
        if any(license_id not in ALLOWED_LICENSES for license_id in licenses):
            fail(f"iOS binary component has an unapproved license: {component_id}")
        if not isinstance(component_policy["version"], str) or not component_policy["version"]:
            fail(f"iOS binary component version is missing: {component_id}")
        if not re.fullmatch(
            r"[A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)",
            component_policy["sourceArchive"],
        ):
            fail(f"iOS binary component source archive is unsafe: {component_id}")
    module_components = binary.get("moduleComponents")
    expected_component_modules = {
        "avcodec", "dav1d", "flac", "freetype", "inflate", "jpeg", "libass",
        "mkv", "mp4", "ogg", "opus", "packetizer_avparser", "png", "soxr",
        "swscale", "ts", "vorbis", "vpx", "xml",
    }
    if not isinstance(module_components, dict) or set(module_components) != expected_component_modules:
        fail("iOS binary module/component closure changed without review.")
    referenced: set[str] = set()
    for module, component_ids in module_components.items():
        if module not in modules or component_ids != sorted(set(component_ids)) or not component_ids:
            fail(f"iOS binary module components are not canonical: {module}")
        if any(component_id not in components for component_id in component_ids):
            fail(f"iOS binary module references an unknown component: {module}")
        referenced.update(component_ids)
    if binary.get("coreComponents") != []:
        fail("iOS core must use only platform-system dynamic dependencies.")
    if referenced != set(components):
        fail("iOS binary component policy contains unused or missing components.")
    if binary.get("moduleAdditionalLicenses") != expected_additional:
        fail("iOS binary direct-source license exceptions changed without review.")

    selected_contribs = [
        "ass", "dav1d", "dvbpsi", "ebml", "ffmpeg", "flac", "freetype2",
        "fribidi", "harfbuzz", "jpeg", "libxml2", "matroska", "ogg", "opus",
        "png", "soxr", "vorbis", "vpx", "zlib",
    ]
    resolved_contribs = sorted(selected_contribs + ["gsm", "openjpeg", "utfcpp"])
    targets = {
        "ios-arm64": {
            "sdk": "iphoneos",
            "architecture": "arm64",
            "minimumOs": "16.2",
            "simulator": False,
        },
        "ios-simulator-arm64": {
            "sdk": "iphonesimulator",
            "architecture": "arm64",
            "minimumOs": "16.2",
            "simulator": True,
        },
    }
    build_arguments = {
        target: [
            "--arch=arm64",
            f"--sdk={settings['sdk']}",
            "--enable-shared",
            "--disable-debug",
            "--config=build-recipes/vlc-apple.conf",
        ]
        for target, settings in targets.items()
    }
    recipe = load_json(root / "build-recipes/ios.json")
    if (
        recipe.get("targets") != targets
        or recipe.get("vlcRevision") != PINNED_REVISION
        or recipe.get("buildMode") != "shared-frameworks"
        or recipe.get("libVlcBuildArguments") != build_arguments
        or recipe.get("sourcePatches") != [
            "build-recipes/patches/vlc-ios-meson-native-compiler.patch",
            "build-recipes/patches/fribidi-meson-native-generator.patch",
        ]
        or recipe.get("mesonNativeFile") != "build-recipes/vlc-apple-native.ini"
        or recipe.get("sourceOverlays") != ["build-recipes/vlc-contrib-utfcpp-rules.mak"]
        or recipe.get("selectedContribPackages") != selected_contribs
        or recipe.get("resolvedContribPackages") != resolved_contribs
        or recipe.get("usesPrebuiltContribs") is not False
        or recipe.get("requiredFrameDeliveryModes") != ["CPU_PULL"]
        or recipe.get("stagedPluginCount") != 84
        or recipe.get("rawSourceBuildPluginCount") != 285
        or recipe.get("frameworkCountPerSlice") != 87
        or recipe.get("requiresApplicationPrivateRelocation") is not True
        or recipe.get("requiresConsumerCodeSigning") is not True
        or recipe.get("candidateReleaseEligible") is not False
    ):
        fail("The pinned iOS source-build recipe is incomplete or release-open.")

    profile = (root / "build-recipes/vlc-apple.conf").read_text(encoding="utf-8")
    for marker in (
        'VLC_DEPLOYMENT_TARGET_IOS="16.2"',
        'VLC_DEPLOYMENT_TARGET_IOS_SIMULATOR="16.2"',
        "VLC_CONTRIB_OPTIONS_IOS=()",
        "VLC_CONFIG_OPTIONS_IOS=(",
        "VLC_MODULE_REMOVAL_LIST_IOS=()",
    ):
        if marker not in profile:
            fail("The Apple VLC profile does not pin its iOS target contract.")
    builder = (root / "scripts/build_vlc_ios.sh").read_text(encoding="utf-8")
    builder_markers = [
        'readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"',
        "iphoneos)",
        "iphonesimulator)",
        'git -C "$source_directory" apply --check "$source_patch"',
        'KMEDIAVLC_MESON_NATIVE_FILE="$meson_native_file"',
        'KMEDIAVLC_MESON_NATIVE_TMPDIR="$meson_native_tmpdir"',
        'contrib/src/utfcpp',
        'make -C "$contrib_directory" list',
    ]
    if not all(marker in builder for marker in builder_markers):
        fail("The iOS VLC build wrapper does not preserve the closed source recipe.")
    source_patch = (
        root / "build-recipes/patches/vlc-ios-meson-native-compiler.patch"
    ).read_text(encoding="utf-8")
    patch_markers = [
        "KMEDIAVLC_MESON_NATIVE_FILE",
        "KMEDIAVLC_MESON_NATIVE_TMPDIR",
        "kmediavlc-meson-native-generator.patch",
        "retain the Apple cross target and deployment flags from HOSTVARS_PIC",
        "CFLAGS +=",
    ]
    if not all(marker in source_patch for marker in patch_markers):
        fail("The iOS VLC source patch does not preserve host tools and deployment flags.")
    bridge_builder = (root / "scripts/build_kmediavlc_ios_bridge.sh").read_text(encoding="utf-8")
    bridge_markers = [
        "-DCMAKE_SYSTEM_NAME=iOS",
        "-DCMAKE_OSX_SYSROOT=\"$sdk\"",
        "-DCMAKE_OSX_ARCHITECTURES=arm64",
        "-DCMAKE_OSX_DEPLOYMENT_TARGET=\"$MINIMUM_IOS\"",
        'platform $expected_platform',
        'minos $MINIMUM_IOS',
    ]
    if not all(marker in bridge_builder for marker in bridge_markers):
        fail("The iOS bridge build wrapper does not pin its SDK and ABI.")
    stager = (root / "scripts/stage_vlc_ios_frameworks.py").read_text(encoding="utf-8")
    stager_markers = [
        'CORE_INSTALL_NAME = f"@rpath/{CORE_FRAMEWORK}.framework/{CORE_FRAMEWORK}"',
        'return f"lib{module}_plugin"',
        'EXPECTED_MINIMUM_IOS = "16.2"',
        'EXPECTED_RAW_PLUGIN_COUNT = 285',
        '"otoolPlatform": "2"',
        '"otoolPlatform": "7"',
        'if "cmd LC_RPATH" in layout',
        'frameworkCount',
    ]
    if (
        not all(marker in stager for marker in stager_markers)
        or "codesign" in stager
        or "os.environ" in stager
    ):
        fail("The iOS framework stager does not close relocation and tool inputs.")
    assembler = (root / "scripts/assemble_ios_xcframeworks.py").read_text(
        encoding="utf-8"
    )
    assembler_markers = [
        "EXPECTED_FRAMEWORK_COUNT",
        '"-create-xcframework"',
        "Frameworks/*.xcframework",
        'git_output(["status", "--porcelain"])',
        "evidenceSha256",
        "auditCandidate",
        "kmedia-vlc-{version}-ios-xcframeworks.zip",
    ]
    if (
        not all(marker in assembler for marker in assembler_markers)
        or "codesign" in assembler
        or "os.environ" in assembler
    ):
        fail("The iOS XCFramework assembler is not deterministic and fail-closed.")
    archive_verifier = (
        root / "scripts/verify_ios_xcframework_archive.py"
    ).read_text(encoding="utf-8")
    verifier_markers = [
        "safe_members",
        "LC_BUILD_VERSION",
        "LC_ID_DYLIB",
        "LC_RPATH",
        "IOS_16_2_0",
        "evidenceSha256",
        "allow_audit_candidate",
        "verify_podspec",
    ]
    if not all(marker in archive_verifier for marker in verifier_markers):
        fail("The iOS XCFramework archive verifier does not independently close the payload.")
    cmake = (root / "native/CMakeLists.txt").read_text(encoding="utf-8")
    cmake_markers = [
        'CMAKE_SYSTEM_NAME STREQUAL "iOS"',
        "if(NOT KMEDIAVLC_IOS)",
        "src/platform_renderer_stub.cpp",
        "target_link_libraries(kmediavlc_bridge PRIVATE ${CMAKE_DL_LIBS})",
    ]
    if not all(marker in cmake for marker in cmake_markers):
        fail("The iOS bridge must exclude JNI and the macOS renderer.")
    smoke_script = (root / "scripts/run_ios_simulator_smoke.sh").read_text(encoding="utf-8")
    smoke_source = (root / "scripts/ios-smoke/KMediaVlcSmoke.m").read_text(encoding="utf-8")
    smoke_markers = [
        '!= "87"',
        "simctl install",
        "simctl launch --terminate-running-process",
        "-target arm64-apple-ios16.2-simulator",
        "-Wl,-rpath,@executable_path/Frameworks",
    ]
    source_markers = [
        "KMEDIAVLC_CPU_PULL",
        "kmediavlc_player_open",
        "kmediavlc_player_acquire_latest_frame",
        "KMEDIAVLC_CPU_ADDRESS",
        "UIImagePNGRepresentation",
    ]
    if (
        not all(marker in smoke_script for marker in smoke_markers)
        or not all(marker in smoke_source for marker in source_markers)
    ):
        fail("The packaged iOS simulator CPU-pull playback gate is incomplete.")
    documentation = (root / "docs/IOS.md").read_text(encoding="utf-8")
    documentation_markers = [
        "iOS 16.2",
        "CPU_PULL",
        "84 playback modules",
        "87 dynamic frameworks",
        "XCFramework",
        "Publication gates still open",
        "not release-eligible",
    ]
    if not all(marker in documentation for marker in documentation_markers):
        fail("The iOS runtime documentation must remain exact and publication-fail-closed.")


def verify_linux_runtime_contract(root: Path) -> None:
    playback = load_json(root / "compliance/policy/linux-playback-modules.json")
    expected_targets = ["linux-x86_64", "linux-aarch64"]
    expected_families = {
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
    if (
        playback.get("schemaVersion") != 1
        or playback.get("targets") != expected_targets
        or playback.get("vlcRevision") != PINNED_REVISION
        or playback.get("reviewStatus")
        not in {"pending-elf-source-license-and-dmabuf-audit", "approved"}
        or playback.get("primaryLicenseSpdx") != "LGPL-2.1-or-later"
    ):
        fail("Linux playback module policy has an unsupported identity or review state.")
    families = playback.get("modulesByFamily")
    if not isinstance(families, dict) or set(families) != expected_families:
        fail("Linux playback module families are incomplete or overbroad.")
    modules: list[str] = []
    for family, names in families.items():
        if not isinstance(names, list) or names != sorted(names) or not names:
            fail(f"Linux playback module family is not a closed sorted list: {family}")
        modules.extend(names)
    required_modules = {
        "avcodec",
        "dav1d",
        "freetype",
        "gles2",
        "glinterop_sw",
        "glsampler_builtin",
        "gnutls",
        "https",
        "libass",
        "mkv",
        "mp4",
        "pulse",
        "vgl",
        "vmem",
    }
    forbidden_platform_modules = {
        "auhal",
        "audiounit_ios",
        "cvpx",
        "glinterop_cvpx",
        "securetransport",
        "videotoolbox",
    }
    if (
        len(modules) != 85
        or len(set(modules)) != 85
        or not required_modules.issubset(modules)
        or forbidden_platform_modules.intersection(modules)
        or any(
            not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]+", name)
            for name in modules
        )
    ):
        fail("Linux playback allowlist must contain its 85 unique platform modules.")
    expected_additional = {
        "mkv": ["MIT"],
        "opus": ["BSD-3-Clause"],
        "ts": ["BSD-3-Clause"],
    }
    if playback.get("additionalDirectSourceLicenses") != expected_additional:
        fail("Linux playback direct-source license exceptions changed without review.")

    binary = load_json(root / "compliance/policy/linux-binary-components.json")
    if (
        binary.get("schemaVersion") != 1
        or binary.get("targets") != expected_targets
        or binary.get("vlcRevision") != PINNED_REVISION
        or binary.get("reviewStatus")
        not in {"pending-link-command-and-license-audit", "approved"}
        or binary.get("buildOnlyContribPackages") != []
    ):
        fail("Linux binary component policy has an unsupported identity or review state.")
    expected_components = {
        "dav1d",
        "ffmpeg",
        "flac",
        "freetype",
        "fribidi",
        "gmp",
        "gnutls",
        "gnutls-libtasn1",
        "gnutls-libunistring",
        "gsm",
        "harfbuzz",
        "libass",
        "libdvbpsi",
        "libebml",
        "libjpeg-turbo",
        "libmatroska",
        "libogg",
        "libpng",
        "libvorbis",
        "libvpx",
        "libxml2",
        "nettle",
        "openjpeg",
        "opus",
        "soxr",
        "zlib",
    }
    components = binary.get("components")
    if (
        not isinstance(components, dict)
        or list(components) != sorted(components)
        or set(components) != expected_components
    ):
        fail("Linux binary components must match the closed playback dependency map.")
    for component_id, component_policy in components.items():
        if not re.fullmatch(r"[a-z0-9-]+", component_id) or not isinstance(
            component_policy, dict
        ):
            fail(f"Invalid Linux binary component: {component_id!r}")
        if set(component_policy) != {"version", "licenseSpdx", "sourceArchive"}:
            fail(f"Linux binary component fields are not closed: {component_id}")
        licenses = component_policy["licenseSpdx"]
        if (
            not isinstance(licenses, list)
            or licenses != sorted(set(licenses))
            or not licenses
            or any(license_id not in ALLOWED_LICENSES for license_id in licenses)
        ):
            fail(f"Linux binary component licenses are not canonical: {component_id}")
        if not isinstance(component_policy["version"], str) or not component_policy["version"]:
            fail(f"Linux binary component version is missing: {component_id}")
        if not re.fullmatch(
            r"[A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)",
            component_policy["sourceArchive"],
        ):
            fail(f"Linux binary component source archive is unsafe: {component_id}")
    expected_component_modules = {
        "avcodec",
        "dav1d",
        "flac",
        "freetype",
        "gnutls",
        "inflate",
        "jpeg",
        "libass",
        "mkv",
        "mp4",
        "ogg",
        "opus",
        "packetizer_avparser",
        "png",
        "soxr",
        "swscale",
        "ts",
        "vorbis",
        "vpx",
        "xml",
    }
    module_components = binary.get("moduleComponents")
    if (
        not isinstance(module_components, dict)
        or set(module_components) != expected_component_modules
    ):
        fail("Linux binary module/component closure changed without review.")
    referenced_components: set[str] = set()
    for module, component_ids in module_components.items():
        if (
            module not in modules
            or component_ids != sorted(set(component_ids))
            or not component_ids
        ):
            fail(f"Linux binary module components are not canonical: {module}")
        if any(component_id not in components for component_id in component_ids):
            fail(f"Linux binary module references an unknown component: {module}")
        referenced_components.update(component_ids)
    if binary.get("coreComponents") != []:
        fail("Linux core component closure changed without review.")
    expected_support_libraries = {
        "libvlc_pulse.so": {
            "licenseSpdx": ["LGPL-2.1-or-later"],
            "requiredByModules": ["pulse"],
            "sourceFiles": ["modules/audio_output/vlcpulse.c"],
        }
    }
    if binary.get("runtimeSupportLibraries") != expected_support_libraries:
        fail("Linux private runtime support libraries changed without review.")
    if referenced_components != set(components):
        fail("Linux binary component policy contains unused or missing components.")
    if binary.get("moduleAdditionalLicenses") != expected_additional:
        fail("Linux binary direct-source license exceptions changed without review.")
    expected_system_dependencies = [
        "libEGL.so.1",
        "libGLESv2.so.2",
        "libatomic.so.1",
        "libc.so.6",
        "libdl.so.2",
        "libdrm.so.2",
        "libfontconfig.so.1",
        "libgbm.so.1",
        "libgcc_s.so.1",
        "libm.so.6",
        "libpthread.so.0",
        "libpulse.so.0",
        "librt.so.1",
        "libstdc++.so.6",
    ]
    expected_target_system_dependencies = {
        "linux-x86_64": ["ld-linux-x86-64.so.2"],
        "linux-aarch64": ["ld-linux-aarch64.so.1"],
    }
    if (
        binary.get("allowedSystemDependencies") != expected_system_dependencies
        or binary.get("allowedSystemDependenciesByTarget")
        != expected_target_system_dependencies
        or binary.get("maximumSymbolVersions")
        != {"GLIBC": "2.39", "GLIBCXX": "3.4.33", "CXXABI": "1.3.15"}
    ):
        fail("Linux ELF dependency or symbol-version ceiling changed without review.")

    recipe = load_json(root / "build-recipes/linux.json")
    expected_contribs = [
        "ass",
        "dav1d",
        "dvbpsi",
        "ebml",
        "ffmpeg",
        "flac",
        "freetype2",
        "fribidi",
        "gnutls",
        "harfbuzz",
        "jpeg",
        "libxml2",
        "matroska",
        "ogg",
        "opus",
        "png",
        "soxr",
        "vorbis",
        "vpx",
        "zlib",
    ]
    expected_system_packages = [
        "egl",
        "fontconfig",
        "gbm",
        "glesv2",
        "libdrm",
        "libpulse",
    ]
    expected_resolved_contribs = sorted(
        expected_contribs + ["gmp", "gsm", "nettle", "openjpeg"]
    )
    if (
        recipe.get("schemaVersion") != 1
        or recipe.get("targets") != expected_targets
        or recipe.get("vlcRevision") != PINNED_REVISION
        or recipe.get("buildSystem") != "meson"
        or recipe.get("mesonVersion") != "1.10.0"
        or recipe.get("minimumGlibc") != "2.39"
        or recipe.get("buildMode") != "shared"
        or recipe.get("usesPrebuiltContribs") is not False
        or recipe.get("selectedContribPackages") != expected_contribs
        or recipe.get("resolvedContribPackages") != expected_resolved_contribs
        or recipe.get("generatedContribMetadataTarget") != "meson-machinefile"
        or recipe.get("systemBuildDependencies") != expected_system_packages
        or recipe.get("runtimeSupportLibraries") != ["libvlc_pulse.so"]
        or recipe.get("renderEngine") != "GLES2"
        or recipe.get("frameTransport") != "DMA_BUF"
        or recipe.get("requiredFrameDeliveryModes") != ["CPU_PULL", "GPU_PUSH"]
        or recipe.get("requiresClosedElfGraph") is not True
        or recipe.get("requiresRenderNodeDmaBufTest") is not True
        or recipe.get("requiresAcquireFenceTest") is not True
        or recipe.get("requiresReleaseFenceTest") is not True
        or recipe.get("requiresVrConsumerAcceptance") is not True
        or recipe.get("candidateReleaseEligible") is not False
    ):
        fail("The Linux source-build recipe is incomplete or release-open.")

    builder = (root / "scripts/build_vlc_linux.sh").read_text(encoding="utf-8")
    builder_markers = [
        'readonly PINNED_REVISION="b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"',
        'readonly PINNED_MESON_VERSION="1.10.0"',
        "--disable-all",
        "--disable-gpl",
        "--enable-gnutls",
        "--enable-gmp",
        "--enable-gsm",
        "--enable-nettle",
        "--enable-openjpeg",
        "make -j1 .zlib",
        "make -j1 .meson-machinefile",
        "--default-library=shared",
        "--wrap-mode=nodownload",
        "-Wl,-Bsymbolic",
        'readonly libvlc="$install_directory/lib/libvlc.so"',
        'readonly core="$install_directory/lib/libvlccore.so.9.0.0"',
        "-Dauto_features=disabled",
        "-Dlua=disabled",
        "-Dcss_engine=enabled",
        "-Dgles2=enabled",
        "-Dpulse=enabled",
        "--tags runtime",
        '"$source_directory/bin/cachegen.c"',
        "-Wl,-rpath,'$ORIGIN/../../lib'",
        "raw-plugin-files.txt",
    ]
    if not all(marker in builder for marker in builder_markers) or "--prefer-static" in builder:
        fail("The Linux build wrapper does not preserve the closed source recipe.")

    workflow = (root / ".github/workflows/linux-source-audit.yml").read_text(
        encoding="utf-8"
    )
    workflow_markers = [
        "ubuntu-24.04-arm",
        "linux-x86_64",
        "linux-aarch64",
        "persist-credentials: false",
        "Compile the Linux GBM EGL DMA-BUF bridge",
        "bash scripts/build_vlc_linux.sh",
        "Stage the closed runtime and play a real CPU frame",
        "python3 scripts/stage_vlc_linux_runtime.py",
        "--allow-audit-candidate",
        'LD_LIBRARY_PATH="$stage/bin"',
        "pinnedVideoLanFixturePublishesCpuPullFrame",
        "without retaining binaries",
    ]
    if not all(marker in workflow for marker in workflow_markers):
        fail("Linux validation does not cover both native architectures and real CPU playback.")
    if "upload-artifact" in workflow or "contents: write" in workflow:
        fail("Linux candidate validation must not publish or retain native payloads.")

    stager = (root / "scripts/stage_vlc_linux_runtime.py").read_text(encoding="utf-8")
    stager_markers = [
        "EXPECTED_SELECTED_PLUGIN_COUNT = 85",
        '"linux-x86_64": "Advanced Micro Devices X86-64"',
        '"linux-aarch64": "AArch64"',
        '"--set-soname"',
        '"--set-rpath"',
        'return "$ORIGIN/../../../bin" if role == "PLUGIN" else "$ORIGIN"',
        '(require_plain_file(install, "lib/libvlc.so"), "bin/libvlc.so.12", "LIBVLC")',
        'require_plain_file(install, "lib/libvlccore.so.9.0.0")',
        'require_plain_file(install, f"lib/{filename}")',
        'binary["runtimeSupportLibraries"]',
        "source = require_plain_file(plugin_root, filename)",
        'dependency not in allowed_system_dependencies',
        'binary["allowedSystemDependenciesByTarget"][args.target]',
        '"GNU_STACK"',
        '"GNU_RELRO"',
        '"Build ID:',
        '"--version-info"',
        '"LD_LIBRARY_PATH": str(install / "lib")',
        '"gpuPushEvidence": "pending-render-node-and-explicit-fence-test"',
        '"vrConsumerEvidence": "pending-kmediaplayer-projection-acceptance"',
    ]
    if not all(marker in stager for marker in stager_markers) or "os.environ" in stager:
        fail("The Linux stager does not close relocation, ELF, or pending GPU evidence.")

    renderer = (root / "native/src/linux_dmabuf_renderer.cpp").read_text(encoding="utf-8")
    renderer_markers = [
        "libvlc_video_engine_gles2",
        "EGL_PLATFORM_GBM_KHR",
        "DRM_FORMAT_ABGR8888",
        "gbm_bo_create_with_modifiers2",
        "eglQueryDmaBufFormatsEXT",
        "eglQueryDmaBufModifiersEXT",
        "EGL_EXT_image_dma_buf_import_modifiers",
        "EGL_SYNC_NATIVE_FENCE_FD_ANDROID",
        "eglDupNativeFenceFDANDROID",
        "release_surface_callback",
        "surface->retired = true",
        "KMEDIAVLC_DMABUF",
        "libvlc_video_transfer_func_SRGB",
    ]
    if not all(marker in renderer for marker in renderer_markers):
        fail("The Linux GBM/EGL DMA-BUF and explicit-fence ownership contract is incomplete.")

    inspector = (root / "native/src/linux_dmabuf_inspector.cpp").read_text(encoding="utf-8")
    inspector_markers = [
        "drmGetNodeTypeFromFd",
        "EGL_EXT_image_dma_buf_import_modifiers",
        "EGL_SYNC_NATIVE_FENCE_FD_ANDROID",
        "eglWaitSyncKHR",
        "eglDupNativeFenceFDANDROID",
        "glReadPixels",
        "DRM_FORMAT_ABGR8888",
        "DRM_FORMAT_MOD_INVALID",
    ]
    if not all(marker in inspector for marker in inspector_markers):
        fail("The Linux physical probe does not independently import DMA-BUFs and fences.")

    integration_test = (
        root
        / "runtime-desktop/src/test/java/io/github/shusek/kmediavlc/runtime/desktop/"
        "VlcDesktopPlayerIntegrationTest.java"
    ).read_text(encoding="utf-8")
    integration_markers = [
        "pinnedVideoLanFixtureImportsLinuxDmaBufsAndReturnsExplicitFences",
        'System.getProperty("kmediavlc.test.linuxRenderNode")',
        "NativeBridge.linuxDmaBufModifiers",
        "NativeBridge.inspectLinuxDmaBufFrame",
        "iteration == 2",
        "frame.release(VlcDesktopFrame.NO_FENCE)",
    ]
    if not all(marker in integration_test for marker in integration_markers):
        fail("The Linux hardware probe lost import, fence, or retirement coverage.")

    desktop_build = (root / "runtime-desktop/build.gradle.kts").read_text(encoding="utf-8")
    desktop_build_markers = [
        'providers.gradleProperty("kmediaVlcTestLinuxRenderNode")',
        'systemProperty("kmediavlc.test.linuxRenderNode", renderNode)',
    ]
    if not all(marker in desktop_build for marker in desktop_build_markers):
        fail("The Linux hardware probe render node is not forwarded to the test JVM.")

    hardware_workflow = (root / ".github/workflows/linux-hardware-probe.yml").read_text(
        encoding="utf-8"
    )
    hardware_workflow_markers = [
        "workflow_dispatch:",
        "tested_commit:",
        "options: [x64, ARM64]",
        'github.ref == \'refs/heads/codex/libvlc4-backend\'',
        'runs-on: [self-hosted, linux, "${{ inputs.architecture }}", kmediavlc-linux-gpu]',
        "pinnedVideoLanFixtureImportsLinuxDmaBufsAndReturnsExplicitFences",
        "Remove the unpublished candidate from the self-hosted runner",
    ]
    if not all(marker in hardware_workflow for marker in hardware_workflow_markers):
        fail("The manual Linux physical-probe workflow is incomplete or not fail-closed.")
    forbidden_hardware_workflow_markers = [
        "pull_request:",
        "push:",
        "upload-artifact",
        "${{ secrets.",
    ]
    if any(marker in hardware_workflow for marker in forbidden_hardware_workflow_markers):
        fail("The Linux physical probe must remain manual, secret-free, and artifact-free.")

    cmake = (root / "native/CMakeLists.txt").read_text(encoding="utf-8")
    cmake_markers = [
        'CMAKE_SYSTEM_NAME STREQUAL "Linux"',
        "src/linux_dmabuf_inspector.cpp",
        "src/linux_dmabuf_renderer.cpp",
        "PkgConfig::KMEDIAVLC_LINUX_GRAPHICS",
        "egl",
        "glesv2",
        "gbm",
        "libdrm",
        "-Wl,-z,noexecstack",
    ]
    if not all(marker in cmake for marker in cmake_markers):
        fail("The Linux renderer is not linked to its bounded hardened graphics graph.")

    documentation = (root / "docs/LINUX.md").read_text(encoding="utf-8")
    documentation_markers = [
        "glibc 2.39",
        "85-plugin",
        "DRM_FORMAT_ABGR8888",
        "EGL_SYNC_NATIVE_FENCE_ANDROID",
        "buffer is retired",
        "tone-maps",
        "VR",
        "Publication gates still open",
        "not release-eligible",
    ]
    if not all(marker in documentation for marker in documentation_markers):
        fail("The Linux candidate documentation no longer states its exact fail-closed contract.")


def verify_legal_files(root: Path) -> None:
    windows_binary = load_json(root / "compliance/policy/windows-x86_64-binary-components.json")
    macos_binary = load_json(root / "compliance/policy/macos-aarch64-binary-components.json")
    ios_binary = load_json(root / "compliance/policy/ios-binary-components.json")
    linux_binary = load_json(root / "compliance/policy/linux-binary-components.json")
    windows_components = windows_binary.get("components")
    macos_components = macos_binary.get("components")
    ios_components = ios_binary.get("components")
    linux_components = linux_binary.get("components")
    if (
        not isinstance(windows_components, dict)
        or not isinstance(macos_components, dict)
        or not isinstance(ios_components, dict)
        or not isinstance(linux_components, dict)
    ):
        fail("Legal notice component inventories are invalid.")
    policies = [windows_components, macos_components, ios_components, linux_components]
    all_component_ids = set().union(*(set(policy) for policy in policies))
    for component_id in all_component_ids:
        definitions = [policy[component_id] for policy in policies if component_id in policy]
        if any(definition != definitions[0] for definition in definitions[1:]):
            fail(f"Cross-platform component terms disagree: {component_id}")
    components = {
        **windows_components,
        **macos_components,
        **ios_components,
        **linux_components,
    }
    if set(components) != set(COMPONENT_NOTICE_FILES):
        fail("Legal notice mapping must cover the cross-platform component union exactly.")
    required = [
        root / "LICENSE",
        root / "NOTICE",
        root / "THIRD_PARTY_NOTICES.md",
        root / "LICENSES/LGPL-2.1.txt",
        root / "LICENSES/LGPL-3.0.txt",
        root / "LICENSES/ISC-kmediavlc-client-api.txt",
        root / "gradle/wrapper/LICENSE",
    ]
    required.extend(root / "LICENSES" / name for name in set(COMPONENT_NOTICE_FILES.values()))
    missing = [str(path.relative_to(root)) for path in required if not path.is_file() or path.stat().st_size < 100]
    if missing:
        fail("Missing or truncated legal files: " + ", ".join(missing))

    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    if windows_binary.get("toolchainImage") not in notices:
        fail("Third-party notices omit the pinned Windows toolchain.")
    macos_toolchain = macos_binary.get("toolchain", {})
    macos_toolchain_notice = (
        f"Xcode {macos_toolchain.get('xcodeVersion')} "
        f"({macos_toolchain.get('xcodeBuild')})"
    )
    if macos_toolchain_notice not in notices:
        fail("Third-party notices omit the pinned macOS toolchain.")
    ios_toolchain = ios_binary.get("toolchain", {})
    ios_toolchain_notice = (
        f"Xcode {ios_toolchain.get('xcodeVersion')} "
        f"({ios_toolchain.get('xcodeBuild')})"
    )
    if ios_toolchain_notice not in notices:
        fail("Third-party notices omit the pinned iOS toolchain.")
    for component_id, component in components.items():
        licenses = " AND ".join(component["licenseSpdx"])
        row = f"| {component_id} | {component['version']} | {licenses} |"
        expected = (
            row,
            f"`{component['sourceArchive']}`",
            f"`LICENSES/{COMPONENT_NOTICE_FILES[component_id]}`",
        )
        if any(value not in notices for value in expected):
            fail(f"Third-party notices omit the reviewed component terms: {component_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    verify_spdx(root)
    verify_no_native_payload(root)
    verify_policy(root)
    verify_pin_occurrences(root)
    verify_macos_transport_contract(root)
    verify_ios_runtime_contract(root)
    verify_linux_runtime_contract(root)
    verify_legal_files(root)
    print("KMediaVlc source and licensing policy verified.")


if __name__ == "__main__":
    main()
