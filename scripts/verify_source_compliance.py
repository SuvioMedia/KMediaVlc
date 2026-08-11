# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PINNED_REVISION = "b5536cdea24b313ba9215eacfbd7fa3295d7f3ee"
PINNED_VERSION = "4.0.0-dev"
PINNED_LIBVLCJNI_REVISION = "a8d53a9151d7e4a9a5dfd0a5eb1cd92669afdc21"
ALLOWED_LICENSES = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "BSL-1.0",
    "FTL",
    "IJG",
    "ISC",
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
    legacy: list[str] = []
    legacy_identifier = "LicenseRef-KMediaVlc-" + "Proprietary"
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix.lower() not in SPDX_EXTENSIONS:
            continue
        head = path.read_text(encoding="utf-8", errors="strict")[:4096]
        if "SPDX-License-Identifier:" not in head:
            missing.append(relative.as_posix())
        if legacy_identifier in head:
            legacy.append(relative.as_posix())
    if missing:
        fail("Files without SPDX identifiers: " + ", ".join(sorted(missing)))
    if legacy:
        fail("Files retain the obsolete proprietary license identifier: " + ", ".join(sorted(legacy)))


def verify_platform_project_isolation(root: Path) -> None:
    settings = (root / "settings.gradle.kts").read_text(encoding="utf-8")
    settings_markers = [
        'providers.gradleProperty("kmediaVlcDesktopOnly")',
        'configuredValue == "true" || configuredValue == "false"',
        'if (!desktopOnly) {',
        'include(":runtime-android")',
    ]
    if not all(marker in settings for marker in settings_markers):
        fail("The unified Gradle graph does not isolate desktop and Android projects explicitly.")

    desktop_workflows = {
        ".github/workflows/ci.yml": 3,
        ".github/workflows/linux-hardware-probe.yml": 2,
        ".github/workflows/linux-source-audit.yml": 2,
        ".github/workflows/native-audit.yml": 3,
        ".github/workflows/release.yml": 1,
    }
    for relative, expected_count in desktop_workflows.items():
        workflow = (root / relative).read_text(encoding="utf-8")
        if workflow.count("-PkmediaVlcDesktopOnly=true") != expected_count:
            fail(f"Desktop workflow does not isolate the Android Gradle project: {relative}")

    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if ci.count("-PkmediaVlcDesktopOnly=false") != 1:
        fail("Android CI must include the Android project explicitly.")


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
    android_runtime = (
        root
        / "runtime-android/src/main/java/io/github/shusek/kmediavlc/runtime/android/"
        "VlcAndroidRuntime.java"
    ).read_text(encoding="utf-8")
    if PINNED_REVISION not in parser or PINNED_REVISION not in notices:
        fail("Pinned VLC revision is not consistent across runtime code and notices.")
    if PINNED_VERSION not in parser:
        fail("Pinned VLC version is not enforced by the runtime parser.")
    if PINNED_REVISION not in android_runtime or PINNED_VERSION not in android_runtime:
        fail("Android runtime identity differs from the pinned VLC source.")
    if PINNED_LIBVLCJNI_REVISION not in android_runtime or PINNED_LIBVLCJNI_REVISION not in notices:
        fail("Android source-build tooling pin is inconsistent.")


def verify_android_contract(root: Path) -> None:
    component = load_json(root / "compliance/components/libvlcjni.json")
    expected_component = {
        "schemaVersion": 1,
        "id": "videolan-libvlcjni-buildsystem",
        "version": "4.0.0-eap29-build-input",
        "revision": PINNED_LIBVLCJNI_REVISION,
        "source": "https://code.videolan.org/videolan/libvlcjni.git",
        "projectLicenseSpdx": "LGPL-2.1-or-later",
        "distributedJavaWrapper": False,
        "distributedJniWrapper": False,
        "usedForSourceBuild": True,
    }
    if component != expected_component:
        fail("The Android libvlcjni build-input component is not closed.")

    recipe = load_json(root / "build-recipes/android.json")
    expected_keys = {
        "schemaVersion", "vlcRevision", "libvlcjniRevision", "vlcSource",
        "libvlcjniSource", "publicationTargets", "ndkVersion", "vlcAndroidApi",
        "clientMinSdk", "libvlcBuildArguments", "contribLicenseProfile",
        "renderEngine", "packagedLibraries", "excludedLibraries",
        "requiredPlaybackModules", "vlcPatch", "libvlcjniPatch", "disabledVlcFeatures",
        "usesPrebuiltContribs", "usesPublishedLibVlcAar",
        "requiresCoreJniOnLoadFirst", "requiresPerFileInventory",
        "requiresModuleLicenseAudit", "requiresCompiledModuleLicenseAudit",
        "requiresLinkerMapAudit", "legalEvidenceBundle",
        "requiresIdenticalAbiComponentEvidence",
        "requiresApprovedLegalEvidenceForPublication",
        "ndkSourceStatus", "requiresNdkRuntimeSourcePackage", "ndkSourcePackagePolicy",
        "requiresIndependentNdkSourceVerification",
        "correspondingSourcePackagePolicy", "requiresCompleteCorrespondingSourcePackage",
        "requiresIndependentCorrespondingSourceVerification",
        "requiresDeviceSurfaceLifecycleTest", "forbidsStockNightly",
        "candidateReleaseEligible",
    }
    if set(recipe) != expected_keys or recipe.get("schemaVersion") != 1:
        fail("The Android source-build recipe fields are not closed.")
    if (
        recipe.get("vlcRevision") != PINNED_REVISION
        or recipe.get("libvlcjniRevision") != PINNED_LIBVLCJNI_REVISION
        or recipe.get("ndkVersion") != "29.0.14206865"
        or recipe.get("vlcAndroidApi") != 21
        or recipe.get("clientMinSdk") != 28
    ):
        fail("The Android source-build recipe identity or toolchain changed.")
    if recipe.get("publicationTargets") != ["android-arm64-v8a", "android-armeabi-v7a"]:
        fail("The Android ABI set must remain closed to ARM64 and ARMv7.")
    if recipe.get("libvlcBuildArguments") != [
        "--release", "--static-cpp", "--license", "a", "--no-jni"
    ]:
        fail("The Android build lost its source/LGPL/static-C++ contract.")
    if (
        recipe.get("contribLicenseProfile") != "LGPL-2.1-plus-ad-clauses"
        or recipe.get("renderEngine") != "ANATIVEWINDOW"
        or recipe.get("packagedLibraries") != ["libkmediavlc_android.so", "libvlc.so"]
        or recipe.get("excludedLibraries") != ["libc++_shared.so", "libvlcjni.so"]
    ):
        fail("The Android rendering or native library inventory changed.")
    expected_playback_modules = [
        "adaptive", "android_aaudio", "android_audiodevice", "android_audiotrack",
        "android_display", "android_window", "avcodec", "egl_android", "filesystem",
        "http", "mediacodec", "mkv", "mp4", "opensles_android",
    ]
    if (
        recipe.get("requiredPlaybackModules") != expected_playback_modules
        or recipe.get("vlcPatch")
        != "patches/vlc/0001-android-external-anw-direct-mediacodec.patch"
        or recipe.get("libvlcjniPatch")
        != "patches/libvlcjni/0001-kmediavlc-android-static-module-policy.patch"
        or recipe.get("disabledVlcFeatures") != ["bluray"]
    ):
        fail("The Android playback module, disabled feature, or source patch policy changed.")
    required_true = [
        "requiresCoreJniOnLoadFirst", "requiresPerFileInventory",
        "requiresModuleLicenseAudit", "requiresCompiledModuleLicenseAudit",
        "requiresLinkerMapAudit", "requiresIdenticalAbiComponentEvidence",
        "requiresApprovedLegalEvidenceForPublication", "requiresNdkRuntimeSourcePackage",
        "requiresIndependentNdkSourceVerification",
        "requiresCompleteCorrespondingSourcePackage",
        "requiresIndependentCorrespondingSourceVerification",
        "requiresDeviceSurfaceLifecycleTest",
        "forbidsStockNightly",
    ]
    if any(recipe.get(key) is not True for key in required_true):
        fail("The Android audit requirements were weakened.")
    if recipe.get("legalEvidenceBundle") != "legal/android-static-legal.json":
        fail("The Android hash-bound legal evidence path changed.")
    if (
        recipe.get("ndkSourcePackagePolicy")
        != "compliance/policy/android-static-components.json"
    ):
        fail("The Android NDK source package is not bound to the static policy.")
    if (
        recipe.get("correspondingSourcePackagePolicy")
        != "compliance/policy/android-corresponding-source.json"
    ):
        fail("The Android corresponding-source package is not bound to its closed policy.")
    if (
        recipe.get("ndkSourceStatus")
        != "exact-source-revisions-recorded-source-package-pending"
    ):
        fail("The Android NDK source-package gate changed without verification.")
    if (
        recipe.get("usesPrebuiltContribs") is not False
        or recipe.get("usesPublishedLibVlcAar") is not False
        or recipe.get("candidateReleaseEligible") is not False
    ):
        fail("The unaudited Android candidate must remain release-ineligible and source-built.")

    static_policy = load_json(
        root / "compliance/policy/android-static-components.json"
    )
    expected_static_keys = {
        "schemaVersion", "target", "vlcRevision", "ndkRevision", "reviewStatus",
        "contribComponents", "candidateLicenseSpdx", "licenseEvidence", "contribArchives",
        "ndkComponents", "ndkSourceInputs", "ndkSourcePackage", "ndkReleaseProvenance",
        "ndkArchiveTemplates", "ndkArchiveSourcePaths",
    }
    if (
        set(static_policy) != expected_static_keys
        or static_policy.get("schemaVersion") != 1
        or static_policy.get("target") != "android-arm"
        or static_policy.get("vlcRevision") != PINNED_REVISION
        or static_policy.get("ndkRevision") != "29.0.14206865"
        or static_policy.get("reviewStatus")
        != "source-mapped-license-and-notice-review-pending"
    ):
        fail("The Android static component policy identity or review state is invalid.")
    contrib_components = static_policy.get("contribComponents")
    if (
        not isinstance(contrib_components, dict)
        or list(contrib_components) != sorted(contrib_components)
        or len(contrib_components) != 54
    ):
        fail("The Android static policy must contain exactly 54 sorted contrib components.")
    source_archive_count = 0
    for component_id, component_policy in contrib_components.items():
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]+", component_id):
            fail(f"Unsafe Android contrib component identifier: {component_id!r}")
        if not isinstance(component_policy, dict) or set(component_policy) != {
            "version", "sourceArchives"
        }:
            fail(f"Android contrib component fields are not closed: {component_id}")
        sources = component_policy["sourceArchives"]
        if (
            not isinstance(component_policy["version"], str)
            or not component_policy["version"]
            or not isinstance(sources, list)
            or sources != sorted(set(sources))
            or not sources
            or any(
                not isinstance(source, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+_-]+\.tar\.(?:gz|xz|bz2)", source)
                for source in sources
            )
        ):
            fail(f"Android contrib source mapping is unsafe: {component_id}")
        source_archive_count += len(sources)
    if source_archive_count != 55:
        fail("The Android contrib source closure must contain exactly 55 source archives.")

    source_archives = {
        source
        for component_policy in contrib_components.values()
        for source in component_policy["sourceArchives"]
    }
    candidate_licenses = static_policy.get("candidateLicenseSpdx")
    if (
        not isinstance(candidate_licenses, dict)
        or list(candidate_licenses) != sorted(candidate_licenses)
        or set(candidate_licenses) != set(contrib_components)
    ):
        fail("Android candidate SPDX mapping must cover all contrib components exactly.")
    for component_id, licenses in candidate_licenses.items():
        if (
            not isinstance(licenses, list)
            or licenses != sorted(set(licenses))
            or not licenses
            or any(
                not isinstance(license_id, str)
                or not re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9.+-]*(?: WITH [A-Za-z0-9][A-Za-z0-9.+-]*)?",
                    license_id,
                )
                or license_id.startswith(("GPL-", "AGPL-", "LicenseRef-NonFree", "unknown"))
                for license_id in licenses
            )
        ):
            fail(f"Android candidate SPDX mapping is unsafe: {component_id}")
    expected_candidate_licenses = {
        "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0", "CC0-1.0", "FTL",
        "IJG", "ISC", "LGPL-2.0-or-later", "LGPL-2.1-only", "LGPL-2.1-or-later",
        "Libpng-2.0", "LicenseRef-Public-Domain", "MIT", "TU-Berlin-1.0",
        "Unicode-DFS-2016", "Zlib",
    }
    if {license_id for licenses in candidate_licenses.values() for license_id in licenses} != (
        expected_candidate_licenses
    ):
        fail("Android contrib candidate SPDX set changed without linked-member review.")
    license_evidence = static_policy.get("licenseEvidence")
    if (
        not isinstance(license_evidence, dict)
        or list(license_evidence) != sorted(license_evidence)
        or set(license_evidence) != source_archives
    ):
        fail("Android license evidence must cover all 55 source archives exactly.")
    license_evidence_count = 0
    for source, paths in license_evidence.items():
        if not isinstance(paths, list) or paths != sorted(set(paths)) or not paths:
            fail(f"Android license evidence is not canonical: {source}")
        for value in paths:
            if not isinstance(value, str):
                fail(f"Android license evidence path is unsafe: {source}")
            parts = value.split("/")
            if (
                value.startswith("/")
                or "//" in value
                or any(
                    not part
                    or part in {".", ".."}
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", part)
                    for part in parts
                )
            ):
                fail(f"Android license evidence path is unsafe: {source}!/{value}")
        license_evidence_count += len(paths)
    if license_evidence_count != 83:
        fail("Android license evidence must contain the exact 83 selected source records.")

    contrib_archives = static_policy.get("contribArchives")
    if (
        not isinstance(contrib_archives, dict)
        or list(contrib_archives) != sorted(contrib_archives)
        or len(contrib_archives) != 62
        or set(contrib_archives.values()) != set(contrib_components)
    ):
        fail("The Android contrib link graph must be an exact sorted 62-archive map.")
    for archive, component_id in contrib_archives.items():
        if (
            not re.fullmatch(r"vlc-contrib/lib/lib[A-Za-z0-9_+.-]+\.a", archive)
            or component_id not in contrib_components
        ):
            fail(f"Unsafe Android contrib archive mapping: {archive!r}")

    ndk_components = static_policy.get("ndkComponents")
    expected_ndk_component = {
        "android-ndk-llvm-runtime": {
            "version": "29.0.14206865",
            "candidateLicenseSpdx": ["Apache-2.0 WITH LLVM-exception"],
            "evidenceFiles": ["NOTICE", "NOTICE.toolchain", "source.properties"],
            "toolchainEvidenceFiles": ["AndroidVersion.txt", "clang_source_info.md"],
            "sourceInputs": ["llvm-android-build", "llvm-project"],
            "sourceStatus": "exact-source-revisions-recorded-source-package-pending",
        }
    }
    if ndk_components != expected_ndk_component:
        fail("The Android NDK static runtime component is not closed.")
    expected_ndk_source_inputs = {
        "llvm-android-build": {
            "repository": "https://android.googlesource.com/toolchain/llvm_android",
            "revision": "1dab3288f660d43a6cb2479107e2b54b3ab0a2a1",
            "tree": "9cf89bb8f12fb9e993e81d2ee2d43f2bc8819d53",
            "role": "android-runtime-build-and-patch-set",
            "requiredPaths": [
                "do_build.py",
                "patches",
                "src/llvm_android/android_version.py",
                "src/llvm_android/builders.py",
            ],
        },
        "llvm-project": {
            "repository": "https://android.googlesource.com/toolchain/llvm-project",
            "revision": "386af4a5c64ab75eaee2448dc38f2e34a40bfed0",
            "tree": "a49e40b73bcc972355bbf00df0d85d00312a625f",
            "role": "linked-runtime-source",
            "requiredPaths": [
                "compiler-rt/lib/builtins",
                "libcxx",
                "libcxxabi",
                "libunwind",
                "runtimes",
            ],
        },
    }
    if static_policy.get("ndkSourceInputs") != expected_ndk_source_inputs:
        fail("The Android NDK source revisions and trees are not closed.")
    expected_ndk_source_package = {
        "archiveRoot": "android-ndk-runtime-source",
        "format": "deterministic-tar-gzip-v1",
        "verifiedSourceStatus": "corresponding-source-mapped",
        "sources": {
            "llvm-android-build": {"scope": "complete-tree", "paths": []},
            "llvm-project": {
                "scope": "selected-subtrees",
                "paths": [
                    "LICENSE.TXT",
                    "README.md",
                    "cmake",
                    "compiler-rt",
                    "libcxx",
                    "libcxxabi",
                    "libunwind",
                    "llvm/cmake",
                    "llvm/include",
                    "llvm/utils/lit",
                    "runtimes",
                    "third-party",
                ],
            },
        },
    }
    if static_policy.get("ndkSourcePackage") != expected_ndk_source_package:
        fail("The Android NDK source package selection is not closed.")
    expected_ndk_release = {
        "releaseName": "r29",
        "clangVersion": "21.0.0",
        "clangRevision": "r563880c",
        "ndkRepository": "https://android.googlesource.com/platform/ndk",
        "ndkTag": "ndk-r29",
        "ndkTagObject": "5199c56421d79df5099aad8e32e32c101ff85cca",
        "ndkCommit": "196e0661200bad5361340700fea67be12e1f1684",
        "manifestRepository": "https://android.googlesource.com/platform/manifest",
        "manifestTagObject": "5d4df6d77b33dc6d31576a66a8ff283c8825493f",
        "manifestCommit": "82eb8adcaafe02dce4e462db2379fad3ea0b54d8",
        "prebuiltTags": {
            "darwin-x86_64": {
                "repository": (
                    "https://android.googlesource.com/platform/prebuilts/clang/host/darwin-x86"
                ),
                "tagObject": "c547cdbfbec71e85920c1f0976e18defc01a0b5b",
                "commit": "2ede290b28d234595fcc23207c633961690c57ba",
            },
            "linux-x86_64": {
                "repository": (
                    "https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86"
                ),
                "tagObject": "be61f23178d3459a558b45dd0df4304b0fda6b26",
                "commit": "568b941cf0c249b9c2a1f853e94a29f0e6291c59",
            },
        },
    }
    if static_policy.get("ndkReleaseProvenance") != expected_ndk_release:
        fail("The Android NDK r29 release/prebuilt provenance is not closed.")
    expected_ndk_templates = {
        "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
        "libclang_rt.builtins-{builtinsArch}-android.a",
        "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
        "{runtimeArch}/libunwind.a",
        "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
        "libc++_static.a",
        "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
        "libc++abi.a",
    }
    ndk_templates = static_policy.get("ndkArchiveTemplates")
    if (
        not isinstance(ndk_templates, dict)
        or list(ndk_templates) != sorted(ndk_templates)
        or set(ndk_templates) != expected_ndk_templates
        or set(ndk_templates.values()) != set(ndk_components)
    ):
        fail("The Android NDK link graph must contain the exact four runtime archives.")
    expected_ndk_source_paths = {
        "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
        "libclang_rt.builtins-{builtinsArch}-android.a": [
            "llvm-project/compiler-rt/lib/builtins"
        ],
        "ndk/toolchains/llvm/prebuilt/{hostTag}/lib/clang/21/lib/linux/"
        "{runtimeArch}/libunwind.a": [
            "llvm-project/libunwind",
            "llvm-project/runtimes",
        ],
        "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
        "libc++_static.a": ["llvm-project/libcxx", "llvm-project/runtimes"],
        "ndk/toolchains/llvm/prebuilt/{hostTag}/sysroot/usr/lib/{targetTuple}/"
        "libc++abi.a": ["llvm-project/libcxxabi", "llvm-project/runtimes"],
    }
    ndk_source_paths = static_policy.get("ndkArchiveSourcePaths")
    if (
        not isinstance(ndk_source_paths, dict)
        or list(ndk_source_paths) != sorted(ndk_source_paths)
        or ndk_source_paths != expected_ndk_source_paths
        or set(ndk_source_paths) != set(ndk_templates)
    ):
        fail("The Android NDK archive-to-source path map is not closed.")

    corresponding_policy = load_json(
        root / "compliance/policy/android-corresponding-source.json"
    )
    expected_corresponding_policy = {
        "schemaVersion": 1,
        "target": "android-arm",
        "archiveRoot": "android-corresponding-source",
        "format": "deterministic-tar-gzip-v1",
        "verifiedClosureStatus": "complete-source-and-relink-inputs-packaged",
        "sourceInputs": {
            "kmediavlc": {
                "repository": "https://github.com/SuvioMedia/KMediaVlc.git",
                "revisionBinding": "tested-commit",
                "scope": "complete-tree",
                "requiredPaths": [
                    "build-recipes/android.json",
                    "compliance/policy/android-corresponding-source.json",
                    "compliance/policy/android-static-components.json",
                    "docs/ANDROID.md",
                    "docs/RELINKING.md",
                    "native/android/CMakeLists.txt",
                    "native/android/kmediavlc_android_jni.cpp",
                    "patches/libvlcjni/0001-kmediavlc-android-static-module-policy.patch",
                    "patches/vlc/0001-android-external-anw-direct-mediacodec.patch",
                    "runtime-android/build.gradle.kts",
                    "scripts/build_vlc_android.sh",
                    "scripts/create_android_link_audit.py",
                    "scripts/package_android_corresponding_source.py",
                    "scripts/package_android_ndk_source.py",
                    "scripts/verify_android_corresponding_source_archive.py",
                    "scripts/verify_android_ndk_source_archive.py",
                ],
            },
            "libvlcjni": {
                "repository": "https://code.videolan.org/videolan/libvlcjni.git",
                "revision": PINNED_LIBVLCJNI_REVISION,
                "tree": "beed578662d1b9c4777bd68c628a3908ed1a1164",
                "scope": "complete-tree",
                "requiredPaths": [
                    "LICENSE",
                    "buildsystem/compile-libvlc.sh",
                    "libvlc/jni/libvlc.mk",
                    "libvlc/jni/libvlcjni.mk",
                ],
            },
            "vlc": {
                "repository": "https://code.videolan.org/videolan/vlc.git",
                "revision": PINNED_REVISION,
                "tree": "d796ecf4915b8e221bc973babcdbd3404ed3c957",
                "scope": "complete-tree",
                "requiredPaths": [
                    "COPYING",
                    "contrib/src",
                    "include/vlc/libvlc.h",
                    "lib/meson.build",
                    "meson.build",
                    "modules",
                    "src/meson.build",
                ],
            },
        },
        "contribSourceArchives": {
            "componentPolicy": "compliance/policy/android-static-components.json",
            "archiveDirectory": "sources/vlc-contrib-tarballs",
            "archiveCount": 55,
        },
        "ndkSourcePackage": {
            "componentPolicy": "compliance/policy/android-static-components.json",
            "archivePath": "source-packages/android-ndk-runtime-source.tar.gz",
            "archiveRoot": "android-ndk-runtime-source",
            "format": "deterministic-tar-gzip-v1",
            "requiresIndependentVerification": True,
        },
        "buildEvidence": {
            "legalManifestPath": "build-evidence/android-static-legal.json",
            "linkAudits": {
                "android-arm64-v8a": "build-evidence/link-audits/android-arm64-v8a.json",
                "android-armeabi-v7a": "build-evidence/link-audits/android-armeabi-v7a.json",
            },
        },
        "generatedFiles": ["REBUILD.md", "SOURCE-SHA256SUMS"],
    }
    if corresponding_policy != expected_corresponding_policy:
        fail("The complete Android corresponding-source package policy is not closed.")

    bridge = (root / "native/android/kmediavlc_android_jni.cpp").read_text(encoding="utf-8")
    bridge_markers = [
        "libvlc_video_set_anw_callbacks",
        "ANativeWindow_fromSurface",
        "ANativeWindow_acquire",
        "ANativeWindow_release",
        "SurfaceBinding",
        '"--keystore=memory"',
        'libvlc_video_engine_disable',
        'libvlc_media_add_option(media, ":no-hw-dec")',
        "capture_selected_tracks",
        "restore_selected_tracks",
        "JNI_OnLoad",
        "current == kStateEnded || current == kStateError",
    ]
    if not all(marker in bridge for marker in bridge_markers):
        fail("The Android ANativeWindow ownership or playback bridge is incomplete.")
    if any(marker in bridge for marker in ["setenv(", "getenv(", "putenv("]):
        fail("The Android bridge must not read or mutate process environment variables.")

    cmake = (root / "native/android/CMakeLists.txt").read_text(encoding="utf-8")
    cmake_markers = [
        'ANDROID_STL STREQUAL "c++_static"',
        "KMEDIAVLC_ANDROID_FAKE_LIBVLC",
        "tests/fake_libvlc.cpp",
        "-Wl,-z,max-page-size=16384",
        "KMEDIAVLC_VLC_SOURCE_DIR",
    ]
    if not all(marker in cmake for marker in cmake_markers):
        fail("The Android NDK build does not close its ABI, STL, and page-size policy.")

    fixture = (root / "native/android/tests/fake_libvlc.cpp").read_text(encoding="utf-8")
    if (
        "libvlc_video_set_output_callbacks" not in fixture
        or "libvlc_video_engine_anw" not in fixture
        or "JNI_OnLoad" not in fixture
    ):
        fail("The Android pinned-header fixture does not exercise the required core ABI.")

    runtime = (
        root
        / "runtime-android/src/main/java/io/github/shusek/kmediavlc/runtime/android/"
        "VlcAndroidRuntime.java"
    ).read_text(encoding="utf-8")
    player_api = (
        root
        / "runtime-android/src/main/java/io/github/shusek/kmediavlc/runtime/android/"
        "VlcAndroidPlayer.java"
    ).read_text(encoding="utf-8")
    vlc_load = runtime.find('System.loadLibrary("vlc")')
    bridge_load = runtime.find('System.loadLibrary("kmediavlc_android")')
    if vlc_load < 0 or bridge_load <= vlc_load or 'System.loadLibrary("vlcjni")' in runtime:
        fail("Android must invoke the VLC core JNI_OnLoad before loading its narrow bridge.")
    if "Map.of(" in player_api or ".isBlank()" in player_api:
        fail("The minSdk 28 API must not call Java library methods introduced in API 30+.")

    instrumented_playback = (
        root
        / "runtime-android/src/androidTest/java/io/github/shusek/kmediavlc/runtime/android/"
        "VlcAndroidPlaybackInstrumentedTest.java"
    ).read_text(encoding="utf-8")
    if not all(
        marker in instrumented_playback
        for marker in [
            "VlcAndroidPlaybackState.ENDED",
            '"the end-of-stream state"',
            "automaticDecodePreservesHdr10SurfaceSignal",
            "BT.2020/PQ",
        ]
    ):
        fail("The Android bundled playback gate does not preserve end-of-stream state.")

    device_smoke = (root / "scripts/run_android_device_smoke.sh").read_text(
        encoding="utf-8"
    )
    device_results = (
        root / "scripts/verify_android_device_smoke_results.py"
    ).read_text(encoding="utf-8")
    device_smoke_markers = [
        "status --porcelain",
        "ro.kernel.qemu",
        "ro.boot.qemu",
        "ro.hardware",
        "ANDROID_SERIAL=",
        ":runtime-android:connectedDebugAndroidTest",
        "android.testInstrumentationRunnerArguments.class",
        "VlcAndroidPlaybackInstrumentedTest",
        "the KMediaVlc Android test package already exists",
        "adb_device uninstall",
        "verify_android_device_smoke_results.py",
    ]
    device_result_markers = [
        "REQUIRED_LIBRARIES",
        "payload_tree",
        "physical-device result is not an exact three-test pass",
        '"(avd)"',
        '"qemuRejected": True',
        '"treeSha256"',
        '"testResultsSha256"',
    ]
    if (
        not all(marker in device_smoke for marker in device_smoke_markers)
        or not all(marker in device_results for marker in device_result_markers)
    ):
        fail("The physical Android playback acceptance gate is incomplete.")

    android_build = (root / "runtime-android/build.gradle.kts").read_text(encoding="utf-8")
    gradle_markers = [
        'setOf("arm64-v8a", "armeabi-v7a")',
        'setOf("libkmediavlc_android.so", "libvlc.so")',
        'minSdk = 28',
        'require(values == expectedManifest("true"))',
        "kmediaVlcAndroidNativePayloadDirectory",
        "AndroidLegalEvidence.read",
        "android-static-legal.json",
        "abstract val legalDirectory: DirectoryProperty",
        'legalDirectory.set(rootProject.layout.projectDirectory.dir("LICENSES"))',
        "assets/kmediavlc/legal/ANDROID_STATIC/",
        "does not bind the packaged libvlc.so",
        "Publishing requires approved hash-bound Android legal evidence.",
        "Publishing requires the NDK runtime source package to match its recorded revisions.",
        "kmediaVlcAndroidNdkSourceArchive",
        "verify_android_ndk_source_archive.py",
        'classifier = "android-ndk-source"',
        "kmediaVlcAndroidCorrespondingSourceArchive",
        "verifyAndroidCorrespondingSourceArchive",
        "verify_android_corresponding_source_archive.py",
        "Publishing requires independently verified complete Android corresponding source.",
        'classifier = "corresponding-source"',
    ]
    if not all(marker in android_build for marker in gradle_markers):
        fail("The Android AAR payload or publication gate is incomplete.")

    builder = (root / "scripts/build_vlc_android.sh").read_text(encoding="utf-8")
    builder_markers = [
        "compile-libvlc.sh",
        "--release --static-cpp --license a --no-jni",
        "APP_LDFLAGS=",
        "ANDROID_NDK=",
        "create_android_link_audit.py",
        "stage_android_legal_evidence.py",
        "libvlcjni-kmediavlc",
        "0001-kmediavlc-android-static-module-policy.patch",
        "0001-android-external-anw-direct-mediacodec.patch",
        "git -C \"$vlc_source\" apply",
        "git -C \"$vlc_source\" apply --reverse",
        "upstream process-path line suppressed",
        "libkmediavlc_android.so",
        "releaseEligible=false",
        "0x4000",
        '--libvlc "$destination/libvlc.so"',
        '--output "$output_directory/legal"',
    ]
    if not all(marker in builder for marker in builder_markers):
        fail("The Android source builder does not produce a fail-closed candidate.")
    if builder.find('"$strip_executable" --strip-unneeded "$destination/libvlc.so"') >= builder.find(
        '--libvlc "$destination/libvlc.so"'
    ):
        fail("The Android link audit must hash the final stripped payload library.")

    audit_generator = (root / "scripts/create_android_link_audit.py").read_text(
        encoding="utf-8"
    )
    audit_markers = [
        "candidate-source-mapped-license-review-pending",
        '"VLC_MODULE"',
        '"VLC_CORE"',
        '"CONTRIB"',
        '"NDK_TOOLCHAIN"',
        "LGPL_TEXT",
        "GPL_TEXT",
        "libvlc_video_set_output_callbacks",
        "FORBIDDEN_NEEDED",
        '"loadAlignment"',
        '"libvlcjniPatch"',
        '"vlcPatch"',
        '"effectiveLicenseSpdx"',
        '"staticComponents"',
        '"candidateLicenseSpdx"',
        '"licenseEvidence"',
        "android-static-components.json",
        "staticComponentPolicy",
        "EXPECTED_NDK_SOURCE_PACKAGE",
    ]
    if not all(marker in audit_generator for marker in audit_markers):
        fail("The Android exact-link audit generator is incomplete.")

    legal_stager = (root / "scripts/stage_android_legal_evidence.py").read_text(
        encoding="utf-8"
    )
    legal_stager_markers = [
        "candidate-linked-member-review-pending",
        "pending-linked-member-review",
        "read_archive_member",
        "Android ABI audits do not have identical static component evidence.",
        '"effectiveLicenseSpdx": None',
        '"candidateLicenseInventorySpdx"',
        '"sourceInputs"',
        "NDK_SOURCE_PACKAGE",
        "exact-source-revisions-recorded-source-package-pending",
        "clang_source_info.md",
        "Android legal evidence file count differs from the closed policy.",
        "partial.rename(output)",
    ]
    if not all(marker in legal_stager for marker in legal_stager_markers):
        fail("The Android hash-bound legal evidence stager is incomplete.")

    ndk_source_packager = (root / "scripts/package_android_ndk_source.py").read_text(
        encoding="utf-8"
    )
    ndk_source_verifier = (
        root / "scripts/verify_android_ndk_source_archive.py"
    ).read_text(encoding="utf-8")
    ndk_source_markers = [
        "deterministic-tar-gzip-v1",
        "corresponding-source-mapped",
        '"ls-tree", "-r", "-z", "--full-tree", "HEAD"',
        "Android NDK source bytes differ from the pinned Git blob",
        "android-ndk-r29-runtime-source",
        "SOURCE-MANIFEST.json",
    ]
    if not all(marker in ndk_source_packager for marker in ndk_source_markers):
        fail("The Android NDK source packager is incomplete.")
    verifier_markers = [
        "deterministic-tar-gzip-v1",
        "corresponding-source-mapped",
        '"ls-tree", "-r", "-z", "--full-tree", "HEAD"',
        "Android NDK source manifest differs from the exact Git objects.",
        "Android NDK source member differs from its Git object",
        "SOURCE-MANIFEST.json",
    ]
    if not all(marker in ndk_source_verifier for marker in verifier_markers):
        fail("The independent Android NDK source verifier is incomplete.")

    corresponding_packager = (
        root / "scripts/package_android_corresponding_source.py"
    ).read_text(encoding="utf-8")
    corresponding_verifier = (
        root / "scripts/verify_android_corresponding_source_archive.py"
    ).read_text(encoding="utf-8")
    corresponding_packager_markers = [
        "complete-source-and-relink-inputs-packaged",
        "NDK_VERIFIER.verify",
        '"ls-tree", "-r", "-z", "--full-tree", "HEAD"',
        "Android source bytes differ from the pinned Git blob",
        "VLC contrib source archive differs from the legal audit",
        "SOURCE-SHA256SUMS",
        "REBUILD.md",
    ]
    if not all(marker in corresponding_packager for marker in corresponding_packager_markers):
        fail("The complete Android corresponding-source packager is incomplete.")
    corresponding_verifier_markers = [
        "complete-source-and-relink-inputs-packaged",
        "NDK_VERIFIER.verify",
        '"ls-tree", "-r", "-z", "--full-tree", "HEAD"',
        "Android corresponding-source manifest differs from the exact source inputs.",
        "Android corresponding-source member differs from its input",
        "gzip header is not deterministic",
        "SOURCE-SHA256SUMS",
        "REBUILD.md",
    ]
    if not all(marker in corresponding_verifier for marker in corresponding_verifier_markers):
        fail("The independent complete Android corresponding-source verifier is incomplete.")
    if "package_android_corresponding_source" in corresponding_verifier:
        fail("The Android corresponding-source verifier must not import its packager.")

    policy_patch = (
        root / "patches/libvlcjni/0001-kmediavlc-android-static-module-policy.patch"
    ).read_text(encoding="utf-8")
    patch_markers = [
        "LC_ALL=C sort",
        "    --disable-bluray",
        "sed -i.bak",
        'rm -f "$pcfile.bak"',
        'find "$1" -name "$2"',
        "PKG_CONFIG_IGNORE_CONFLICTS=1",
        'APP_LDFLAGS="${APP_LDFLAGS}"',
        "    dummy",
        "    file_logger",
        "    lua",
        "    rc",
        "    rotate",
        "    stream_out_(cycle|rtp)",
    ]
    if not all(marker in policy_patch for marker in patch_markers):
        fail("The Android static module policy patch is incomplete.")

    vlc_patch = (
        root / "patches/vlc/0001-android-external-anw-direct-mediacodec.patch"
    ).read_text(encoding="utf-8")
    vlc_patch_markers = [
        "AWindowHandler_newFromANWs",
        "awh->b_has_ndk_air_api = false",
        "awh->b_has_ndk_asc_api = false",
        "discard per-buffer HDR dataspaces",
    ]
    if not all(marker in vlc_patch for marker in vlc_patch_markers):
        fail("The Android external-ANativeWindow HDR patch is incomplete.")

    ci = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    ci_markers = [
        "verify-android-anw:",
        "ndk;29.0.14206865",
        "cmake;4.1.2",
        ":runtime-android:check",
        "test_android_native_bridge.sh",
        "bash gradlew",
    ]
    if not all(marker in ci for marker in ci_markers):
        fail("CI does not cross-compile and verify both Android callback ABIs.")

    documentation = (root / "docs/ANDROID.md").read_text(encoding="utf-8")
    if (
        "not a published native payload yet" not in documentation
        or "Publication gates still open" not in documentation
        or "does not change process-wide `HOME`" not in documentation
        or "Physical-device acceptance harness" not in documentation
        or "acceptance.json" not in documentation
    ):
        fail("Android documentation must remain explicit about its open release gates.")


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

    source_audit = (root / ".github/workflows/macos-source-audit.yml").read_text(
        encoding="utf-8"
    )
    source_audit_markers = [
        "workflow_dispatch:",
        "tested_commit:",
        "candidate_version:",
        "permissions:\n  contents: read",
        "scripts/build_vlc_macos.sh",
        "scripts/stage_vlc_macos_runtime.py",
        "pinnedVideoLanFixturePublishesCpuPullFrame",
        "pinnedVideoLanFixturePublishesAndReplacesRealMacIosurfaceFrames",
        "releaseEligible:false",
        "path: ${{ runner.temp }}/macos-aarch64-evidence",
    ]
    if not all(marker in source_audit for marker in source_audit_markers):
        fail("The manual macOS source audit is incomplete or does not retain bounded evidence.")
    forbidden_source_audit_markers = [
        "pull_request:",
        "push:",
        "${{ secrets.",
        "path: ${{ runner.temp }}/macos-aarch64-candidate",
    ]
    if any(marker in source_audit for marker in forbidden_source_audit_markers):
        fail("The macOS source audit must remain manual, secret-free, and candidate-free.")

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
    smoke_builder = (root / "scripts/build_ios_smoke_app.sh").read_text(encoding="utf-8")
    simulator_smoke = (root / "scripts/run_ios_simulator_smoke.sh").read_text(
        encoding="utf-8"
    )
    device_smoke = (root / "scripts/run_ios_device_smoke.sh").read_text(encoding="utf-8")
    smoke_source = (root / "scripts/ios-smoke/KMediaVlcSmoke.m").read_text(encoding="utf-8")
    builder_markers = [
        '!= "87"',
        "PLAYBACK_FIXTURE_SHA256",
        "libaudiounit_ios_plugin",
        "kmediavlc-playback.mkv",
        "iphoneos)",
        "iphonesimulator)",
        'expected_platform="IOS"',
        'expected_platform="IOSSIMULATOR"',
        "build_kmediavlc_ios_bridge.sh",
        "status --porcelain --untracked-files=no",
        "KMediaVlcTestedCommit",
        "KMediaVlcVlcRevision",
        "install_name_tool -id",
        "vtool -show-build",
        "otool -D",
        "-Wl,-rpath,@executable_path/Frameworks",
    ]
    simulator_markers = [
        "build_ios_smoke_app.sh",
        "iphonesimulator",
        "simctl install",
        "simctl launch --terminate-running-process",
        "codesign --force --sign -",
    ]
    device_markers = [
        "embedded.mobileprovision",
        "PLAYBACK_FIXTURE_SHA256",
        "KMediaVlcTestedCommit",
        "KMediaVlcVlcRevision",
        "codesign --verify --deep --strict",
        "Signature=adhoc",
        "devicectl device install app",
        "devicectl device process launch",
        "devicectl device uninstall app",
        "--console",
        "vtool -show-build",
        "otool -D",
        "KMEDIAVLC_SMOKE PASS ",
        "KMEDIAVLC_SMOKE FAIL ",
    ]
    source_markers = [
        "KMEDIAVLC_CPU_PULL",
        "kmediavlc_player_open",
        "kmediavlc_player_acquire_latest_frame",
        "KMEDIAVLC_CPU_ADDRESS",
        "kmediavlc_player_get_snapshot",
        "kmediavlc_player_seek",
        "KMEDIAVLC_STATE_ENDED",
        "create_audio_fixture",
        "audioDurationUs",
        "KMEDIAVLC_SMOKE %s",
    ]
    if (
        not all(marker in smoke_builder for marker in builder_markers)
        or not all(marker in simulator_smoke for marker in simulator_markers)
        or not all(marker in device_smoke for marker in device_markers)
        or not all(marker in smoke_source for marker in source_markers)
    ):
        fail("The packaged iOS simulator or physical-device playback gate is incomplete.")
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
        "codex/linux-bundled-runtime",
        "codex/bundled-libvlc4-integration",
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
        'github.ref == \'refs/heads/main\'',
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

    if (root / "LICENSE").read_bytes() != (root / "LICENSES/LGPL-2.1.txt").read_bytes():
        fail("The repository LICENSE must be the canonical bundled LGPL-2.1 text.")

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
    verify_platform_project_isolation(root)
    verify_policy(root)
    verify_pin_occurrences(root)
    verify_macos_transport_contract(root)
    verify_ios_runtime_contract(root)
    verify_linux_runtime_contract(root)
    verify_android_contract(root)
    verify_legal_files(root)
    print("KMediaVlc source and licensing policy verified.")


if __name__ == "__main__":
    main()
