# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import io
import json
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMMIT = "0123456789abcdef0123456789abcdef01234567"
VERSION = "0.1.0-rc.1"


def load_module(name: str, relative: str):
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


BASE_PACKAGER = load_module(
    "package_windows_source_for_desktop_test",
    "scripts/package_corresponding_source.py",
)
ASSEMBLER = load_module(
    "assemble_desktop_source_test",
    "scripts/assemble_desktop_corresponding_source.py",
)
VERIFIER = load_module(
    "verify_desktop_source_test",
    "scripts/verify_desktop_corresponding_source_archive.py",
)


class DesktopCorrespondingSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "repository"
        policy_root = self.root / "compliance/policy"
        scripts_root = self.root / "scripts"
        policy_root.mkdir(parents=True)
        scripts_root.mkdir(parents=True)
        shutil.copyfile(
            ROOT / "scripts/verify_corresponding_source_archive.py",
            scripts_root / "verify_corresponding_source_archive.py",
        )
        common = {
            "zlib": {
                "version": "1.3.2",
                "licenseSpdx": ["Zlib"],
                "sourceArchive": "zlib-1.3.2.tar.xz",
            }
        }
        self.write_policy(
            "windows-x86_64-binary-components.json",
            {
                "schemaVersion": 1,
                "target": "windows-x86_64",
                "vlcRevision": ASSEMBLER.PINNED_REVISION,
                "toolchainImage": "registry.videolan.org/vlc-debian-llvm-ucrt:20260611225331",
                "reviewStatus": "approved",
                "components": common,
            },
        )
        self.write_policy(
            "linux-binary-components.json",
            {
                "schemaVersion": 1,
                "targets": ["linux-x86_64", "linux-aarch64"],
                "vlcRevision": ASSEMBLER.PINNED_REVISION,
                "reviewStatus": "approved",
                "components": {
                    "dav1d": {
                        "version": "1.5.4",
                        "licenseSpdx": ["BSD-2-Clause"],
                        "sourceArchive": "dav1d-1.5.4.tar.xz",
                    },
                    **common,
                },
            },
        )
        self.write_policy(
            "macos-aarch64-binary-components.json",
            {
                "schemaVersion": 1,
                "target": "macos-aarch64",
                "vlcRevision": ASSEMBLER.PINNED_REVISION,
                "reviewStatus": "approved",
                "components": {
                    "libvpx": {
                        "version": "1.16.0",
                        "licenseSpdx": ["BSD-3-Clause"],
                        "sourceArchive": "libvpx-1.16.0.tar.gz",
                    },
                    **common,
                },
            },
        )
        self.base_candidate = self.base / "windows-candidate.tar.gz"
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
        with tarfile.open(self.base_candidate, "w:gz") as archive:
            for name, value in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(value)
                archive.addfile(info, io.BytesIO(value))
        self.windows_archive = self.base / "windows-source.tar.gz"
        BASE_PACKAGER.package(
            self.root,
            self.base_candidate,
            self.windows_archive,
            COMMIT,
            VERSION,
            1_700_000_000,
        )
        self.linux = self.base / "linux-inputs"
        self.macos = self.base / "macos-inputs"
        self.linux.mkdir()
        self.macos.mkdir()
        (self.linux / "zlib-1.3.2.tar.xz").write_bytes(b"zlib source\n")
        (self.linux / "dav1d-1.5.4.tar.xz").write_bytes(b"dav1d source\n")
        (self.macos / "libvpx-1.16.0.tar.gz").write_bytes(b"libvpx source\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_policy(self, name: str, value: dict) -> None:
        (self.root / "compliance/policy" / name).write_text(
            json.dumps(value, indent=2) + "\n",
            encoding="utf-8",
        )

    def assemble(self, name: str = "desktop-source.tar.gz") -> Path:
        output = self.base / name
        ASSEMBLER.assemble(
            self.root,
            self.windows_archive,
            [self.linux, self.macos],
            output,
            VERSION,
            COMMIT,
            1_700_000_000,
        )
        return output

    def test_assembles_and_independently_verifies_the_platform_union(self) -> None:
        archive = self.assemble()
        digest = VERIFIER.verify(self.root, archive, VERSION, COMMIT)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        with tarfile.open(archive, "r:gz") as source:
            manifest = json.load(
                source.extractfile("corresponding-source/SOURCE-MANIFEST.json")
            )
        self.assertEqual(ASSEMBLER.TARGETS, manifest["targets"])
        self.assertEqual(
            [
                "dav1d-1.5.4.tar.xz",
                "libvpx-1.16.0.tar.gz",
                "zlib-1.3.2.tar.xz",
            ],
            list(manifest["selectedContribSha256"]),
        )

    def test_is_deterministic(self) -> None:
        first = self.assemble("first.tar.gz")
        second = self.assemble("second.tar.gz")
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_rejects_conflicting_audit_source_bytes(self) -> None:
        (self.macos / "zlib-1.3.2.tar.xz").write_bytes(b"different zlib source\n")
        with self.assertRaises(ValueError):
            self.assemble()

    def test_rejects_a_missing_platform_source_archive(self) -> None:
        (self.macos / "libvpx-1.16.0.tar.gz").unlink()
        with self.assertRaises(ValueError):
            self.assemble()


if __name__ == "__main__":
    unittest.main()
