# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INVENTORY = load_module(
    "create_windows_native_inventory_for_extract", "scripts/create_windows_native_inventory.py"
)
EXTRACTOR = load_module("extract_windows_candidate", "scripts/extract_windows_candidate.py")
VERSION = "0.1.0-rc.1"
SOURCE_OFFER = (
    "https://github.com/SuvioMedia/KMediaVlc/releases/download/"
    f"v{VERSION}/kmedia-vlc-{VERSION}-corresponding-source.tar.gz"
)


class ExtractWindowsCandidateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.staging = self.base / "staging"
        self.inventory = self.base / "inventory.json"
        _, _, modules = INVENTORY.load_policies(ROOT, allow_audit_candidate=True)
        paths = [
            "bin/kmediavlc_bridge.dll",
            "bin/libvlc.dll",
            "bin/libvlccore-9.dll",
            *(f"lib/vlc/plugins/lib{module}_plugin.dll" for module in modules),
            "lib/vlc/plugins/plugins.dat",
        ]
        for relative in paths:
            path = self.staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((relative + "\n").encode("ascii"))
        checksums = []
        for path in sorted(self.staging.rglob("*")):
            if path.is_file():
                relative = path.relative_to(self.staging).as_posix()
                checksums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}\n")
        (self.staging / "SHA256SUMS").write_text("".join(checksums), encoding="ascii")
        INVENTORY.create(
            ROOT,
            self.staging,
            self.inventory,
            VERSION,
            SOURCE_OFFER,
            allow_audit_candidate=True,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def archive(self, name: str) -> Path:
        archive = self.base / name
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as target:
            for path in sorted(self.staging.rglob("*")):
                if path.is_file():
                    target.write(path, path.relative_to(self.staging).as_posix())
        return archive

    def test_extracts_only_hash_bound_audit_candidate(self) -> None:
        output = self.base / "output"
        EXTRACTOR.extract(
            self.archive("candidate.zip"),
            self.inventory,
            output,
            allow_audit_candidate=True,
        )
        self.assertEqual(96, len([path for path in output.rglob("*") if path.is_file()]))
        self.assertEqual(
            (self.staging / "bin/libvlc.dll").read_bytes(),
            (output / "bin/libvlc.dll").read_bytes(),
        )

    def test_release_mode_rejects_pending_review(self) -> None:
        with self.assertRaises(ValueError):
            EXTRACTOR.extract(
                self.archive("pending.zip"),
                self.inventory,
                self.base / "pending-output",
            )

    def test_rejects_runtime_bytes_changed_after_inventory(self) -> None:
        (self.staging / "bin/libvlc.dll").write_bytes(b"tampered")
        with self.assertRaises(ValueError):
            EXTRACTOR.extract(
                self.archive("tampered.zip"),
                self.inventory,
                self.base / "tampered-output",
                allow_audit_candidate=True,
            )


if __name__ == "__main__":
    unittest.main()
