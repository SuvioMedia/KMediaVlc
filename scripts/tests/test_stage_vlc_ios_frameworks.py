# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import json
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "stage_vlc_ios_frameworks", ROOT / "scripts/stage_vlc_ios_frameworks.py"
)
assert SPEC is not None and SPEC.loader is not None
STAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGER)


class StageVlcIosFrameworksTest(unittest.TestCase):
    def test_loads_exact_closed_candidate_policy(self) -> None:
        policy, modules = STAGER.load_policy(ROOT, allow_audit_candidate=True)
        self.assertEqual(
            "pending-framework-and-source-license-audit", policy["reviewStatus"]
        )
        self.assertEqual(84, len(modules))
        self.assertEqual(84, len({name for _, name in modules}))
        self.assertIn(("audio_output", "audiounit_ios"), modules)
        self.assertIn(("codec", "videotoolbox"), modules)
        self.assertIn(("video_output", "vmem"), modules)
        self.assertNotIn(("video_output", "vgl"), modules)
        binary = STAGER.load_binary_policy(ROOT, modules, allow_audit_candidate=True)
        self.assertEqual("pending-link-command-and-license-audit", binary["reviewStatus"])
        self.assertEqual(22, len(binary["components"]))
        self.assertEqual(["BSL-1.0"], binary["components"]["utfcpp"]["licenseSpdx"])
        self.assertIn("utfcpp", binary["moduleComponents"]["mkv"])

    def test_release_mode_rejects_pending_dependency_review(self) -> None:
        with self.assertRaises(SystemExit):
            STAGER.load_policy(ROOT, allow_audit_candidate=False)
        _, modules = STAGER.load_policy(ROOT, allow_audit_candidate=True)
        with self.assertRaises(SystemExit):
            STAGER.load_binary_policy(ROOT, modules, allow_audit_candidate=False)

    def test_parses_exact_ios_simulator_build_version(self) -> None:
        output = """Load command 8
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 7
    minos 16.2
      sdk 26.5
   ntools 1
     tool 3
"""
        self.assertEqual([("7", "16.2")], STAGER.parse_build_versions(output))

    def test_plugin_framework_name_matches_vlc_loader_contract(self) -> None:
        executable = STAGER.executable_for_plugin("audiounit_ios")
        self.assertEqual("libaudiounit_ios_plugin", executable)
        self.assertEqual(
            "@rpath/libaudiounit_ios_plugin.framework/libaudiounit_ios_plugin",
            STAGER.expected_install_name("PLUGIN", executable),
        )

    def test_relocation_points_libvlc_and_plugins_at_core_framework(self) -> None:
        plugin = Path("/tmp/libmp4_plugin")
        with mock.patch.object(STAGER, "run_tool", return_value="") as run_tool:
            STAGER.relocate_macho(
                plugin,
                "PLUGIN",
                "libmp4_plugin",
                Path("/usr/bin/install_name_tool"),
            )
        command = run_tool.call_args.args[0]
        self.assertEqual("@rpath/libmp4_plugin.framework/libmp4_plugin", command[2])
        self.assertIn("@rpath/libvlccore.dylib", command)
        self.assertIn(
            "@rpath/KMediaVlcCore.framework/KMediaVlcCore",
            command,
        )

    def test_bridge_framework_exposes_stable_c_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            framework = root / "KMediaVlc.framework"
            framework.mkdir()
            header = root / "kmediavlc_client.h"
            header.write_text("/* stable ABI */\n", encoding="utf-8")
            names = STAGER.write_framework_metadata(
                framework,
                "KMediaVlc",
                STAGER.TARGETS["ios-simulator-arm64"],
                [header],
            )
            self.assertEqual(["kmediavlc_client.h"], names)
            module_map = (framework / "Modules/module.modulemap").read_text(
                encoding="utf-8"
            )
            self.assertIn("framework module KMediaVlc", module_map)
            self.assertIn('umbrella header "kmediavlc_client.h"', module_map)
            with (framework / "Info.plist").open("rb") as source:
                metadata = plistlib.load(source)
            self.assertEqual("KMediaVlc", metadata["CFBundleExecutable"])
            self.assertEqual("16.2", metadata["MinimumOSVersion"])

    def test_recipe_closes_device_simulator_and_upstream_utfcpp_inputs(self) -> None:
        recipe = json.loads(
            (ROOT / "build-recipes/ios.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {"ios-arm64", "ios-simulator-arm64"}, set(recipe["targets"])
        )
        self.assertEqual(
            {"iphoneos", "iphonesimulator"},
            {target["sdk"] for target in recipe["targets"].values()},
        )
        self.assertEqual(
            {"16.2"}, {target["minimumOs"] for target in recipe["targets"].values()}
        )
        self.assertEqual(84, recipe["stagedPluginCount"])
        self.assertEqual(286, recipe["rawSourceBuildPluginCount"])
        self.assertEqual(87, recipe["frameworkCountPerSlice"])
        self.assertIn("utfcpp", recipe["resolvedContribPackages"])
        self.assertEqual([], recipe["sourceOverlays"])

    def test_bridge_cmake_excludes_jni_and_macos_renderer_on_ios(self) -> None:
        cmake = (ROOT / "native/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('CMAKE_SYSTEM_NAME STREQUAL "iOS"', cmake)
        self.assertIn("if(NOT KMEDIAVLC_IOS)", cmake)
        self.assertIn("KMEDIAVLC_IOS=1", cmake)
        self.assertIn("src/platform_renderer_stub.cpp", cmake)
        ios_branch = cmake.split("if(KMEDIAVLC_IOS)", 2)[1]
        self.assertNotIn("macos_iosurface_renderer.cpp", ios_branch.split("elseif", 1)[0])

    def test_ios_bridge_scans_only_the_flattened_framework_graph(self) -> None:
        bridge = (ROOT / "native/src/kmediavlc_bridge.cpp").read_text(
            encoding="utf-8"
        )
        ios_branch = bridge.split("#if defined(KMEDIAVLC_IOS)", 1)[1]
        self.assertIn('setenv("VLC_LIB_PATH", path.c_str(), 1)', ios_branch)
        self.assertIn('setenv("VLC_PLUGIN_PATH", path.c_str(), 1)', ios_branch)
        self.assertIn('arguments.push_back("--no-plugins-cache")', bridge)
        self.assertIn('arguments.push_back("--plugins-scan")', bridge)
        self.assertIn('arguments.push_back("--no-plugins-scan")', bridge)

    def test_bridge_build_script_pins_both_ios_abis(self) -> None:
        builder = (ROOT / "scripts/build_kmediavlc_ios_bridge.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            'readonly MINIMUM_IOS="16.2"',
            "iphoneos)",
            "iphonesimulator)",
            "-DCMAKE_SYSTEM_NAME=iOS",
            '-DCMAKE_OSX_ARCHITECTURES=arm64',
            'platform $expected_platform',
            'minos $MINIMUM_IOS',
        ):
            self.assertIn(marker, builder)


if __name__ == "__main__":
    unittest.main()
