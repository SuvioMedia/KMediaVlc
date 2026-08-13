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
    def create_staging(
        self,
        root: Path,
        version: str,
        artifact_set: str = "multiplatform",
    ) -> None:
        for artifact in central.selected_artifacts(artifact_set):
            contract = central.ARTIFACTS[artifact]
            directory = root / central.GROUP / artifact / version
            directory.mkdir(parents=True)
            prefix = f"{artifact}-{version}"
            names = [
                f"{prefix}.{contract['primaryExtension']}",
                f"{prefix}.pom",
                *(
                    f"{prefix}-{classifier}.{extension}"
                    for classifier, extension in contract["classifiers"]
                ),
            ]
            for name in names:
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
            self.assertEqual(11, len(central.base_files(extracted, version)))

    def test_packages_only_the_ios_artifact_set(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            staging = root / "staging"
            staging.mkdir()
            version = "0.1.0-rc.3"
            self.create_staging(staging, version, "ios")
            archive = root / "ios.tar.gz"

            release.package(staging, version, 1_700_000_000, archive, "ios")

            extracted = root / "extracted"
            extracted.mkdir()
            extractor.extract(archive, extracted)
            self.assertEqual(5, len(central.base_files(extracted, version, "ios")))


if __name__ == "__main__":
    unittest.main()
