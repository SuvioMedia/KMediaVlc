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
    "libxml2": "libxml2-Copyright.txt",
    "nettle": "LGPL-3.0.txt",
    "openjpeg": "OpenJPEG-LICENSE.txt",
    "opus": "Opus-COPYING.txt",
    "soxr": "SoXR-LICENCE.txt",
    "speexdsp": "SpeexDSP-COPYING.txt",
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
        "requiredPlaybackModules", "libvlcjniPatch", "disabledVlcFeatures",
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
        or recipe.get("libvlcjniPatch")
        != "patches/libvlcjni/0001-kmediavlc-android-static-module-policy.patch"
        or recipe.get("disabledVlcFeatures") != ["bluray"]
    ):
        fail("The Android playback module, disabled feature, or libvlcjni patch policy changed.")
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
        "JNI_OnLoad",
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

    android_build = (root / "runtime-android/build.gradle.kts").read_text(encoding="utf-8")
    gradle_markers = [
        'setOf("arm64-v8a", "armeabi-v7a")',
        'setOf("libkmediavlc_android.so", "libvlc.so")',
        'minSdk = 28',
        'require(values == expectedManifest("true"))',
        "kmediaVlcAndroidNativePayloadDirectory",
        "AndroidLegalEvidence.read",
        "android-static-legal.json",
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
    ):
        fail("Android documentation must remain explicit about its open release gates.")


def verify_legal_files(root: Path) -> None:
    binary = load_json(root / "compliance/policy/windows-x86_64-binary-components.json")
    components = binary.get("components")
    if not isinstance(components, dict) or set(components) != set(COMPONENT_NOTICE_FILES):
        fail("Legal notice mapping must cover the closed Windows component inventory exactly.")
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
    if binary.get("toolchainImage") not in notices:
        fail("Third-party notices omit the pinned Windows toolchain.")
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
    verify_android_contract(root)
    verify_legal_files(root)
    print("KMediaVlc source and licensing policy verified.")


if __name__ == "__main__":
    main()
