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
        self.ndk_host = self.ndk / "toolchains/llvm/prebuilt/linux-x86_64"
        self.output = self.base / "output"
        self.policy_path = self.root / "compliance/policy/android-static-components.json"
        self.source = self.vlc / "contrib/tarballs/demo-1.0.tar.xz"
        self.write_archive(self.source, {"LICENSE": b"demo license\n"})
        for name in ("NOTICE", "NOTICE.toolchain", "source.properties"):
            self.write(self.ndk / name, f"ndk {name}\n".encode("ascii"))
        for name in ("AndroidVersion.txt", "clang_source_info.md"):
            self.write(self.ndk_host / name, f"ndk {name}\n".encode("ascii"))
        self.ndk_source_inputs = {
            "llvm-android-build": {
                "repository": "https://android.googlesource.com/toolchain/llvm_android",
                "revision": LEGAL.LLVM_ANDROID_REVISION,
                "tree": "1" * 40,
                "role": "android-runtime-build-and-patch-set",
                "requiredPaths": ["do_build.py"],
            },
            "llvm-project": {
                "repository": "https://android.googlesource.com/toolchain/llvm-project",
                "revision": LEGAL.LLVM_PROJECT_REVISION,
                "tree": "2" * 40,
                "role": "linked-runtime-source",
                "requiredPaths": ["compiler-rt/lib/builtins"],
            },
        }
        self.ndk_release = {
            "releaseName": "r29",
            "clangVersion": "21.0.0",
            "clangRevision": "r563880c",
            "ndkRepository": "https://android.googlesource.com/platform/ndk",
            "prebuiltTags": {
                "linux-x86_64": {
                    "repository": (
                        "https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86"
                    ),
                    "tagObject": "3" * 40,
                    "commit": "4" * 40,
                }
            },
        }
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
                    "toolchainEvidenceFiles": [
                        "AndroidVersion.txt",
                        "clang_source_info.md",
                    ],
                    "sourceInputs": ["llvm-android-build", "llvm-project"],
                    "sourceStatus": LEGAL.NDK_SOURCE_STATUS,
                }
            },
            "ndkSourceInputs": self.ndk_source_inputs,
            "ndkReleaseProvenance": self.ndk_release,
            "ndkArchiveTemplates": {},
            "ndkArchiveSourcePaths": {},
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
            "sourceStatus": LEGAL.NDK_SOURCE_STATUS,
            "sourceInputs": [
                {"id": source_id, **value}
                for source_id, value in self.ndk_source_inputs.items()
            ],
            "binaryProvenance": {
                **{
                    key: value
                    for key, value in self.ndk_release.items()
                    if key != "prebuiltTags"
                },
                "prebuilt": {
                    "hostTag": "linux-x86_64",
                    **self.ndk_release["prebuiltTags"]["linux-x86_64"],
                },
            },
            "evidenceFiles": [
                {
                    "path": f"ndk/{name}",
                    "sha256": self.file_sha256(
                        (self.ndk if name in {"NOTICE", "NOTICE.toolchain", "source.properties"} else self.ndk_host)
                        / name
                    ),
                    "size": (
                        (self.ndk if name in {"NOTICE", "NOTICE.toolchain", "source.properties"} else self.ndk_host)
                        / name
                    ).stat().st_size,
                }
                for name in (
                    "NOTICE",
                    "NOTICE.toolchain",
                    "source.properties",
                    "AndroidVersion.txt",
                    "clang_source_info.md",
                )
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
        self.assertEqual(6, len(manifest["files"]))
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
            LEGAL.NDK_SOURCE_STATUS,
            components["android-ndk-llvm-runtime"]["sourceStatus"],
        )
        self.assertEqual(
            [], components["android-ndk-llvm-runtime"]["sourceArchives"]
        )
        self.assertEqual(
            ["llvm-android-build", "llvm-project"],
            [
                entry["id"]
                for entry in components["android-ndk-llvm-runtime"]["sourceInputs"]
            ],
        )
        self.assertEqual(
            "linux-x86_64",
            components["android-ndk-llvm-runtime"]["binaryProvenance"]["prebuilt"][
                "hostTag"
            ],
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
