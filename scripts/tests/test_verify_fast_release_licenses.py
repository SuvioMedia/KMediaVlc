# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "verify_fast_release_licenses", ROOT / "scripts/verify_fast_release_licenses.py"
)
assert SPEC is not None and SPEC.loader is not None
SCANNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCANNER)


class FastReleaseLicenseScanTest(unittest.TestCase):
    def test_repository_policies_pass(self) -> None:
        self.assertGreaterEqual(SCANNER.verify(ROOT, [], []), 20)

    def test_forbidden_gpl_identifier_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "inventory.json"
            path.write_text(json.dumps({"licenseSpdx": "GPL-3.0-only"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Forbidden license"):
                SCANNER.verify(ROOT, [path], [])


if __name__ == "__main__":
    unittest.main()
