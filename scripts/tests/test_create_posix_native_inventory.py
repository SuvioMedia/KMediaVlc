# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1.0-rc.1"
SOURCE_OFFER = (
    "https://github.com/SuvioMedia/KMediaVlc/releases/download/"
    f"v{VERSION}/kmedia-vlc-{VERSION}-corresponding-source.tar.gz"
)


def load_module(name: str, relative: str):
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


INVENTORY = load_module(
    "create_posix_native_inventory_test",
    "scripts/create_posix_native_inventory.py",
)
PACKAGER = load_module("package_native_runtime_posix_test", "scripts/package_native_runtime.py")


class CreatePosixNativeInventoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def candidate(
        self,
        target: str,
        root: Path = ROOT,
    ) -> tuple[Path, Path, Path, dict]:
        staging = self.base / f"{target}-runtime"
        report_path = self.base / f"{target}-report.json"
        output = self.base / f"{target}-inventory.json"
        playback, binary = INVENTORY.load_policies(root, target, True)
        families = playback["modulesByFamily"]
        modules = [name for family in sorted(families) for name in families[family]]
        suffix = ".dylib" if target == "macos-aarch64" else ".so"
        fixed = [
            (f"bin/libkmediavlc_bridge{suffix}", "BRIDGE", [], None),
            (
                "bin/libvlc.12.dylib" if target == "macos-aarch64" else "bin/libvlc.so.12",
                "LIBVLC",
                [],
                None,
            ),
            (
                "bin/libvlccore.9.dylib"
                if target == "macos-aarch64"
                else "bin/libvlccore.so.9",
                "CORE",
                binary["coreComponents"],
                None,
            ),
        ]
        if target.startswith("linux-"):
            fixed.extend(
                (f"bin/{name}", "SUPPORT", [], None)
                for name in binary["runtimeSupportLibraries"]
            )
        entries: list[dict] = []
        for relative, role, source_components, module in fixed:
            entries.append(
                self.write_entry(staging, relative, role, source_components, module)
            )
        for module in modules:
            filename = (
                f"lib{module}_plugin.dylib"
                if target == "macos-aarch64"
                else f"lib{module}_plugin.so"
            )
            entries.append(
                self.write_entry(
                    staging,
                    f"lib/vlc/plugins/{filename}",
                    "PLUGIN",
                    binary["moduleComponents"].get(module, []),
                    module,
                )
            )
        entries.append(
            self.write_entry(
                staging,
                "lib/vlc/plugins/plugins.dat",
                "DATA",
                [],
                None,
            )
        )
        report = {
            "schemaVersion": 1,
            "target": target,
            "vlcRevision": INVENTORY.PINNED_REVISION,
            "reviewStatus": playback["reviewStatus"],
            "binaryReviewStatus": binary["reviewStatus"],
            "auditCandidate": (
                playback["reviewStatus"] != "approved"
                or binary["reviewStatus"] != "approved"
            ),
            "files": entries,
        }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        return staging, report_path, output, report

    def write_entry(
        self,
        staging: Path,
        relative: str,
        role: str,
        source_components: list[str],
        module: str | None,
    ) -> dict:
        path = staging / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(relative.encode("ascii"))
        entry = {
            "path": relative,
            "role": role,
            "sourceComponents": source_components,
            "size": path.stat().st_size,
            "sha256": self.digest(path),
        }
        if module is not None:
            entry["module"] = module
        return entry

    def test_creates_packager_valid_linux_and_macos_inventories(self) -> None:
        expected_counts = {
            "linux-x86_64": 91,
            "linux-aarch64": 91,
            "macos-aarch64": 94,
        }
        for target, expected_count in expected_counts.items():
            with self.subTest(target=target):
                staging, report, output, _ = self.candidate(target)
                inventory = INVENTORY.create(
                    ROOT,
                    staging,
                    report,
                    output,
                    target,
                    VERSION,
                    SOURCE_OFFER,
                )
                self.assertEqual(expected_count, len(inventory["files"]))
                self.assertTrue((staging / INVENTORY.AUDIT_NAME).is_file())
                core = next(
                    entry
                    for entry in inventory["files"]
                    if entry["role"] == "CORE"
                )
                self.assertIn("MIT", core["licenseSpdx"])
                mkv = next(
                    entry
                    for entry in inventory["files"]
                    if entry["path"].endswith(
                        "libmkv_plugin.dylib"
                        if target == "macos-aarch64"
                        else "libmkv_plugin.so"
                    )
                )
                self.assertIn("BSL-1.0", mkv["licenseSpdx"])
                validated = PACKAGER.validate_inventory(
                    inventory,
                    PACKAGER.load_policy(ROOT),
                    target,
                    staging,
                    output,
                )
                self.assertEqual(expected_count, len(validated))

    def test_maps_support_library_to_dynamic_dependency(self) -> None:
        staging, report, output, _ = self.candidate("linux-x86_64")
        inventory = INVENTORY.create(
            ROOT,
            staging,
            report,
            output,
            "linux-x86_64",
            VERSION,
            SOURCE_OFFER,
        )
        support = next(
            entry
            for entry in inventory["files"]
            if entry["path"].endswith("libvlc_pulse.so")
        )
        self.assertEqual("DEPENDENCY", support["role"])
        self.assertEqual("DYNAMIC", support["linkage"])

    def test_release_mode_rejects_pending_platform_reviews(self) -> None:
        pending_root = self.base / "pending-macos-root"
        files = {
            "scripts/stage_vlc_macos_runtime.py": None,
            "build-recipes/macos.json": None,
            "compliance/policy/macos-aarch64-playback-modules.json": (
                "pending-mach-o-and-source-license-audit"
            ),
            "compliance/policy/macos-aarch64-binary-components.json": (
                "pending-link-command-and-license-audit"
            ),
        }
        for relative, pending_status in files.items():
            source = ROOT / relative
            destination = pending_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if pending_status is None:
                shutil.copy2(source, destination)
                continue
            payload = json.loads(source.read_text(encoding="utf-8"))
            payload["reviewStatus"] = pending_status
            destination.write_text(json.dumps(payload), encoding="utf-8")

        staging, report, output, _ = self.candidate(
            "macos-aarch64",
            pending_root,
        )
        with self.assertRaises(ValueError):
            INVENTORY.create(
                pending_root,
                staging,
                report,
                output,
                "macos-aarch64",
                VERSION,
                SOURCE_OFFER,
            )

    def test_rejects_report_hash_drift(self) -> None:
        staging, report, output, payload = self.candidate("linux-x86_64")
        payload["files"][0]["sha256"] = "0" * 64
        report.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(ValueError):
            INVENTORY.create(
                ROOT,
                staging,
                report,
                output,
                "linux-x86_64",
                VERSION,
                SOURCE_OFFER,
            )


if __name__ == "__main__":
    unittest.main()
