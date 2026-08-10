# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "stage_android_legal_evidence", ROOT / "scripts/stage_android_legal_evidence.py"
)
LEGAL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(LEGAL)


class AndroidLegalEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "root"
        self.vlc = self.base / "vlc"
        self.ndk = self.base / "ndk"
        self.output = self.base / "output"
        self.policy_path = self.root / "compliance/policy/android-static-components.json"
        self.source = self.vlc / "contrib/tarballs/demo-1.0.tar.xz"
        self.write_archive(self.source, {"LICENSE": b"demo license\n"})
        for name in ("NOTICE", "NOTICE.toolchain", "source.properties"):
            self.write(self.ndk / name, f"ndk {name}\n".encode("ascii"))
        self.policy = {
            "schemaVersion": 1,
            "target": "android-arm",
            "vlcRevision": LEGAL.VLC_REVISION,
            "ndkRevision": LEGAL.NDK_REVISION,
            "reviewStatus": "source-mapped-license-and-notice-review-pending",
            "contribComponents": {
                "demo": {"version": "1.0", "sourceArchives": ["demo-1.0.tar.xz"]}
            },
            "candidateLicenseSpdx": {"demo": ["MIT"]},
            "licenseEvidence": {"demo-1.0.tar.xz": ["LICENSE"]},
            "contribArchives": {"vlc-contrib/lib/libdemo.a": "demo"},
            "ndkComponents": {
                "android-ndk-llvm-runtime": {
                    "version": LEGAL.NDK_REVISION,
                    "candidateLicenseSpdx": ["Apache-2.0 WITH LLVM-exception"],
                    "evidenceFiles": ["NOTICE", "NOTICE.toolchain", "source.properties"],
                    "sourceStatus": "pending-corresponding-source-map",
                }
            },
            "ndkArchiveTemplates": {},
        }
        self.write_json(self.policy_path, self.policy)
        policy_sha256 = self.file_sha256(self.policy_path)
        license_value = b"demo license\n"
        source_component = {
            "id": "demo",
            "kind": "VLC_CONTRIB",
            "version": "1.0",
            "candidateLicenseSpdx": ["MIT"],
            "licenseReviewStatus": LEGAL.COMPONENT_REVIEW_STATUS,
            "sourceArchives": [
                {
                    "path": "vlc-contrib-tarballs/demo-1.0.tar.xz",
                    "sha256": self.file_sha256(self.source),
                    "size": self.source.stat().st_size,
                    "licenseEvidence": [
                        {
                            "path": "vlc-contrib-tarballs/demo-1.0.tar.xz!/LICENSE",
                            "sha256": hashlib.sha256(license_value).hexdigest(),
                            "size": len(license_value),
                        }
                    ],
                }
            ],
        }
        ndk_component = {
            "id": "android-ndk-llvm-runtime",
            "kind": "NDK_TOOLCHAIN",
            "version": LEGAL.NDK_REVISION,
            "candidateLicenseSpdx": ["Apache-2.0 WITH LLVM-exception"],
            "licenseReviewStatus": LEGAL.COMPONENT_REVIEW_STATUS,
            "sourceStatus": "pending-corresponding-source-map",
            "evidenceFiles": [
                {
                    "path": f"ndk/{name}",
                    "sha256": self.file_sha256(self.ndk / name),
                    "size": (self.ndk / name).stat().st_size,
                }
                for name in ("NOTICE", "NOTICE.toolchain", "source.properties")
            ],
        }
        self.components = [ndk_component, source_component]
        self.audit_paths = []
        for target in sorted(LEGAL.EXPECTED_TARGETS):
            report = {
                "schemaVersion": 1,
                "target": target,
                "vlcRevision": LEGAL.VLC_REVISION,
                "ndkRevision": LEGAL.NDK_REVISION,
                "reviewStatus": LEGAL.REVIEW_STATUS,
                "libvlc": {
                    "sha256": hashlib.sha256(target.encode("ascii")).hexdigest(),
                    "effectiveLicenseSpdx": None,
                },
                "staticComponents": self.components,
                "evidence": {
                    "staticComponentPolicy": {
                        "path": "compliance/policy/android-static-components.json",
                        "sha256": policy_sha256,
                    }
                },
            }
            path = self.base / f"{target}.json"
            self.write_json(path, report)
            self.audit_paths.append(path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write(path: Path, value: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    @staticmethod
    def write_json(path: Path, value: dict) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return path

    @staticmethod
    def write_archive(path: Path, members: dict[str, bytes]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, mode="w:xz") as archive:
            for relative, value in members.items():
                member = tarfile.TarInfo(f"source/{relative}")
                member.size = len(value)
                archive.addfile(member, io.BytesIO(value))
        return path

    @staticmethod
    def file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def stage(self) -> dict:
        return LEGAL.stage(
            self.root,
            self.vlc,
            self.ndk,
            self.audit_paths,
            self.output,
        )

    def test_stages_hash_bound_path_free_legal_bundle(self) -> None:
        manifest = self.stage()
        self.assertEqual(LEGAL.LEGAL_REVIEW_STATUS, manifest["reviewStatus"])
        self.assertIsNone(manifest["effectiveLicenseSpdx"])
        self.assertEqual(4, len(manifest["files"]))
        components = {component["id"]: component for component in manifest["components"]}
        self.assertEqual(
            "source-archive-hashes-recorded",
            components["demo"]["sourceStatus"],
        )
        self.assertEqual(
            [
                {
                    "path": "vlc-contrib-tarballs/demo-1.0.tar.xz",
                    "sha256": self.file_sha256(self.source),
                    "size": self.source.stat().st_size,
                }
            ],
            components["demo"]["sourceArchives"],
        )
        self.assertEqual(
            "pending-corresponding-source-map",
            components["android-ndk-llvm-runtime"]["sourceStatus"],
        )
        self.assertEqual(
            [], components["android-ndk-llvm-runtime"]["sourceArchives"]
        )
        self.assertEqual(
            b"demo license\n",
            (
                self.output
                / "contrib/demo/demo-1.0.tar.xz/LICENSE"
            ).read_bytes(),
        )
        rendered = (self.output / "android-static-legal.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.base), rendered)
        for entry in manifest["files"]:
            path = self.output / entry["path"]
            self.assertEqual(entry["sha256"], self.file_sha256(path))
            self.assertEqual(entry["size"], path.stat().st_size)

    def test_rejects_component_evidence_that_differs_between_abis(self) -> None:
        report = json.loads(self.audit_paths[1].read_text(encoding="utf-8"))
        report["staticComponents"][1]["candidateLicenseSpdx"] = ["BSD-2-Clause"]
        self.write_json(self.audit_paths[1], report)
        with self.assertRaisesRegex(ValueError, "do not have identical"):
            self.stage()

    def test_rejects_source_archive_changed_after_audit(self) -> None:
        self.write_archive(self.source, {"LICENSE": b"changed license\n"})
        with self.assertRaisesRegex(ValueError, "source hash differs"):
            self.stage()


if __name__ == "__main__":
    unittest.main()
