# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "promote_android_payload", ROOT / "scripts/promote_android_payload.py"
)
assert SPEC is not None and SPEC.loader is not None
PROMOTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROMOTER)


class PromoteAndroidPayloadTest(unittest.TestCase):
    def test_promotes_real_candidate_metadata_without_touching_libraries(self) -> None:
        candidate = Path("/private/tmp/kmediavlc-android-rebuild/payload3")
        if not candidate.is_dir():
            self.skipTest("Local source-built Android candidate is not available")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "promoted"
            before = {
                path.relative_to(candidate): path.read_bytes()
                for path in candidate.glob("jni/*/*.so")
            }
            PROMOTER.promote(ROOT, candidate, output)
            runtime = (output / "android-runtime.properties").read_text(encoding="ascii")
            legal = json.loads(
                (output / "legal/android-static-legal.json").read_text(encoding="utf-8")
            )
            self.assertIn("releaseEligible=true", runtime)
            self.assertEqual(PROMOTER.STATUS, legal["reviewStatus"])
            self.assertEqual("passed", legal["automaticLicenseScan"]["result"])
            for relative, value in before.items():
                self.assertEqual(value, (output / relative).read_bytes())


if __name__ == "__main__":
    unittest.main()
