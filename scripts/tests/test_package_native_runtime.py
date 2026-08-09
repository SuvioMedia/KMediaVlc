# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "package_native_runtime", ROOT / "scripts/package_native_runtime.py"
)
assert SPEC is not None and SPEC.loader is not None
PACKAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGER)


class PackageNativeRuntimePolicyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.staging = self.root / "staging"
        paths = [
            "kmediavlc_bridge.dll",
            "libvlc.dll",
            "libvlccore.dll",
            "plugins/codec/libcodec_plugin.dll",
        ]
        for relative in paths:
            path = self.staging / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(relative.encode("ascii"))
        roles = ["BRIDGE", "LIBVLC", "CORE", "PLUGIN"]
        licenses = [
            "LicenseRef-KMediaVlc-Proprietary",
            "LGPL-2.1-or-later",
            "LGPL-2.1-or-later",
            "LGPL-2.1-or-later",
        ]
        self.inventory = {
            "schemaVersion": 1,
            "provenance": "source-build",
            "libvlcVersion": PACKAGER.PINNED_VERSION,
            "libvlcRevision": PACKAGER.PINNED_REVISION,
            "target": "windows-x86_64",
            "gplComponents": False,
            "nonfreeComponents": False,
            "frameDeliveryModes": ["GPU_PUSH", "CPU_PULL"],
            "renderEngines": ["D3D11"],
            "pluginDirectory": "plugins",
            "hdr10Metadata": True,
            "files": [
                {
                    "path": path,
                    "component": "kmediavlc" if index == 0 else "videolan-vlc",
                    "licenseSpdx": licenses[index],
                    "role": roles[index],
                    "source": "sources/kmediavlc.tar.gz" if index == 0 else "sources/vlc.tar.xz",
                    "linkage": "DYNAMIC",
                }
                for index, path in enumerate(paths)
            ],
        }
        self.inventory_path = self.root / "inventory.json"
        self.inventory_path.write_text(json.dumps(self.inventory), encoding="utf-8")
        PACKAGER.inventory_path_global = self.inventory_path
        self.policy = PACKAGER.load_policy(ROOT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def validate(self) -> list[dict]:
        return PACKAGER.validate_inventory(
            self.inventory, self.policy, "windows-x86_64", self.staging
        )

    def test_accepts_exact_source_built_dynamic_inventory(self) -> None:
        files = self.validate()
        self.assertEqual(4, len(files))
        self.assertTrue(all(len(entry["sha256"]) == 64 for entry in files))

    def test_rejects_gpl_or_static_component(self) -> None:
        self.inventory["files"][3]["licenseSpdx"] = "GPL-2.0-or-later"
        with self.assertRaises(SystemExit):
            self.validate()
        self.inventory["files"][3]["licenseSpdx"] = "LGPL-2.1-or-later"
        self.inventory["files"][3]["linkage"] = "STATIC"
        with self.assertRaises(SystemExit):
            self.validate()

    def test_rejects_stock_nightly_and_uninventoried_file(self) -> None:
        self.inventory["provenance"] = "videolan-nightly"
        with self.assertRaises(SystemExit):
            self.validate()
        self.inventory["provenance"] = "source-build"
        (self.staging / "plugins/codec/extra.dll").write_bytes(b"extra")
        with self.assertRaises(SystemExit):
            self.validate()


if __name__ == "__main__":
    unittest.main()
