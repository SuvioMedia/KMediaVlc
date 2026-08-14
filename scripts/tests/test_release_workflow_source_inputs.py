# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/release.yml"


class ReleaseWorkflowSourceInputsTest(unittest.TestCase):
    def test_release_reopens_the_audited_windows_source_set(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertGreaterEqual(
            workflow.count("vlc-windows-x86_64-source-audit-"),
            2,
        )
        self.assertIn("Download the audited Windows corresponding source", workflow)
        self.assertIn("scripts/verify_corresponding_source_archive.py", workflow)
        self.assertIn('--tested-commit "$DESKTOP_RUNTIME_COMMIT"', workflow)
        self.assertIn("corresponding-source/contrib-tarballs", workflow)
        self.assertIn("--strip-components=2", workflow)
        self.assertIn('--contrib "$windows_contrib"', workflow)

    def test_release_does_not_reconstruct_windows_sources_ad_hoc(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertNotIn("speexdsp-1.2.1.tar.gz", workflow)
        self.assertNotIn("AMF-1.5.2.tar.gz", workflow)


if __name__ == "__main__":
    unittest.main()
