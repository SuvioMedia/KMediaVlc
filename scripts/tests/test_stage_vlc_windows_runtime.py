# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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
        _, _, modules = STAGER.load_policy(ROOT, allow_audit_candidate=True)
        for _, name in modules:
            path = self.install / "lib/vlc/plugins" / f"lib{name}_plugin.dll"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode("ascii"))
        extra = self.install / "lib/vlc/plugins/libstream_out_dummy_plugin.dll"
        extra.write_bytes(b"forbidden release extra")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_stages_only_the_audit_candidate_policy(self) -> None:
        policy, binary_policy, modules = STAGER.load_policy(
            ROOT, allow_audit_candidate=True
        )
        self.assertEqual("pending-meson-dependency-audit", policy["reviewStatus"])
        self.assertEqual(
            "pending-link-command-audit", binary_policy["reviewStatus"]
        )
        self.assertEqual(90, len(modules))

    def test_release_mode_rejects_pending_dependency_review(self) -> None:
        with self.assertRaises(SystemExit):
            STAGER.load_policy(ROOT, allow_audit_candidate=False)

    def test_copy_helper_hashes_exact_bytes(self) -> None:
        destination = self.output / "bridge.dll"
        result = STAGER.copy_file(self.bridge, destination)
        self.assertEqual(6, result["size"])
        self.assertEqual(64, len(result["sha256"]))

    def test_current_report_is_an_audit_candidate(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/stage_vlc_windows_runtime.py"),
                "--root",
                str(ROOT),
                "--install",
                str(self.install),
                "--bridge",
                str(self.bridge),
                "--output",
                str(self.output),
                "--report",
                str(self.report),
                "--allow-audit-candidate",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        report = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertTrue(report["auditCandidate"])
        self.assertEqual("pending-meson-dependency-audit", report["reviewStatus"])
        self.assertEqual(
            "pending-link-command-audit", report["binaryReviewStatus"]
        )

    def test_explicit_pending_review_report_is_an_audit_candidate(self) -> None:
        pending_root = self.base / "pending-report-root"
        for filename, pending_status in {
            "windows-x86_64-playback-modules.json": "pending-meson-dependency-audit",
            "windows-x86_64-binary-components.json": "pending-link-command-audit",
        }.items():
            policy = json.loads(
                (ROOT / "compliance/policy" / filename).read_text(encoding="utf-8")
            )
            policy["reviewStatus"] = pending_status
            destination = pending_root / "compliance/policy" / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(policy) + "\n", encoding="utf-8")
        output = self.base / "audit-output"
        report_path = self.base / "audit-report.json"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/stage_vlc_windows_runtime.py"),
                "--root",
                str(pending_root),
                "--install",
                str(self.install),
                "--bridge",
                str(self.bridge),
                "--output",
                str(output),
                "--report",
                str(report_path),
                "--allow-audit-candidate",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertTrue(report["auditCandidate"])


if __name__ == "__main__":
    unittest.main()
