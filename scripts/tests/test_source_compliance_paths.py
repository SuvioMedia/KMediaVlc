# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "verify_source_compliance.py"
SPEC = importlib.util.spec_from_file_location("source_compliance", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
COMPLIANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPLIANCE)
ROOT = MODULE_PATH.parent.parent


class SourceCompliancePathTest(unittest.TestCase):
    def test_ancestor_named_build_does_not_disable_repository_scan(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value) / "build" / "repository"
            root.mkdir(parents=True)
            (root / "missing.py").write_text("print('missing SPDX')\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "missing.py"):
                COMPLIANCE.verify_spdx(root)

    def test_repository_build_directory_remains_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value) / "repository"
            ignored = [
                root / "build" / "generated.py",
                root / ".vlc-source" / "upstream.py",
            ]
            for path in ignored:
                path.parent.mkdir(parents=True)
                path.write_text("print('external or generated')\n", encoding="utf-8")
            COMPLIANCE.verify_spdx(root)


class RetainedDesktopAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        policy_path = Path("compliance/policy/desktop-retained-audit-e439692.json")
        policy = json.loads((ROOT / policy_path).read_text(encoding="utf-8"))
        for relative in [policy_path, Path(policy["evidence"]["acceptancePath"]),
                         *map(Path, policy["approvedPolicyFiles"])]:
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)

    def test_historical_evidence_survives_a_new_pending_source_pin(self) -> None:
        COMPLIANCE.verify_desktop_retained_audit(self.root)

    def test_new_source_cannot_inherit_the_previous_approval(self) -> None:
        path = self.root / "compliance/policy/windows-x86_64-playback-modules.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotEqual(COMPLIANCE.DESKTOP_AUDIT_REVISION, policy["vlcRevision"])
        policy["reviewStatus"] = "approved"
        path.write_text(json.dumps(policy), encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "cannot inherit"):
            COMPLIANCE.verify_desktop_retained_audit(self.root)


if __name__ == "__main__":
    unittest.main()
