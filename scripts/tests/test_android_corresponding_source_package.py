# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERSION = "0.1.0-rc.1"


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PACKAGER = load_module(
    "package_android_corresponding_source_test",
    "scripts/package_android_corresponding_source.py",
)
VERIFIER = load_module(
    "verify_android_corresponding_source_test",
    "scripts/verify_android_corresponding_source_archive.py",
)
NDK_PACKAGER = load_module(
    "package_android_ndk_for_corresponding_test",
    "scripts/package_android_ndk_source.py",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AndroidCorrespondingSourcePackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "kmediavlc"
        self.vlc = self.base / "vlc"
        self.libvlcjni = self.base / "libvlcjni"
        self.llvm_project = self.base / "llvm-project"
        self.llvm_android = self.base / "llvm-android"
        self.contrib = self.base / "contrib-tarballs"
        self.external = self.base / "external"
        self.contrib.mkdir()
        self.external.mkdir()

        self.llvm_android_identity = self.create_repository(
            self.llvm_android,
            {
                "do_build.py": b"print('build')\n",
                "patches/runtime.patch": b"runtime patch\n",
                "src/llvm_android/android_version.py": b"version = 29\n",
                "src/llvm_android/builders.py": b"class Runtime: pass\n",
            },
        )
        self.llvm_project_identity = self.create_repository(
            self.llvm_project,
            {
                "LICENSE.TXT": b"Apache-2.0 WITH LLVM-exception\n",
                "README.md": b"LLVM runtime sources\n",
                "compiler-rt/lib/builtins/add.c": b"int add(int a, int b) { return a + b; }\n",
                "libcxx/vector.cpp": b"// libcxx\n",
                "libcxxabi/abi.cpp": b"// libcxxabi\n",
                "libunwind/unwind.cpp": b"// libunwind\n",
                "runtimes/CMakeLists.txt": b"project(runtimes)\n",
            },
        )
        self.vlc_identity = self.create_repository(
            self.vlc,
            {
                "COPYING": b"LGPL source\n",
                "contrib/src/demo/rules.mak": b"DEMO_VERSION := 1\n",
                "include/vlc/libvlc.h": b"void libvlc_new(void);\n",
                "lib/meson.build": b"libvlc = library('vlc')\n",
                "meson.build": b"project('vlc')\n",
                "modules/demo.c": b"/* VLC module */\n",
                "src/meson.build": b"libvlccore = library('vlccore')\n",
            },
        )
        self.libvlcjni_identity = self.create_repository(
            self.libvlcjni,
            {
                "LICENSE": b"LGPL source\n",
                "buildsystem/compile-libvlc.sh": b"#!/bin/sh\nexit 0\n",
                "libvlc/jni/libvlc.mk": b"LOCAL_MODULE := vlc\n",
                "libvlc/jni/libvlcjni.mk": b"LOCAL_MODULE := vlcjni\n",
            },
        )

        self.component_sources: dict[str, list[str]] = {}
        archive_index = 0
        for component_index in range(54):
            count = 2 if component_index == 0 else 1
            names = []
            for _ in range(count):
                name = f"source{archive_index:02d}.tar.gz"
                (self.contrib / name).write_bytes(f"source archive {archive_index}\n".encode("ascii"))
                names.append(name)
                archive_index += 1
            self.component_sources[f"component{component_index:02d}"] = names
        self.assertEqual(55, archive_index)

        static_policy = self.static_policy()
        corresponding_policy = self.corresponding_policy()
        recipe = {
            "vlcRevision": self.vlc_identity[0],
            "libvlcjniRevision": self.libvlcjni_identity[0],
            "ndkVersion": "29.0.14206865",
            "correspondingSourcePackagePolicy": (
                "compliance/policy/android-corresponding-source.json"
            ),
            "requiresCompleteCorrespondingSourcePackage": True,
            "requiresIndependentCorrespondingSourceVerification": True,
        }
        root_files = {
            "build-recipes/android.json": self.json_bytes(recipe),
            "compliance/policy/android-corresponding-source.json": self.json_bytes(
                corresponding_policy
            ),
            "compliance/policy/android-static-components.json": self.json_bytes(static_policy),
            "scripts/build_vlc_android.sh": b"#!/bin/sh\nexit 0\n",
        }
        self.tested_commit, _ = self.create_repository(self.root, root_files)
        self.epoch = int(self.git(self.root, "show", "-s", "--format=%ct", "HEAD"))

        self.audits: dict[str, Path] = {}
        audit_entries = []
        static_digest = sha256(
            self.root / "compliance/policy/android-static-components.json"
        )
        for target, abi, marker in (
            ("android-arm64-v8a", "arm64-v8a", "a"),
            ("android-armeabi-v7a", "armeabi-v7a", "b"),
        ):
            libvlc_digest = marker * 64
            audit = {
                "schemaVersion": 1,
                "target": target,
                "abi": abi,
                "androidApi": 21,
                "vlcRevision": self.vlc_identity[0],
                "libvlcjniRevision": self.libvlcjni_identity[0],
                "ndkRevision": "29.0.14206865",
                "reviewStatus": "candidate-source-mapped-license-review-pending",
                "libvlc": {"sha256": libvlc_digest},
                "modules": [{"name": "demo"}],
                "staticArchives": [{"path": "demo.a"}],
                "staticComponents": [
                    {"id": "android-ndk-llvm-runtime"},
                    *({"id": component_id} for component_id in sorted(self.component_sources)),
                ],
                "evidence": {
                    "staticComponentPolicy": {
                        "path": "compliance/policy/android-static-components.json",
                        "sha256": static_digest,
                    }
                },
            }
            audit_path = self.external / f"{abi}.json"
            audit_path.write_bytes(self.json_bytes(audit))
            self.audits[target] = audit_path
            audit_entries.append(
                {
                    "target": target,
                    "reportSha256": sha256(audit_path),
                    "libvlcSha256": libvlc_digest,
                }
            )

        components = [
            {
                "id": "android-ndk-llvm-runtime",
                "kind": "NDK_TOOLCHAIN",
                "sourceStatus": "exact-source-revisions-recorded-source-package-pending",
                "sourceArchives": [],
            }
        ]
        for component_id, names in sorted(self.component_sources.items()):
            components.append(
                {
                    "id": component_id,
                    "kind": "VLC_CONTRIB",
                    "sourceStatus": "source-archive-hashes-recorded",
                    "sourceArchives": [
                        {
                            "path": f"vlc-contrib-tarballs/{name}",
                            "sha256": sha256(self.contrib / name),
                            "size": (self.contrib / name).stat().st_size,
                        }
                        for name in names
                    ],
                }
            )
        legal = {
            "schemaVersion": 1,
            "vlcRevision": self.vlc_identity[0],
            "ndkRevision": "29.0.14206865",
            "reviewStatus": "candidate-linked-member-review-pending",
            "effectiveLicenseSpdx": None,
            "candidateLicenseInventorySpdx": [],
            "staticComponentPolicy": {
                "path": "compliance/policy/android-static-components.json",
                "sha256": static_digest,
            },
            "abiAudits": audit_entries,
            "files": [],
            "components": sorted(components, key=lambda value: value["id"]),
        }
        self.legal_manifest = self.external / "android-static-legal.json"
        self.legal_manifest.write_bytes(self.json_bytes(legal))

        self.ndk_archive = self.external / "android-ndk-source.tar.gz"
        NDK_PACKAGER.package(
            self.root,
            self.llvm_project,
            self.llvm_android,
            self.ndk_archive,
            self.tested_commit,
            VERSION,
            self.epoch,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def json_bytes(value: object) -> bytes:
        return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")

    @staticmethod
    def git(repository: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()

    def create_repository(self, repository: Path, files: dict[str, bytes]) -> tuple[str, str]:
        repository.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(repository)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for relative, value in files.items():
            path = repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        self.git(repository, "add", ".")
        self.git(
            repository,
            "-c",
            "user.name=KMediaVlc Test",
            "-c",
            "user.email=test.invalid@example.invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        )
        return self.git(repository, "rev-parse", "HEAD"), self.git(
            repository, "rev-parse", "HEAD^{tree}"
        )

    def static_policy(self) -> dict:
        return {
            "schemaVersion": 1,
            "target": "android-arm",
            "vlcRevision": self.vlc_identity[0],
            "ndkRevision": "29.0.14206865",
            "contribComponents": {
                component_id: {"version": "1", "sourceArchives": names}
                for component_id, names in sorted(self.component_sources.items())
            },
            "ndkSourceInputs": {
                "llvm-android-build": {
                    "repository": "https://example.invalid/llvm_android",
                    "revision": self.llvm_android_identity[0],
                    "tree": self.llvm_android_identity[1],
                    "role": "android-runtime-build-and-patch-set",
                    "requiredPaths": [
                        "do_build.py",
                        "patches",
                        "src/llvm_android/android_version.py",
                        "src/llvm_android/builders.py",
                    ],
                },
                "llvm-project": {
                    "repository": "https://example.invalid/llvm-project",
                    "revision": self.llvm_project_identity[0],
                    "tree": self.llvm_project_identity[1],
                    "role": "linked-runtime-source",
                    "requiredPaths": [
                        "compiler-rt/lib/builtins",
                        "libcxx",
                        "libcxxabi",
                        "libunwind",
                        "runtimes",
                    ],
                },
            },
            "ndkSourcePackage": {
                "archiveRoot": "android-ndk-runtime-source",
                "format": "deterministic-tar-gzip-v1",
                "verifiedSourceStatus": "corresponding-source-mapped",
                "sources": {
                    "llvm-android-build": {"scope": "complete-tree", "paths": []},
                    "llvm-project": {
                        "scope": "selected-subtrees",
                        "paths": [
                            "LICENSE.TXT",
                            "README.md",
                            "compiler-rt",
                            "libcxx",
                            "libcxxabi",
                            "libunwind",
                            "runtimes",
                        ],
                    },
                },
            },
            "ndkReleaseProvenance": {"releaseName": "test-r29"},
            "ndkArchiveSourcePaths": {
                "ndk/runtime.a": ["llvm-project/compiler-rt/lib/builtins"]
            },
        }

    def corresponding_policy(self) -> dict:
        return {
            "schemaVersion": 1,
            "target": "android-arm",
            "archiveRoot": "android-corresponding-source",
            "format": "deterministic-tar-gzip-v1",
            "verifiedClosureStatus": "complete-source-and-relink-inputs-packaged",
            "sourceInputs": {
                "kmediavlc": {
                    "repository": "https://example.invalid/KMediaVlc.git",
                    "revisionBinding": "tested-commit",
                    "scope": "complete-tree",
                    "requiredPaths": [
                        "build-recipes/android.json",
                        "compliance/policy/android-corresponding-source.json",
                        "compliance/policy/android-static-components.json",
                        "scripts/build_vlc_android.sh",
                    ],
                },
                "libvlcjni": {
                    "repository": "https://example.invalid/libvlcjni.git",
                    "revision": self.libvlcjni_identity[0],
                    "tree": self.libvlcjni_identity[1],
                    "scope": "complete-tree",
                    "requiredPaths": [
                        "LICENSE",
                        "buildsystem/compile-libvlc.sh",
                        "libvlc/jni/libvlc.mk",
                        "libvlc/jni/libvlcjni.mk",
                    ],
                },
                "vlc": {
                    "repository": "https://example.invalid/vlc.git",
                    "revision": self.vlc_identity[0],
                    "tree": self.vlc_identity[1],
                    "scope": "complete-tree",
                    "requiredPaths": [
                        "COPYING",
                        "contrib/src",
                        "include/vlc/libvlc.h",
                        "lib/meson.build",
                        "meson.build",
                        "modules",
                        "src/meson.build",
                    ],
                },
            },
            "contribSourceArchives": {
                "componentPolicy": "compliance/policy/android-static-components.json",
                "archiveDirectory": "sources/vlc-contrib-tarballs",
                "archiveCount": 55,
            },
            "ndkSourcePackage": {
                "componentPolicy": "compliance/policy/android-static-components.json",
                "archivePath": "source-packages/android-ndk-runtime-source.tar.gz",
                "archiveRoot": "android-ndk-runtime-source",
                "format": "deterministic-tar-gzip-v1",
                "requiresIndependentVerification": True,
            },
            "buildEvidence": {
                "legalManifestPath": "build-evidence/android-static-legal.json",
                "linkAudits": {
                    "android-arm64-v8a": "build-evidence/link-audits/android-arm64-v8a.json",
                    "android-armeabi-v7a": "build-evidence/link-audits/android-armeabi-v7a.json",
                },
            },
            "generatedFiles": ["REBUILD.md", "SOURCE-SHA256SUMS"],
        }

    def package(self, name: str) -> Path:
        output = self.external / name
        PACKAGER.package(
            self.root,
            self.vlc,
            self.libvlcjni,
            self.contrib,
            self.ndk_archive,
            self.llvm_project,
            self.llvm_android,
            self.legal_manifest,
            self.audits["android-arm64-v8a"],
            self.audits["android-armeabi-v7a"],
            output,
            self.tested_commit,
            VERSION,
            self.epoch,
        )
        return output

    def verify(self, archive: Path, tested_commit: str | None = None) -> str:
        return VERIFIER.verify(
            self.root,
            archive,
            self.vlc,
            self.libvlcjni,
            self.contrib,
            self.ndk_archive,
            self.llvm_project,
            self.llvm_android,
            self.legal_manifest,
            self.audits["android-arm64-v8a"],
            self.audits["android-armeabi-v7a"],
            VERSION,
            tested_commit or self.tested_commit,
        )

    def test_packages_complete_closure_deterministically_and_verifies(self) -> None:
        first = self.package("first.tar.gz")
        second = self.package("second.tar.gz")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(sha256(first), self.verify(first))
        with tarfile.open(first, "r:gz") as archive:
            manifest = json.load(
                archive.extractfile(
                    "android-corresponding-source/SOURCE-MANIFEST.json"
                )
            )
        self.assertEqual(55, len(manifest["contribSourceArchives"]))
        self.assertEqual(3, len(manifest["sourceInputs"]))
        self.assertEqual(
            "complete-source-and-relink-inputs-packaged",
            manifest["verifiedClosureStatus"],
        )

    def test_rejects_tracked_source_modification_in_packager_and_verifier(self) -> None:
        archive = self.package("clean.tar.gz")
        (self.vlc / "meson.build").write_text("project('changed')\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "tracked modifications"):
            self.package("modified.tar.gz")
        with self.assertRaisesRegex(ValueError, "tracked modifications"):
            self.verify(archive)

    def test_rejects_contrib_bytes_that_differ_from_legal_evidence(self) -> None:
        archive = self.package("legal.tar.gz")
        (self.contrib / "source00.tar.gz").write_bytes(b"changed source archive\n")
        with self.assertRaisesRegex(ValueError, "differs from the legal audit"):
            self.package("changed-contrib.tar.gz")
        with self.assertRaisesRegex(ValueError, "differs from legal evidence"):
            self.verify(archive)

    def test_rejects_a_different_tested_commit(self) -> None:
        archive = self.package("identity.tar.gz")
        other = "0123456789abcdef0123456789abcdef01234567"
        with self.assertRaises(ValueError):
            self.verify(archive, other)

    def test_rejects_tampered_packaged_source_member(self) -> None:
        archive = self.package("original.tar.gz")
        tampered = self.external / "tampered.tar.gz"
        with tarfile.open(archive, "r:gz") as source, tampered.open("xb") as raw:
            with gzip.GzipFile(
                filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=self.epoch
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as target:
                    for member in source.getmembers():
                        if member.isdir():
                            target.addfile(member)
                            continue
                        stream = source.extractfile(member)
                        assert stream is not None
                        value = stream.read()
                        if member.name.endswith("sources/vlc/meson.build"):
                            value = b"project('tampered')\n"
                            member.size = len(value)
                        target.addfile(member, io.BytesIO(value))
        with self.assertRaisesRegex(ValueError, "manifest differs|mode or size differs|member differs"):
            self.verify(tampered)


if __name__ == "__main__":
    unittest.main()
