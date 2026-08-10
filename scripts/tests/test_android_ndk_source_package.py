# SPDX-License-Identifier: LicenseRef-KMediaVlc-Proprietary

from __future__ import annotations

import gzip
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
TESTED_COMMIT = "0123456789abcdef0123456789abcdef01234567"
EPOCH = 1_700_000_000


def load_module(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PACKAGER = load_module(
    "package_android_ndk_source", "scripts/package_android_ndk_source.py"
)
VERIFIER = load_module(
    "verify_android_ndk_source_archive",
    "scripts/verify_android_ndk_source_archive.py",
)


class AndroidNdkSourcePackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "repository"
        (self.root / "compliance/policy").mkdir(parents=True)
        self.llvm_android = self.base / "llvm-android"
        self.llvm_project = self.base / "llvm-project"
        android_revision, android_tree = self.create_git_repository(
            self.llvm_android,
            {
                "README.md": b"Android LLVM build scripts\n",
                "do_build.py": b"#!/usr/bin/env python3\nprint('build')\n",
                "patches/runtime.patch": b"runtime patch\n",
                "src/llvm_android/android_version.py": b"PATCH_LEVEL = 0\n",
                "src/llvm_android/builders.py": b"class BuiltinsBuilder: pass\n",
            },
            executable={"do_build.py"},
        )
        project_revision, project_tree = self.create_git_repository(
            self.llvm_project,
            {
                "compiler-rt/cmake/config.cmake": b"set(BUILTINS ON)\n",
                "compiler-rt/lib/builtins/add.c": b"int add(int a, int b) { return a + b; }\n",
                "libcxx/src/vector.cpp": b"// libc++ source\n",
                "outside/not-packaged.txt": b"not part of the runtime source closure\n",
            },
        )
        self.policy = {
            "schemaVersion": 1,
            "target": "android-arm",
            "ndkRevision": PACKAGER.NDK_REVISION,
            "ndkSourceInputs": {
                "llvm-android-build": {
                    "repository": "https://example.invalid/llvm_android",
                    "revision": android_revision,
                    "tree": android_tree,
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
                    "revision": project_revision,
                    "tree": project_tree,
                    "role": "linked-runtime-source",
                    "requiredPaths": ["compiler-rt/lib/builtins", "libcxx"],
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
                        "paths": ["compiler-rt", "libcxx"],
                    },
                },
            },
            "ndkReleaseProvenance": {"releaseName": "r29-fixture"},
            "ndkArchiveSourcePaths": {
                "ndk/libbuiltins.a": ["llvm-project/compiler-rt/lib/builtins"],
                "ndk/libc++_static.a": ["llvm-project/libcxx"],
            },
        }
        self.write_policy()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def run_git(directory: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout.strip()

    def create_git_repository(
        self, directory: Path, files: dict[str, bytes], executable: set[str] | None = None
    ) -> tuple[str, str]:
        directory.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(directory)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for relative, value in files.items():
            path = directory / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            if executable and relative in executable:
                path.chmod(0o755)
        self.run_git(directory, "add", ".")
        self.run_git(
            directory,
            "-c",
            "user.name=KMediaVlc Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-q",
            "-m",
            "Pinned source fixture",
        )
        return (
            self.run_git(directory, "rev-parse", "HEAD"),
            self.run_git(directory, "rev-parse", "HEAD^{tree}"),
        )

    def write_policy(self) -> None:
        path = self.root / "compliance/policy/android-static-components.json"
        path.write_text(json.dumps(self.policy, indent=2) + "\n", encoding="utf-8")

    def package(self, name: str = "android-ndk-source.tar.gz") -> Path:
        output = self.base / name
        PACKAGER.package(
            self.root,
            self.llvm_project,
            self.llvm_android,
            output,
            TESTED_COMMIT,
            VERSION,
            EPOCH,
        )
        return output

    def test_packages_and_verifies_exact_git_objects_deterministically(self) -> None:
        first = self.package("first.tar.gz")
        second = self.package("second.tar.gz")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        with tarfile.open(first, "r:gz") as archive:
            manifest = json.load(
                archive.extractfile(
                    "android-ndk-runtime-source/SOURCE-MANIFEST.json"
                )
            )
            names = {member.name.rstrip("/") for member in archive.getmembers()}
        self.assertEqual("corresponding-source-mapped", manifest["verifiedSourceStatus"])
        self.assertIn(
            "android-ndk-runtime-source/llvm-android-build/README.md", names
        )
        self.assertIn(
            "android-ndk-runtime-source/llvm-project/compiler-rt/cmake/config.cmake",
            names,
        )
        self.assertNotIn(
            "android-ndk-runtime-source/llvm-project/outside/not-packaged.txt", names
        )
        digest = VERIFIER.verify(
            self.root,
            first,
            self.llvm_project,
            self.llvm_android,
            VERSION,
            TESTED_COMMIT,
        )
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_packager_and_verifier_reject_tracked_source_changes(self) -> None:
        archive = self.package()
        (self.llvm_android / "do_build.py").write_text("changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "tracked modifications"):
            PACKAGER.package(
                self.root,
                self.llvm_project,
                self.llvm_android,
                self.base / "changed.tar.gz",
                TESTED_COMMIT,
                VERSION,
                EPOCH,
            )
        with self.assertRaisesRegex(ValueError, "tracked modifications"):
            VERIFIER.verify(
                self.root,
                archive,
                self.llvm_project,
                self.llvm_android,
                VERSION,
                TESTED_COMMIT,
            )

    def test_verifier_rejects_a_tampered_source_member(self) -> None:
        archive = self.package()
        tampered = self.base / "tampered.tar.gz"
        with tarfile.open(archive, "r:gz") as source:
            members = source.getmembers()
            with tampered.open("xb") as raw:
                with gzip.GzipFile(
                    filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=EPOCH
                ) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                    ) as target:
                        for member in members:
                            if member.isdir():
                                target.addfile(member)
                                continue
                            stream = source.extractfile(member)
                            assert stream is not None
                            data = stream.read()
                            if member.name.endswith("compiler-rt/lib/builtins/add.c"):
                                data = b"X" + data[1:]
                            target.addfile(member, io.BytesIO(data))
        with self.assertRaisesRegex(ValueError, "differs from its Git object"):
            VERIFIER.verify(
                self.root,
                tampered,
                self.llvm_project,
                self.llvm_android,
                VERSION,
                TESTED_COMMIT,
            )

    def test_verifier_rejects_a_different_release_commit(self) -> None:
        archive = self.package()
        with self.assertRaisesRegex(ValueError, "release identity"):
            VERIFIER.verify(
                self.root,
                archive,
                self.llvm_project,
                self.llvm_android,
                VERSION,
                "89abcdef0123456789abcdef0123456789abcdef",
            )

    def test_packager_rejects_a_nonexistent_selected_subtree(self) -> None:
        self.policy["ndkSourcePackage"]["sources"]["llvm-project"]["paths"].append(
            "missing-runtime-source"
        )
        self.write_policy()
        with self.assertRaisesRegex(ValueError, "source path is missing"):
            self.package()


if __name__ == "__main__":
    unittest.main()
