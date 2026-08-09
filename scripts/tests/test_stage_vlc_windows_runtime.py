# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "stage_vlc_windows_runtime", ROOT / "scripts/stage_vlc_windows_runtime.py"
)
assert SPEC is not None and SPEC.loader is not None
STAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGER)


class StageVlcWindowsRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.install = self.base / "install"
        self.bridge = self.base / "kmediavlc_bridge.dll"
        self.output = self.base / "output"
        self.report = self.base / "report.json"
        for relative in ("bin/libvlc.dll", "bin/libvlccore-9.dll"):
            path = self.install / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode("ascii"))
        self.bridge.write_bytes(b"bridge")
        _, modules = STAGER.load_policy(ROOT, allow_audit_candidate=True)
        for _, name in modules:
            path = self.install / "lib/vlc/plugins" / f"lib{name}_plugin.dll"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode("ascii"))
        extra = self.install / "lib/vlc/plugins/libstream_out_dummy_plugin.dll"
        extra.write_bytes(b"forbidden release extra")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stages_only_the_closed_candidate_policy(self) -> None:
        policy, modules = STAGER.load_policy(ROOT, allow_audit_candidate=True)
        self.assertEqual("pending-meson-dependency-audit", policy["reviewStatus"])
        self.assertEqual(90, len(modules))

    def test_release_mode_rejects_pending_dependency_review(self) -> None:
        with self.assertRaises(SystemExit):
            STAGER.load_policy(ROOT, allow_audit_candidate=False)

    def test_copy_helper_hashes_exact_bytes(self) -> None:
        destination = self.output / "bridge.dll"
        result = STAGER.copy_file(self.bridge, destination)
        self.assertEqual(6, result["size"])
        self.assertEqual(64, len(result["sha256"]))


if __name__ == "__main__":
    unittest.main()
