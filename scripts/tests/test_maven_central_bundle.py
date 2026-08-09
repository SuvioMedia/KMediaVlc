# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "build_maven_central_bundle.py"
SPEC = importlib.util.spec_from_file_location("central", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
CENTRAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CENTRAL)


class MavenCentralBundleTest(unittest.TestCase):
    def create_staging(self, root: Path, version: str) -> Path:
        directory = root / CENTRAL.GROUP / CENTRAL.ARTIFACT / version
        directory.mkdir(parents=True)
        prefix = f"{CENTRAL.ARTIFACT}-{version}"
        for name in (
            f"{prefix}.jar",
            f"{prefix}.pom",
            f"{prefix}-sources.jar",
            f"{prefix}-javadoc.jar",
            f"{prefix}-corresponding-source.tar.gz",
        ):
            (directory / name).write_bytes(name.encode("ascii"))
        return directory

    def test_normalize_removes_only_gradle_generated_files(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            version = "0.1.0-rc.1"
            directory = self.create_staging(root, version)
            prefix = f"{CENTRAL.ARTIFACT}-{version}"
            module = directory / f"{prefix}.module"
            metadata = directory.parent / "maven-metadata.xml"
            module.write_bytes(b"generated")
            metadata.write_bytes(b"generated")
            for path in (*CENTRAL.required_files(root, version), module, metadata):
                for suffix in CENTRAL.GENERATED_CHECKSUM_SUFFIXES:
                    path.with_name(path.name + suffix).write_bytes(b"generated")

            CENTRAL.normalize(type("Arguments", (), {"staging": root, "version": version})())

            self.assertEqual(5, len(CENTRAL.base_files(root, version)))
            actual = {
                path for path in (root / CENTRAL.GROUP).rglob("*") if path.is_file()
            }
            self.assertEqual(set(CENTRAL.required_files(root, version)), actual)

    def test_packages_only_signed_closed_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            version = "0.1.0"
            self.create_staging(root, version)
            bases = CENTRAL.base_files(root, version)
            for path in bases:
                path.with_name(path.name + ".asc").write_bytes(b"signature")
            arguments = type("Arguments", (), {"staging": root, "version": version})()
            CENTRAL.checksums(arguments)
            output = root / "central.zip"
            CENTRAL.package(
                type(
                    "Arguments",
                    (),
                    {
                        "staging": root,
                        "version": version,
                        "epoch": 1_700_000_000,
                        "output": output,
                    },
                )()
            )
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(20, len(archive.namelist()))
                self.assertTrue(all(name.startswith("io/github/shusek/") for name in archive.namelist()))

    def test_rejects_snapshot_extra_file_and_missing_signature(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            with self.assertRaisesRegex(ValueError, "non-SNAPSHOT"):
                CENTRAL.required_files(root, "0.1.0-SNAPSHOT")
            for invalid in ("0.1.0-01", "0.1.0-..", "01.0.0", "v0.1.0"):
                with self.subTest(version=invalid), self.assertRaisesRegex(ValueError, "SemVer"):
                    CENTRAL.required_files(root, invalid)
            version = "0.1.0"
            directory = self.create_staging(root, version)
            (directory / "foreign.txt").write_text("unexpected", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "closed"):
                CENTRAL.base_files(root, version)
            (directory / "foreign.txt").unlink()
            with self.assertRaisesRegex(ValueError, "sidecar"):
                CENTRAL.package(
                    type(
                        "Arguments",
                        (),
                        {
                            "staging": root,
                            "version": version,
                            "epoch": 1_700_000_000,
                            "output": root / "central.zip",
                        },
                    )()
                )


if __name__ == "__main__":
    unittest.main()
