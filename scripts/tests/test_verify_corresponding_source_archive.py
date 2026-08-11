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
COMMIT = "0123456789abcdef0123456789abcdef01234567"
VERSION = "0.1.0-rc.1"


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PACKAGER = load_module("package_source_for_verifier", "scripts/package_corresponding_source.py")
VERIFIER = load_module("verify_corresponding_source_archive", "scripts/verify_corresponding_source_archive.py")


class VerifyCorrespondingSourceArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "repository"
        policy_directory = self.root / "compliance/policy"
        policy_directory.mkdir(parents=True)
        self.policy_path = policy_directory / "windows-x86_64-binary-components.json"
        self.policy = {
            "schemaVersion": 1,
            "target": "windows-x86_64",
            "vlcRevision": VERIFIER.PINNED_REVISION,
            "toolchainImage": VERIFIER.PINNED_TOOLCHAIN,
            "reviewStatus": "approved",
            "components": {
                "zlib": {
                    "version": "1.3.2",
                    "licenseSpdx": ["Zlib"],
                    "sourceArchive": "zlib-1.3.2.tar.xz",
                }
            },
        }
        self.write_policy()
        self.candidate = self.base / "candidate.tar.gz"
        files = {
            "corresponding-source/kmediavlc/build.gradle.kts": b"plugins { base }\n",
            "corresponding-source/vlc/meson.build": b"project('vlc')\n",
            "corresponding-source/BUILD-TOOLCHAIN.txt": b"immutable toolchain\n",
            "corresponding-source/TOOLCHAIN-STATIC-ARCHIVES-SHA256SUMS": (
                b"0" * 64 + b"  /opt/llvm-mingw/lib/libunwind.a\n"
            ),
            "corresponding-source/toolchain-licenses/LICENSE.TXT": b"toolchain license\n",
            "corresponding-source/contrib-tarballs/zlib-1.3.2.tar.xz": b"zlib source\n",
        }
        with tarfile.open(self.candidate, "w:gz") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        self.archive = self.base / f"kmedia-vlc-{VERSION}-corresponding-source.tar.gz"
        PACKAGER.package(
            self.root,
            self.candidate,
            self.archive,
            COMMIT,
            VERSION,
            1_700_000_000,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_policy(self) -> None:
        self.policy_path.write_text(
            json.dumps(self.policy, indent=2) + "\n", encoding="utf-8"
        )

    def test_verifies_approved_version_commit_and_contrib_bytes(self) -> None:
        digest = VERIFIER.verify(self.root, self.archive, VERSION, COMMIT)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_rejects_a_different_release_identity(self) -> None:
        with self.assertRaises(ValueError):
            VERIFIER.verify(
                self.root,
                self.archive,
                VERSION,
                "89abcdef0123456789abcdef0123456789abcdef",
            )

    def test_rejects_a_policy_that_is_not_approved(self) -> None:
        self.policy["reviewStatus"] = "pending-link-command-audit"
        self.write_policy()
        with self.assertRaises(ValueError):
            VERIFIER.verify(self.root, self.archive, VERSION, COMMIT)


if __name__ == "__main__":
    unittest.main()
