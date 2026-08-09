# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "extract_release_repository.py"
SPEC = importlib.util.spec_from_file_location("release_extract", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
EXTRACTOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXTRACTOR)


class ReleaseRepositoryExtractorTest(unittest.TestCase):
    def write_archive(self, path: Path, entries: list[tuple[str, bytes | None, str]]) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, payload, kind in entries:
                info = tarfile.TarInfo(name)
                if kind == "directory":
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                elif kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "../../outside"
                    archive.addfile(info)
                else:
                    assert payload is not None
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))

    def test_extracts_only_regular_maven_tree(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            root = Path(value)
            archive = root / "repository.tar.gz"
            output = root / "output"
            output.mkdir()
            self.write_archive(
                archive,
                [
                    ("maven", None, "directory"),
                    ("maven/io", None, "directory"),
                    ("maven/io/example.pom", b"pom", "file"),
                ],
            )
            EXTRACTOR.extract(archive, output)
            self.assertEqual(b"pom", (output / "io/example.pom").read_bytes())

    def test_rejects_traversal_links_and_foreign_root(self) -> None:
        cases = [
            [("maven/../escape", b"bad", "file")],
            [("maven/link", None, "symlink")],
            [("repository/file", b"bad", "file")],
        ]
        for entries in cases:
            with self.subTest(entries=entries), tempfile.TemporaryDirectory() as value:
                root = Path(value)
                archive = root / "repository.tar.gz"
                output = root / "output"
                output.mkdir()
                self.write_archive(archive, entries)
                with self.assertRaises(ValueError):
                    EXTRACTOR.extract(archive, output)


if __name__ == "__main__":
    unittest.main()
