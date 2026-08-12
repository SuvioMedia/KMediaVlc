# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def load_module(name: str, relative: str):
    specification = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


MATRIX = load_module(
    "package_native_runtime_matrix_test",
    "scripts/package_native_runtime_matrix.py",
)


class PackageNativeRuntimeMatrixTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.matrix = self.base / "matrix.json"
        self.output = self.base / "output"
        payloads = []
        for target in MATRIX.REQUIRED_TARGETS:
            staging = self.base / target / "runtime"
            staging.mkdir(parents=True)
            inventory = self.base / target / "inventory.json"
            inventory.write_text("{}", encoding="utf-8")
            payloads.append(
                {
                    "target": target,
                    "staging": str(staging),
                    "inventory": str(inventory),
                }
            )
        self.payload = {"schemaVersion": 1, "payloads": payloads}
        self.write_matrix()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_matrix(self) -> None:
        self.matrix.write_text(json.dumps(self.payload), encoding="utf-8")

    @staticmethod
    def fake_package(
        root: Path,
        staging: Path,
        inventory: Path,
        target: str,
        source_offer: str,
        recipe_revision: str,
        output: Path,
    ) -> str:
        destination = output / "META-INF/kmediavlc/native" / target
        destination.mkdir(parents=True)
        (destination / "manifest.properties").write_text(
            f"target={target}\nsourceOffer={source_offer}\nrecipeRevision={recipe_revision}\n",
            encoding="utf-8",
        )
        return f"runtime-{target}"

    def test_packages_every_required_desktop_target(self) -> None:
        with mock.patch.object(
            MATRIX.package_native_runtime,
            "package",
            side_effect=self.fake_package,
        ) as package:
            runtime_ids = MATRIX.package_matrix(
                ROOT,
                self.matrix,
                "https://example.test/source.tar.gz",
                "a" * 40,
                self.output,
            )
        self.assertEqual(4, package.call_count)
        self.assertEqual(
            [f"runtime-{target}" for target in MATRIX.REQUIRED_TARGETS],
            runtime_ids,
        )
        self.assertEqual(
            set(MATRIX.REQUIRED_TARGETS),
            {
                path.name
                for path in (self.output / "META-INF/kmediavlc/native").iterdir()
            },
        )

    def test_real_packager_closes_the_complete_matrix(self) -> None:
        root = self.base / "policy-root"
        policy_root = root / "compliance/policy"
        policy_root.mkdir(parents=True)
        (policy_root / "release-policy.json").write_bytes(
            (ROOT / "compliance/policy/release-policy.json").read_bytes()
        )
        platform_policies = {
            "windows-x86_64": {
                "schemaVersion": 1,
                "target": "windows-x86_64",
                "vlcRevision": MATRIX.package_native_runtime.PINNED_REVISION,
                "reviewStatus": "approved",
            },
            "macos-aarch64": {
                "schemaVersion": 1,
                "target": "macos-aarch64",
                "vlcRevision": MATRIX.package_native_runtime.PINNED_REVISION,
                "reviewStatus": "approved",
            },
            "linux": {
                "schemaVersion": 1,
                "targets": ["linux-x86_64", "linux-aarch64"],
                "vlcRevision": MATRIX.package_native_runtime.PINNED_REVISION,
                "reviewStatus": "approved",
            },
        }
        for platform, policy in platform_policies.items():
            for kind in ("playback-modules", "binary-components"):
                (policy_root / f"{platform}-{kind}.json").write_text(
                    json.dumps(policy), encoding="utf-8"
                )

        engines = {
            "linux-aarch64": "GLES2",
            "linux-x86_64": "GLES2",
            "macos-aarch64": "OPENGL",
            "windows-x86_64": "D3D11",
        }
        roles = ["BRIDGE", "LIBVLC", "CORE", "PLUGIN"]
        for payload in self.payload["payloads"]:
            target = payload["target"]
            staging = Path(payload["staging"])
            files = []
            for role in roles:
                relative = role.lower()
                (staging / relative).write_bytes(f"{target}-{role}".encode("ascii"))
                files.append(
                    {
                        "path": relative,
                        "component": "kmediavlc" if role == "BRIDGE" else "videolan-vlc",
                        "licenseSpdx": "LGPL-2.1-or-later",
                        "role": role,
                        "source": "https://example.test/source.tar.gz",
                        "linkage": "DYNAMIC",
                    }
                )
            Path(payload["inventory"]).write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "provenance": "source-build",
                        "libvlcVersion": MATRIX.package_native_runtime.PINNED_VERSION,
                        "libvlcRevision": MATRIX.package_native_runtime.PINNED_REVISION,
                        "target": target,
                        "gplComponents": False,
                        "nonfreeComponents": False,
                        "frameDeliveryModes": ["GPU_PUSH", "CPU_PULL"],
                        "renderEngines": [engines[target]],
                        "pluginDirectory": "plugins",
                        "hdr10Metadata": target in {"macos-aarch64", "windows-x86_64"},
                        "files": files,
                    }
                ),
                encoding="utf-8",
            )
        self.write_matrix()
        runtime_ids = MATRIX.package_matrix(
            root,
            self.matrix,
            "https://example.test/source.tar.gz",
            "a" * 40,
            self.output,
        )
        self.assertEqual(4, len(runtime_ids))
        for target in MATRIX.REQUIRED_TARGETS:
            self.assertTrue(
                (self.output / "META-INF/kmediavlc/native" / target / "manifest.properties").is_file()
            )

    def test_rejects_partial_or_unsorted_matrix(self) -> None:
        for payloads in (
            self.payload["payloads"][:-1],
            list(reversed(self.payload["payloads"])),
        ):
            with self.subTest(count=len(payloads)):
                self.payload["payloads"] = payloads
                self.write_matrix()
                with self.assertRaises(ValueError):
                    MATRIX.load_matrix(self.matrix)
        self.payload["payloads"] = []

    def test_rejects_relative_payload_paths(self) -> None:
        self.payload["payloads"][0]["staging"] = "relative/runtime"
        self.write_matrix()
        with self.assertRaises(ValueError):
            MATRIX.load_matrix(self.matrix)


if __name__ == "__main__":
    unittest.main()
