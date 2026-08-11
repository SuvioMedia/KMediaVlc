# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "package_corresponding_source", ROOT / "scripts/package_corresponding_source.py"
)
assert SPEC is not None and SPEC.loader is not None
PACKAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGER)
COMMIT = "0123456789abcdef0123456789abcdef01234567"


class PackageCorrespondingSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.candidate = self.base / "candidate.tar.gz"
        policy, archives = PACKAGER.load_policy(ROOT, allow_audit_candidate=True)
        self.assertEqual("pending-link-command-audit", policy["reviewStatus"])
        files = {
            "corresponding-source/kmediavlc/build.gradle.kts": b"plugins { base }\n",
            "corresponding-source/kmediavlc/native/bridge.cpp": b"// bridge\n",
            "corresponding-source/vlc/meson.build": b"project('vlc')\n",
            "corresponding-source/vlc/modules/codec.c": b"/* codec */\n",
            "corresponding-source/BUILD-TOOLCHAIN.txt": b"immutable toolchain\n",
            "corresponding-source/TOOLCHAIN-STATIC-ARCHIVES-SHA256SUMS": (
                b"0" * 64 + b"  /opt/llvm-mingw/lib/libunwind.a\n"
            ),
            "corresponding-source/toolchain-licenses/LICENSE.TXT": b"toolchain license\n",
            "corresponding-source/contrib-tarballs/forbidden-gpl.tar.xz": b"excluded",
        }
        files.update(
            {
                f"corresponding-source/contrib-tarballs/{archive}": archive.encode("ascii")
                for archive in archives
            }
        )
        with tarfile.open(self.candidate, "w:gz") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_packages_only_reviewed_contrib_sources_deterministically(self) -> None:
        first = self.base / "first.tar.gz"
        second = self.base / "second.tar.gz"
        first_hash = PACKAGER.package(
            ROOT, self.candidate, first, COMMIT, "0.1.0-rc.1", 1_700_000_000,
            allow_audit_candidate=True
        )
        second_hash = PACKAGER.package(
            ROOT, self.candidate, second, COMMIT, "0.1.0-rc.1", 1_700_000_000,
            allow_audit_candidate=True
        )
        self.assertEqual(first_hash, second_hash)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first, "r:gz") as archive:
            names = {member.name.rstrip("/") for member in archive.getmembers()}
            self.assertNotIn(
                "corresponding-source/contrib-tarballs/forbidden-gpl.tar.xz", names
            )
            manifest = json.load(archive.extractfile("corresponding-source/SOURCE-MANIFEST.json"))
        self.assertEqual(COMMIT, manifest["testedCommit"])
        self.assertEqual("0.1.0-rc.1", manifest["releaseVersion"])
        self.assertEqual("pending-link-command-audit", manifest["componentReviewStatus"])

    def test_release_mode_rejects_pending_binary_review(self) -> None:
        with self.assertRaises(ValueError):
            PACKAGER.package(
                ROOT,
                self.candidate,
                self.base / "release.tar.gz",
                COMMIT,
                "0.1.0-rc.1",
                1_700_000_000,
            )

    def test_rejects_links_in_source_candidate(self) -> None:
        unsafe = self.base / "unsafe.tar.gz"
        with tarfile.open(unsafe, "w:gz") as archive:
            info = tarfile.TarInfo("corresponding-source/vlc/escape")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside"
            archive.addfile(info)
        with self.assertRaises(ValueError):
            PACKAGER.package(
                ROOT,
                unsafe,
                self.base / "unsafe-output.tar.gz",
                COMMIT,
                "0.1.0-rc.1",
                1_700_000_000,
                allow_audit_candidate=True,
            )


if __name__ == "__main__":
    unittest.main()
