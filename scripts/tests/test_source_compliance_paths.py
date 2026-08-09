# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "verify_source_compliance.py"
SPEC = importlib.util.spec_from_file_location("source_compliance", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
COMPLIANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(COMPLIANCE)


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


if __name__ == "__main__":
    unittest.main()
