# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "stage_vlc_macos_runtime", ROOT / "scripts/stage_vlc_macos_runtime.py"
)
assert SPEC is not None and SPEC.loader is not None
STAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGER)


class StageVlcMacosRuntimeTest(unittest.TestCase):
    def test_loads_exact_approved_policy(self) -> None:
        policy, binary, modules = STAGER.load_policy(ROOT, allow_audit_candidate=False)
        self.assertEqual("approved", policy["reviewStatus"])
        self.assertEqual("approved", binary["reviewStatus"])
        self.assertEqual(27, len(binary["components"]))
        self.assertEqual(89, len(modules))
        self.assertEqual(89, len({name for _, name in modules}))
        self.assertIn(("video_output", "vgl"), modules)
        self.assertIn(("video_output", "glinterop_cvpx"), modules)
        self.assertIn(("misc", "securetransport"), modules)

    def test_release_mode_rejects_pending_dependency_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pending_root = Path(temporary)
            statuses = {
                "macos-aarch64-playback-modules.json": (
                    "pending-mach-o-and-source-license-audit"
                ),
                "macos-aarch64-binary-components.json": (
                    "pending-link-command-and-license-audit"
                ),
            }
            for filename, pending_status in statuses.items():
                payload = json.loads(
                    (ROOT / "compliance/policy" / filename).read_text(encoding="utf-8")
                )
                payload["reviewStatus"] = pending_status
                destination = pending_root / "compliance/policy" / filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(payload), encoding="utf-8")
            recipe = pending_root / "build-recipes/macos.json"
            recipe.parent.mkdir(parents=True, exist_ok=True)
            recipe.write_bytes((ROOT / "build-recipes/macos.json").read_bytes())

            with self.assertRaises(SystemExit):
                STAGER.load_policy(pending_root, allow_audit_candidate=False)

    def test_parses_closed_otool_records(self) -> None:
        output = """/tmp/libvlc.12.dylib:
\t@rpath/libvlc.12.dylib (compatibility version 13.0.0, current version 13.0.0)
\t@loader_path/libvlccore.9.dylib (compatibility version 10.0.0, current version 10.0.0)
\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1356.0.0)
"""
        self.assertEqual(
            [
                "@rpath/libvlc.12.dylib",
                "@loader_path/libvlccore.9.dylib",
                "/usr/lib/libSystem.B.dylib",
            ],
            STAGER.parse_otool_dependencies(output),
        )

    def test_parses_exact_macos_build_version(self) -> None:
        output = """Load command 8
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform 1
    minos 14.0
      sdk 26.5
   ntools 1
     tool 3
"""
        self.assertEqual([("1", "14.0")], STAGER.parse_build_versions(output))

    def test_relocation_uses_only_loader_relative_core_paths(self) -> None:
        plugin = Path("/tmp/libpng_plugin.dylib")
        with mock.patch.object(STAGER, "run_tool", return_value="") as run_tool:
            STAGER.relocate_macho(
                plugin,
                "PLUGIN",
                Path("/usr/bin/install_name_tool"),
                Path("/usr/bin/codesign"),
            )
        self.assertEqual(2, run_tool.call_count)
        relocation = run_tool.call_args_list[0].args[0]
        self.assertEqual("@rpath/libpng_plugin.dylib", relocation[2])
        self.assertIn("@loader_path/../../../bin/libvlccore.9.dylib", relocation)
        self.assertNotIn("@rpath/libvlccore.dylib", relocation[-2:])

    def test_copy_helper_hashes_relocated_candidate_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source.dylib"
            destination = base / "output/bin/libvlc.12.dylib"
            source.write_bytes(b"candidate")
            result = STAGER.copy_file(source, destination)
            self.assertEqual(9, result["size"])
            self.assertEqual(64, len(result["sha256"]))


if __name__ == "__main__":
    unittest.main()
