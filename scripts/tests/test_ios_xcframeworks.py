#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from assemble_ios_xcframeworks import (  # noqa: E402
    EXPECTED_FRAMEWORK_COUNT,
    expected_frameworks,
    podspec,
)
from verify_ios_xcframework_archive import (  # noqa: E402
    IOS_16_2_0,
    IOS_PLATFORM,
    IOS_SIMULATOR_PLATFORM,
    LC_BUILD_VERSION,
    LC_ID_DYLIB,
    LC_LOAD_DYLIB,
    MACHO_ARM64,
    MACHO_MAGIC_64,
    MH_DYLIB,
    IosArchiveError,
    safe_members,
    verify_macho,
    verify_podspec,
)


def dylib_command(command: int, name: str) -> bytes:
    encoded = name.encode("utf-8") + b"\0"
    size = (24 + len(encoded) + 7) & ~7
    return (
        struct.pack("<IIIIII", command, size, 24, 0, 0, 0)
        + encoded
        + b"\0" * (size - 24 - len(encoded))
    )


def macho_bytes(
    install_name: str,
    dependencies: list[str],
    platform: int,
    minimum: int = IOS_16_2_0,
) -> bytes:
    commands = [
        struct.pack(
            "<IIIIII",
            LC_BUILD_VERSION,
            24,
            platform,
            minimum,
            minimum,
            0,
        ),
        dylib_command(LC_ID_DYLIB, install_name),
        *(dylib_command(LC_LOAD_DYLIB, dependency) for dependency in dependencies),
    ]
    command_blob = b"".join(commands)
    header = struct.pack(
        "<IIIIIIII",
        MACHO_MAGIC_64,
        MACHO_ARM64,
        0,
        MH_DYLIB,
        len(commands),
        len(command_blob),
        0,
        0,
    )
    return header + command_blob


class IosXcframeworkTest(unittest.TestCase):
    def test_policy_closes_all_87_logical_frameworks(self) -> None:
        frameworks = expected_frameworks()
        self.assertEqual(EXPECTED_FRAMEWORK_COUNT, len(frameworks))
        self.assertEqual(
            {"KMediaVlc", "KMediaVlcCore", "KMediaVlcLibVlc"},
            {
                record["frameworkName"]
                for record in frameworks
                if record["role"] != "PLUGIN"
            },
        )
        self.assertEqual(84, sum(record["role"] == "PLUGIN" for record in frameworks))
        self.assertIn(
            "libaudiounit_ios_plugin",
            {record["frameworkName"] for record in frameworks},
        )
        self.assertIn(
            "libvmem_plugin",
            {record["frameworkName"] for record in frameworks},
        )

    def test_generated_podspec_embeds_and_hash_binds_the_complete_graph(self) -> None:
        value = podspec(
            "1.2.3",
            "16.2",
            "kmedia-vlc-1.2.3-ios-xcframeworks.zip",
            "a" * 64,
        )
        self.assertIn("spec.name                  = 'KMediaVlc'", value)
        self.assertIn(
            "spec.license               = "
            "{ :type => 'LGPL-2.1-or-later', :file => 'LICENSE' }",
            value,
        )
        self.assertIn("spec.ios.deployment_target = '16.2'", value)
        self.assertIn(
            "spec.vendored_frameworks   = 'Frameworks/*.xcframework'",
            value,
        )
        self.assertIn("SuvioMedia/KMediaVlc/releases/download/v1.2.3", value)
        self.assertIn(":sha256 => '" + "a" * 64 + "'", value)

    def test_podspec_verifier_rejects_archive_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "kmedia-vlc-1.2.3-ios-xcframeworks.zip"
            archive.write_bytes(b"archive")
            generated = root / "KMediaVlc.podspec"
            generated.write_text(
                podspec(
                    "1.2.3",
                    "16.2",
                    archive.name,
                    hashlib.sha256(archive.read_bytes()).hexdigest(),
                ),
                encoding="utf-8",
            )
            verify_podspec(generated, archive, "1.2.3")
            archive.write_bytes(b"changed")
            with self.assertRaises(IosArchiveError):
                verify_podspec(generated, archive, "1.2.3")

    def test_archive_member_gate_rejects_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "unsafe.zip"
            with zipfile.ZipFile(archive_path, "x") as output:
                output.writestr("../escape", b"forbidden")
            with zipfile.ZipFile(archive_path, "r") as archive:
                with self.assertRaises(IosArchiveError):
                    safe_members(archive)

    def test_pure_macho_gate_accepts_device_and_simulator_slices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            core = "@rpath/KMediaVlcCore.framework/KMediaVlcCore"
            device = root / "KMediaVlcCore"
            device.write_bytes(
                macho_bytes(core, ["/usr/lib/libSystem.B.dylib"], IOS_PLATFORM)
            )
            self.assertEqual(
                "IOS",
                verify_macho(device, "CORE", "KMediaVlcCore", False)["platform"],
            )
            plugin_name = "libvmem_plugin"
            plugin_id = f"@rpath/{plugin_name}.framework/{plugin_name}"
            simulator = root / plugin_name
            simulator.write_bytes(
                macho_bytes(
                    plugin_id,
                    [core, "/usr/lib/libSystem.B.dylib"],
                    IOS_SIMULATOR_PLATFORM,
                )
            )
            self.assertEqual(
                "IOSSIMULATOR",
                verify_macho(simulator, "PLUGIN", plugin_name, True)["platform"],
            )

    def test_pure_macho_gate_rejects_a_newer_minimum(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "KMediaVlc"
            install_name = "@rpath/KMediaVlc.framework/KMediaVlc"
            binary.write_bytes(
                macho_bytes(
                    install_name,
                    ["/usr/lib/libSystem.B.dylib"],
                    IOS_PLATFORM,
                    (17 << 16),
                )
            )
            with self.assertRaises(IosArchiveError):
                verify_macho(binary, "BRIDGE", "KMediaVlc", False)


if __name__ == "__main__":
    unittest.main()
