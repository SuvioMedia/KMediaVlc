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
            "libvlccore-9.dll",
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
        revision = "0123456789abcdef0123456789abcdef01234567"
        manifest = PACKAGER.manifest_text(
            self.inventory, files, "kmediavlc4-0123456789abcdef", "source.tar.gz", revision
        )
        self.assertIn(f"recipeRevision={revision}\n", manifest)
        self.assertIn(f"bridge.abiVersion={PACKAGER.BRIDGE_ABI_VERSION}\n", manifest)

    def test_rejects_policy_for_a_different_bridge_abi(self) -> None:
        self.policy["bridgeAbiVersion"] = PACKAGER.BRIDGE_ABI_VERSION - 1
        with self.assertRaises(SystemExit):
            self.validate()

    def test_rejects_non_commit_recipe_revision(self) -> None:
        self.assertEqual(
            "0123456789abcdef0123456789abcdef01234567",
            PACKAGER.validate_recipe_revision("0123456789abcdef0123456789abcdef01234567"),
        )
        for revision in ("main", "A" * 40, "a" * 39, "a" * 41):
            with self.subTest(revision=revision), self.assertRaises(SystemExit):
                PACKAGER.validate_recipe_revision(revision)

    def test_accepts_canonical_alias_of_staging_directory(self) -> None:
        aliased_staging = self.staging / ".." / self.staging.name
        files = PACKAGER.validate_inventory(
            self.inventory, self.policy, "windows-x86_64", aliased_staging
        )
        self.assertEqual(4, len(files))

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
