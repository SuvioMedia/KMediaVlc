# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
try:
    import build_maven_central_bundle as central
    import extract_release_repository as extractor
    import package_release_repository as release
finally:
    sys.path.pop(0)


class ReleaseRepositoryPackagerTest(unittest.TestCase):
    def create_staging(self, root: Path, version: str) -> None:
        directory = root / central.GROUP / central.ARTIFACT / version
        directory.mkdir(parents=True)
        prefix = f"{central.ARTIFACT}-{version}"
        for name in (
            f"{prefix}.jar",
            f"{prefix}.pom",
            f"{prefix}-sources.jar",
            f"{prefix}-javadoc.jar",
            f"{prefix}-corresponding-source.tar.gz",
        ):
            (directory / name).write_bytes(name.encode("ascii"))

    def test_is_deterministic_and_round_trips_through_safe_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            staging = root / "staging"
            staging.mkdir()
            version = "0.1.0-rc.1"
            self.create_staging(staging, version)
            first = root / "first.tar.gz"
            second = root / "second.tar.gz"

            first_hash = release.package(staging, version, 1_700_000_000, first)
            second_hash = release.package(staging, version, 1_700_000_000, second)

            self.assertEqual(first_hash, second_hash)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            extracted = root / "extracted"
            extracted.mkdir()
            extractor.extract(first, extracted)
            self.assertEqual(5, len(central.base_files(extracted, version)))


if __name__ == "__main__":
    unittest.main()
