# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "desktop_sources", Path(__file__).resolve().parents[1] / "audit_vlc_desktop_sources.py"
)
assert SPEC is not None and SPEC.loader is not None
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class SourceLicenseGateTest(unittest.TestCase):
    def test_lgpl_header_with_comment_line_breaks(self) -> None:
        data = b"/* GNU Lesser General Public\n * License, version 2.1 or later. */\n#include <stdlib.h>"
        self.assertEqual("LGPL-2.1-or-later", AUDIT.classify("source.c", data)[0])

    def test_gpl_header_with_comment_line_breaks_is_rejected(self) -> None:
        data = b"/* GNU General Public\n * License, version 2 or later. */\n#include <stdlib.h>"
        with self.assertRaisesRegex(ValueError, "Forbidden"):
            AUDIT.classify("source.c", data)

    def test_spdx_conjunction_cannot_hide_a_forbidden_license(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unapproved"):
            AUDIT.classify("source.c", b"// SPDX-License-Identifier: LGPL-2.1-or-later AND GPL-2.0-only\n")

    def test_changed_project_context_bytes_require_review(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            AUDIT.classify("compat/lfind.c", b"/* changed without a license header */")

    def test_unknown_license_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            AUDIT.classify("source.c", b"/* All rights reserved. */")


if __name__ == "__main__":
    unittest.main()
