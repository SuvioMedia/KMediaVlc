# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import importlib.util
import io
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "prefetch_vlc_archive", ROOT / "scripts/prefetch_vlc_archive.py"
)
assert SPEC is not None and SPEC.loader is not None
PREFETCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFETCH)


class PrefetchVlcArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.destination = self.root / "tarballs"
        self.destination.mkdir()
        self.archive = "source-1.0.tar.xz"
        self.payload = b"pinned source archive\n"
        self.digest = hashlib.sha512(self.payload).hexdigest()
        self.manifest = self.root / "SHA512SUMS"
        self.manifest.write_text(
            f"{self.digest}  {self.archive}\n", encoding="ascii"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prefetch(self, urls: list[str] | None = None) -> Path:
        return PREFETCH.prefetch(
            manifest=self.manifest,
            archive=self.archive,
            destination_directory=self.destination,
            urls=urls or ["https://mirror.example.invalid/source-1.0.tar.xz"],
        )

    def test_reuses_only_a_checksum_verified_existing_archive(self) -> None:
        archive = self.destination / self.archive
        archive.write_bytes(self.payload)
        with mock.patch.object(PREFETCH, "download") as download:
            self.assertEqual(archive.resolve(), self.prefetch())
        download.assert_not_called()

    def test_rejects_an_existing_archive_with_the_wrong_digest(self) -> None:
        (self.destination / self.archive).write_bytes(b"unexpected\n")
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.prefetch()

    def test_uses_a_fallback_and_installs_only_the_verified_payload(self) -> None:
        urls = [
            "https://first.example.invalid/source-1.0.tar.xz",
            "https://second.example.invalid/source-1.0.tar.xz",
        ]

        def download(url: str, output: Path) -> bool:
            if url == urls[0]:
                output.write_bytes(b"partial\n")
                return False
            output.write_bytes(self.payload)
            return True

        with mock.patch.object(PREFETCH, "download", side_effect=download) as mocked:
            archive = self.prefetch(urls)
        self.assertEqual(self.payload, archive.read_bytes())
        self.assertEqual(2, mocked.call_count)

    def test_rejects_credentials_or_query_data_in_a_mirror_url(self) -> None:
        for url in (
            "https://user@example.invalid/source.tar.xz",
            "https://example.invalid/source.tar.xz?token=value",
            "http://example.invalid/source.tar.xz",
        ):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "HTTPS"):
                self.prefetch([url])

    def bundle_prefetch(self, payload: bytes, *, duplicate: bool = False, symbolic: bool = False) -> Path:
        member_name = f"source/contrib/{self.archive}"
        bundle = self.root / "bundle.tar.gz"
        with tarfile.open(bundle, "w:gz") as target:
            member = tarfile.TarInfo(member_name)
            member.size = len(payload)
            if symbolic:
                member.type = tarfile.SYMTYPE
                member.linkname = "/outside"
                member.size = 0
            target.addfile(member, io.BytesIO(payload))
            if duplicate:
                target.addfile(member, io.BytesIO(payload))

        def download(url: str, output: Path) -> bool:
            shutil.copyfile(bundle, output)
            return True

        with mock.patch.object(PREFETCH, "download", side_effect=download):
            return PREFETCH.prefetch(
                manifest=self.manifest, archive=self.archive,
                destination_directory=self.destination, urls=[],
                bundle_url="https://example.invalid/source-bundle.tar.gz",
                bundle_member=member_name,
            )

    def test_recovers_original_archive_from_corresponding_source_bundle(self) -> None:
        self.assertEqual(self.payload, self.bundle_prefetch(self.payload).read_bytes())
        self.assertEqual([self.archive], [p.name for p in self.destination.iterdir()])

    def test_rejects_changed_archive_inside_source_bundle(self) -> None:
        with self.assertRaisesRegex(ValueError, "pinned SHA-512"):
            self.bundle_prefetch(b"changed archive")
        self.assertEqual([], list(self.destination.iterdir()))

    def test_rejects_duplicate_or_symbolic_bundle_member(self) -> None:
        for option in ("duplicate", "symbolic"):
            with self.subTest(option=option), self.assertRaisesRegex(ValueError, "regular file"):
                self.bundle_prefetch(self.payload, **{option: True})
            self.assertEqual([], list(self.destination.iterdir()))

    def test_rejects_missing_bundle_member(self) -> None:
        bundle = self.root / "empty.tar.gz"
        with tarfile.open(bundle, "w:gz"):
            pass
        with self.assertRaisesRegex(ValueError, "exact pinned archive"):
            PREFETCH.extract_bundle_member(bundle, "absent.tar.gz", self.root / "out")


if __name__ == "__main__":
    unittest.main()
