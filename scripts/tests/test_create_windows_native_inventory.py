# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


INVENTORY = load_module(
    "create_windows_native_inventory", "scripts/create_windows_native_inventory.py"
)
PACKAGER = load_module("package_native_runtime", "scripts/package_native_runtime.py")
VERSION = "0.1.0-rc.1"
SOURCE_OFFER = (
    "https://github.com/SuvioMedia/KMediaVlc/releases/download/"
    f"v{VERSION}/kmedia-vlc-{VERSION}-corresponding-source.tar.gz"
)


class CreateWindowsNativeInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.staging = self.base / "staging"
        self.output = self.base / "inventory.json"
        _, _, modules = INVENTORY.load_policies(ROOT, allow_audit_candidate=True)
        paths = [
            "bin/kmediavlc_bridge.dll",
            "bin/libvlc.dll",
            "bin/libvlccore-9.dll",
            *(f"lib/vlc/plugins/lib{module}_plugin.dll" for module in modules),
            "lib/vlc/plugins/plugins.dat",
            "SHA256SUMS",
        ]
        for relative in paths:
            path = self.staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode("ascii"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_creates_packager_valid_closed_audit_inventory(self) -> None:
        inventory = INVENTORY.create(
            ROOT,
            self.staging,
            self.output,
            VERSION,
            SOURCE_OFFER,
            allow_audit_candidate=True,
        )
        self.assertEqual(96, len(inventory["files"]))
        self.assertTrue((self.staging / INVENTORY.AUDIT_NAME).is_file())
        avcodec = next(
            entry for entry in inventory["files"] if entry["path"].endswith("libavcodec_plugin.dll")
        )
        self.assertIn("TU-Berlin-1.0", avcodec["licenseSpdx"])
        audit = json.loads((self.staging / INVENTORY.AUDIT_NAME).read_text(encoding="utf-8"))
        self.assertEqual(["ffmpeg", "gsm", "openjpeg", "zlib"], next(
            entry["sourceComponents"]
            for entry in audit["runtimeFiles"]
            if entry["path"].endswith("libavcodec_plugin.dll")
        ))

        PACKAGER.inventory_path_global = self.output
        validated = PACKAGER.validate_inventory(
            inventory,
            PACKAGER.load_policy(ROOT),
            "windows-x86_64",
            self.staging,
        )
        self.assertEqual(96, len(validated))

    def test_release_mode_rejects_pending_link_review(self) -> None:
        with self.assertRaises(ValueError):
            INVENTORY.create(
                ROOT,
                self.staging,
                self.output,
                VERSION,
                SOURCE_OFFER,
            )

    def test_rejects_an_uninventoried_staging_file(self) -> None:
        extra = self.staging / "bin/vlc.exe"
        extra.write_bytes(b"forbidden player executable")
        with self.assertRaises(ValueError):
            INVENTORY.create(
                ROOT,
                self.staging,
                self.output,
                VERSION,
                SOURCE_OFFER,
                allow_audit_candidate=True,
            )


if __name__ == "__main__":
    unittest.main()
