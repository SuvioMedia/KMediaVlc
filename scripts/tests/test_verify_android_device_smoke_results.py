# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "verify_android_device_smoke_results.py"
SPEC = importlib.util.spec_from_file_location("android_device_results", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
RESULTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RESULTS)


class AndroidDeviceSmokeResultsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.payload = self.root / "payload"
        for relative in RESULTS.REQUIRED_LIBRARIES:
            path = self.payload / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((relative + "\n").encode("ascii"))
        legal = self.payload / "legal/android-static-legal.json"
        legal.parent.mkdir(parents=True)
        legal.write_text("{}\n", encoding="ascii")
        (self.payload / "android-runtime.properties").write_text(
            "\n".join(
                [
                    "schemaVersion=1",
                    f"vlcRevision={RESULTS.PINNED_VLC_REVISION}",
                    f"libvlcjniRevision={RESULTS.PINNED_LIBVLCJNI_REVISION}",
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
        self.results = self.root / "results.xml"
        self.write_results()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_results(
        self,
        *,
        skipped: int = 0,
        extra_case: bool = False,
        device: str = "Pixel 9 Pro - 16",
    ) -> None:
        cases = list(RESULTS.TEST_CASES)
        if extra_case:
            cases.append("unexpectedCase")
        test_count = len(cases)
        testcases = "\n".join(
            f'<testcase name="{name}" classname="{RESULTS.TEST_CLASS}" />'
            for name in cases
        )
        self.results.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<testsuites tests="{test_count}" failures="0" errors="0" skipped="{skipped}">
  <testsuite name="{RESULTS.TEST_CLASS}" tests="{test_count}"
      failures="0" errors="0" skipped="{skipped}">
    <properties><property name="device" value="{device}" /></properties>
    {testcases}
  </testsuite>
</testsuites>
""",
            encoding="utf-8",
        )

    def verify(self) -> dict[str, object]:
        return RESULTS.verify_acceptance(
            payload=self.payload,
            results=self.results,
            tested_commit="a" * 40,
            device_abi="arm64-v8a",
            api_level=35,
            manufacturer="Google",
            model="Pixel 9 Pro",
            build_fingerprint="google/tokay/tokay:15/AP4A.250205.002/123456:user/release-keys",
        )

    def test_writes_closed_physical_device_evidence(self) -> None:
        report = self.verify()

        self.assertEqual("a" * 40, report["kmediaVlcCommit"])
        self.assertTrue(report["physicalDevice"]["qemuRejected"])
        self.assertEqual(sorted(RESULTS.TEST_CASES), report["testCases"])
        self.assertEqual(4, len(report["payload"]["runtimeLibraries"]))
        self.assertRegex(report["payload"]["treeSha256"], r"^[0-9a-f]{64}$")

    def test_rejects_skipped_test(self) -> None:
        self.write_results(skipped=1)
        with self.assertRaisesRegex(ValueError, "three-test pass"):
            self.verify()

    def test_rejects_extra_test(self) -> None:
        self.write_results(extra_case=True)
        with self.assertRaisesRegex(ValueError, "three-test pass"):
            self.verify()

    def test_rejects_emulator_result(self) -> None:
        self.write_results(device="KMediaVlcApi35Arm64Test(AVD) - 15")
        with self.assertRaisesRegex(ValueError, "emulator"):
            self.verify()

    def test_rejects_symbolic_payload_member(self) -> None:
        target = self.payload / RESULTS.REQUIRED_LIBRARIES[0]
        target.unlink()
        target.symlink_to(self.payload / RESULTS.REQUIRED_LIBRARIES[1])
        with self.assertRaisesRegex(ValueError, "symbolic"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
