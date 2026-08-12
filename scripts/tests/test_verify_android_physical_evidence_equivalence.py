# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "verify_android_physical_evidence_equivalence",
    SCRIPTS / "verify_android_physical_evidence_equivalence.py",
)
assert SPEC is not None and SPEC.loader is not None
EQUIVALENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EQUIVALENCE)


class AndroidPhysicalEvidenceEquivalenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.git("init")
        self.git("config", "user.email", "android-evidence@example.invalid")
        self.git("config", "user.name", "Android Evidence Test")
        (self.root / "behavior.txt").write_text("physically tested\n", encoding="utf-8")
        self.git("add", "behavior.txt")
        self.git("commit", "-m", "physical execution state")
        self.execution_commit = self.git("rev-parse", "HEAD").stdout.strip()

        self.payload = Path(self.temporary.name) / "payload"
        library_hashes = {}
        for relative in EQUIVALENCE.REQUIRED_LIBRARIES:
            path = self.payload / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((relative + "\n").encode("ascii"))
            library_hashes[relative] = self.digest(path.read_bytes())
        legal = self.payload / "legal/android-static-legal.json"
        legal.parent.mkdir(parents=True)
        legal.write_text(
            json.dumps(
                {
                    "reviewStatus": "candidate-linked-member-review-pending",
                    "effectiveLicenseSpdx": None,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.payload / "android-runtime.properties").write_text(
            "\n".join(
                [
                    "schemaVersion=1",
                    f"vlcRevision={EQUIVALENCE.PINNED_VLC_REVISION}",
                    f"libvlcjniRevision={EQUIVALENCE.PINNED_LIBVLCJNI_REVISION}",
                    "bridgeAbi=1",
                    "renderEngine=ANATIVEWINDOW",
                    "minSdk=28",
                    "abis=arm64-v8a,armeabi-v7a",
                    "libraries=libkmediavlc_android.so,libvlc.so",
                    "staticCpp=true",
                    "releaseEligible=false",
                    "",
                ]
            ),
            encoding="ascii",
        )

        junit = self.junit_bytes()
        evidence = self.root / "compliance/evidence/android"
        evidence.mkdir(parents=True)
        junit_base64 = evidence / "test-results.xml.b64"
        junit_base64.write_bytes(base64.b64encode(junit) + b"\n")
        acceptance = {
            "schemaVersion": 1,
            "kmediaVlcCommit": self.execution_commit,
            "vlcRevision": EQUIVALENCE.PINNED_VLC_REVISION,
            "libvlcjniRevision": EQUIVALENCE.PINNED_LIBVLCJNI_REVISION,
            "physicalDevice": {
                "apiLevel": 36,
                "buildFingerprint": "vendor/device/build:16/release/user/release-keys",
                "gradleDescription": "Physical Phone - 16",
                "manufacturer": "Vendor",
                "model": "Physical Phone",
                "primaryAbi": "arm64-v8a",
                "qemuRejected": True,
            },
            "payload": {
                "fileCount": 6,
                "treeSha256": "0" * 64,
                "runtimeLibraries": library_hashes,
            },
            "testClass": EQUIVALENCE.TEST_CLASS,
            "testCases": list(EQUIVALENCE.TEST_CASES),
            "testResultsSha256": self.digest(junit),
        }
        acceptance_path = evidence / "acceptance.json"
        self.write_json(acceptance_path, acceptance)
        policy = {
            "schemaVersion": 1,
            "executionCommit": self.execution_commit,
            "evidence": {
                "acceptancePath": "compliance/evidence/android/acceptance.json",
                "acceptanceSha256": self.file_digest(acceptance_path),
                "junitBase64Path": "compliance/evidence/android/test-results.xml.b64",
                "junitBase64Sha256": self.file_digest(junit_base64),
                "junitDecodedSha256": self.digest(junit),
            },
            "runtimeLibraries": library_hashes,
            "behaviorPaths": ["behavior.txt"],
        }
        policy_path = self.root / EQUIVALENCE.POLICY_PATH
        policy_path.parent.mkdir(parents=True)
        self.write_json(policy_path, policy)
        self.git("add", "compliance")
        self.git("commit", "-m", "retain physical evidence")
        self.release_commit = self.git("rev-parse", "HEAD").stdout.strip()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    @staticmethod
    def digest(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    @classmethod
    def file_digest(cls, path: Path) -> str:
        return cls.digest(path.read_bytes())

    @staticmethod
    def write_json(path: Path, value: dict) -> None:
        path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    @staticmethod
    def junit_bytes() -> bytes:
        testcases = "\n".join(
            f'<testcase name="{name}" classname="{EQUIVALENCE.TEST_CLASS}" />'
            for name in EQUIVALENCE.TEST_CASES
        )
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="3" failures="0" errors="0" skipped="0">
  <testsuite name="{EQUIVALENCE.TEST_CLASS}" tests="3" failures="0" errors="0" skipped="0">
    <properties><property name="device" value="Physical Phone - 16" /></properties>
    {testcases}
  </testsuite>
</testsuites>
""".encode("utf-8")

    def verify(self, name: str, commit: str | None = None) -> dict[str, object]:
        output = Path(self.temporary.name) / name
        return EQUIVALENCE.verify(
            root=self.root,
            payload=self.payload,
            release_commit=commit or self.release_commit,
            output=output,
        )

    def test_binds_exact_runtime_hashes_to_fresh_physical_result(self) -> None:
        report = self.verify("valid.json")
        self.assertEqual(self.execution_commit, report["executionCommit"])
        self.assertEqual(self.release_commit, report["releaseCommit"])
        self.assertTrue(report["behaviorPathsUnchanged"])
        self.assertFalse(report["releaseEligible"])

    def test_rejects_runtime_library_changed_after_physical_test(self) -> None:
        changed = self.payload / EQUIVALENCE.REQUIRED_LIBRARIES[0]
        changed.write_bytes(b"different binary\n")
        with self.assertRaisesRegex(ValueError, "physically tested binaries"):
            self.verify("changed-library.json")

    def test_rejects_behavior_change_after_physical_test(self) -> None:
        (self.root / "behavior.txt").write_text("changed\n", encoding="utf-8")
        self.git("add", "behavior.txt")
        self.git("commit", "-m", "change playback behavior")
        changed_commit = self.git("rev-parse", "HEAD").stdout.strip()
        with self.assertRaisesRegex(ValueError, "playback behavior changed"):
            self.verify("changed-behavior.json", changed_commit)


if __name__ == "__main__":
    unittest.main()
