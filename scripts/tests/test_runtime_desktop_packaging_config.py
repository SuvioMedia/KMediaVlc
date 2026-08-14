# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_SCRIPT = ROOT / "runtime-desktop/build.gradle.kts"


class RuntimeDesktopPackagingConfigTest(unittest.TestCase):
    def test_full_manifest_matrix_is_required_only_for_matrix_packaging(self) -> None:
        build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
        marker = 'systemProperty("kmediavlc.test.bundledManifestMatrix", "true")'
        marker_index = build_script.index(marker)
        condition = build_script.rfind("if (", 0, marker_index)

        self.assertNotEqual(-1, condition)
        self.assertEqual(
            "if (matrixNativeConfigured) {",
            build_script[condition : build_script.index("\n", condition)],
        )
        self.assertNotIn(
            "if (nativePackagingConfigured) {\n"
            "    tasks.test {\n"
            f"        {marker}",
            build_script,
        )


if __name__ == "__main__":
    unittest.main()
