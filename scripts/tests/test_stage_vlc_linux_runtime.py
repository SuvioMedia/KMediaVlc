# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/stage_vlc_linux_runtime.py"
SPEC = importlib.util.spec_from_file_location("stage_vlc_linux_runtime", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
STAGER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGER)


class LinuxRuntimeStagerTest(unittest.TestCase):
    def test_symbol_versions_use_numeric_family_maxima(self) -> None:
        parsed = STAGER.parse_symbol_versions(
            "GLIBC_2.9 GLIBC_2.39 GLIBCXX_3.4.9 GLIBCXX_3.4.33 CXXABI_1.3.15"
        )
        self.assertEqual(
            {"GLIBC": "2.39", "GLIBCXX": "3.4.33", "CXXABI": "1.3.15"},
            parsed,
        )
        self.assertTrue(STAGER.version_at_most("2.39", "2.39"))
        self.assertTrue(STAGER.version_at_most("3.4.9", "3.4.33"))
        self.assertFalse(STAGER.version_at_most("2.40", "2.39"))

    def test_dynamic_parser_rejects_legacy_rpath(self) -> None:
        valid = """
         0x000000000000000e (SONAME)             Library soname: [libvlc.so.12]
         0x0000000000000001 (NEEDED)             Shared library: [libvlccore.so.9]
         0x000000000000001d (RUNPATH)            Library runpath: [$ORIGIN]
        """
        self.assertEqual(
            ("libvlc.so.12", ["libvlccore.so.9"], "$ORIGIN"),
            STAGER.parse_dynamic(valid),
        )
        with self.assertRaisesRegex(SystemExit, "SONAME/RUNPATH"):
            STAGER.parse_dynamic(valid + "\n (RPATH) Library rpath: [/tmp]\n")

    def test_symbol_version_parser_allows_an_elf_without_direct_glibc_symbols(self) -> None:
        self.assertEqual(
            {"GLIBC": None, "GLIBCXX": None, "CXXABI": None},
            STAGER.parse_symbol_versions("No version information found in this file."),
        )

    def test_stages_exact_policy_with_mocked_elf_tools_and_stays_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as value:
            temporary = Path(value)
            install = temporary / "install"
            bridge = temporary / "libkmediavlc_bridge.so"
            output = temporary / "stage"
            report = temporary / "report.json"
            tools = temporary / "tools"
            tools.mkdir()
            self.write_file(install / "lib/libvlc.so.12")
            self.write_file(install / "lib/libvlccore.so.9")
            self.write_file(bridge)

            policy = json.loads(
                (ROOT / "compliance/policy/linux-playback-modules.json").read_text(
                    encoding="utf-8"
                )
            )
            for family, names in policy["modulesByFamily"].items():
                for name in names:
                    self.write_file(
                        install / f"lib/vlc/plugins/{family}/lib{name}_plugin.so"
                    )

            cache_generator = install / "libexec/vlc/vlc-cache-gen"
            self.write_executable(
                cache_generator,
                "#!/bin/sh\nprintf cache > \"$1/plugins.dat\"\n",
            )
            for name in ("patchelf", "strip"):
                self.write_executable(tools / name, "#!/bin/sh\nexit 0\n")
            self.write_executable(tools / "readelf", self.fake_readelf())

            arguments = [
                "stage_vlc_linux_runtime.py",
                "--root",
                str(ROOT),
                "--install",
                str(install),
                "--bridge",
                str(bridge),
                "--target",
                "linux-x86_64",
                "--output",
                str(output),
                "--report",
                str(report),
                "--patchelf",
                str(tools / "patchelf"),
                "--readelf",
                str(tools / "readelf"),
                "--strip",
                str(tools / "strip"),
            ]
            with mock.patch.object(sys, "argv", arguments):
                with self.assertRaisesRegex(SystemExit, "have not completed review"):
                    STAGER.main()

            with mock.patch.object(sys, "argv", arguments + ["--allow-audit-candidate"]):
                STAGER.main()
            evidence = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(85, evidence["selectedPluginCount"])
            self.assertEqual(85, evidence["rawPluginCount"])
            self.assertTrue(evidence["auditCandidate"])
            self.assertEqual(89, len(evidence["files"]))
            self.assertEqual(88, len(evidence["elf"]))
            self.assertTrue((output / "lib/vlc/plugins/plugins.dat").is_file())

    @staticmethod
    def write_file(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic-elf")

    @staticmethod
    def write_executable(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    @staticmethod
    def fake_readelf() -> str:
        return f"""#!{sys.executable}
import sys
from pathlib import Path

arguments = sys.argv[1:]
path = Path(arguments[-1])
name = path.name
if name == "libkmediavlc_bridge.so":
    role = "bridge"
elif name == "libvlc.so.12":
    role = "libvlc"
elif name == "libvlccore.so.9":
    role = "core"
else:
    role = "plugin"

if "-h" in arguments:
    print("  Class:                             ELF64")
    print("  Machine:                           Advanced Micro Devices X86-64")
elif "-dW" in arguments:
    print(f" (SONAME) Library soname: [{{name}}]")
    if role in {{"libvlc", "plugin"}}:
        print(" (NEEDED) Shared library: [libvlccore.so.9]")
    print(" (NEEDED) Shared library: [libc.so.6]")
    runpath = "$ORIGIN/../../../bin" if role == "plugin" else "$ORIGIN"
    print(f" (RUNPATH) Library runpath: [{{runpath}}]")
elif "-lW" in arguments:
    print(" GNU_STACK 0x000000 0x000000 0x000000 0x000000 0x000000 RW 0x10")
    print(" GNU_RELRO 0x000000 0x000000 0x000000 0x000000 0x000000 R 0x1")
elif "-nW" in arguments:
    print(" Build ID: 0123456789abcdef0123456789abcdef01234567")
elif "--version-info" in arguments:
    print(" Name: GLIBC_2.39")
else:
    raise SystemExit(2)
"""


if __name__ == "__main__":
    unittest.main()
