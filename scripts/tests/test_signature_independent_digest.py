# SPDX-License-Identifier: LGPL-2.1-or-later

import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("package_native_runtime", ROOT / "scripts/package_native_runtime.py")
BUILD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BUILD)

# The same library digested by NativeLibraryDigest.java (NativeLibraryDigestTest).
EXPECTED_DIGEST = "0f884355e9c78c6a4762fc0e6f06816a1ecb8c7fbaa6f3bb1897272ff94f3b5e"
TEXT_SIZE = 0x4000
LINKEDIT_CONTENT = 0x100


def macho(
    signature_size=0x90,
    signature_last=False,
    signed=True,
    code=0x11,
    rpath="@loader_path",
    trailing=0,
    magic=0xFADE0CC0,
):
    """Builds a minimal arm64 dylib layout: __TEXT, __LINKEDIT, code signature and an rpath."""
    rpath_bytes = rpath.encode("ascii")
    rpath_size = (12 + len(rpath_bytes) + 1 + 7) & ~7
    signature_offset = TEXT_SIZE + LINKEDIT_CONTENT
    linkedit_size = signature_offset + (signature_size if signed else 0) - TEXT_SIZE
    commands = [segment(b"__TEXT", 0, TEXT_SIZE, 0, TEXT_SIZE, 5)]
    signature = struct.pack("<IIII", 0x1D, 16, signature_offset, signature_size)
    if signed and not signature_last:
        commands.append(signature)
    commands.append(
        segment(b"__LINKEDIT", TEXT_SIZE, (linkedit_size + 0x3FFF) & ~0x3FFF, TEXT_SIZE, linkedit_size, 1)
    )
    commands.append(struct.pack("<III", 0x8000001C, rpath_size, 12) + rpath_bytes.ljust(rpath_size - 12, b"\0"))
    if signed and signature_last:
        commands.append(signature)
    load_commands = b"".join(commands)
    header = struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 6, len(commands), len(load_commands), 0x00100085, 0)
    text = header + load_commands
    text += bytes([code]) * (TEXT_SIZE - len(text))
    linkedit = bytes(index & 0xFF for index in range(TEXT_SIZE, signature_offset))
    blob = b""
    if signed:
        blob = struct.pack(">III", magic, signature_size, 0) + bytes([signature_size & 0xFF]) * (signature_size - 12)
    return text + linkedit + blob + b"\0" * trailing


def segment(name, vm_address, vm_size, file_offset, file_size, protection):
    return struct.pack(
        "<II16sQQQQIIII", 0x19, 72, name, vm_address, vm_size, file_offset, file_size,
        protection, protection, 0, 0,
    )


class SignatureIndependentDigestTest(unittest.TestCase):
    def digest(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.dylib"
            path.write_bytes(data)
            return BUILD.signature_independent_sha256(path)

    def test_re_signing_keeps_the_digest(self):
        self.assertEqual(EXPECTED_DIGEST, self.digest(macho()))
        self.assertEqual(EXPECTED_DIGEST, self.digest(macho(signature_size=0x2A0, signature_last=True)))

    def test_code_and_load_command_changes_change_the_digest(self):
        self.assertNotEqual(EXPECTED_DIGEST, self.digest(macho(signature_size=0x2A0, signature_last=True, code=0x7F)))
        self.assertNotEqual(EXPECTED_DIGEST, self.digest(macho(rpath="@executable_path")))

    def test_other_files_have_no_digest(self):
        self.assertIsNone(self.digest(b"\x7fELF not a Mach-O file"))

    def test_rejects_mach_o_files_without_one_trailing_signature(self):
        for data in (macho(signed=False), macho(trailing=16), macho(magic=0x12345678)):
            with self.assertRaises(SystemExit):
                self.digest(data)


    def test_macos_manifests_record_the_digest_outside_the_runtime_id(self):
        inventory = {
            "target": "macos-aarch64", "frameDeliveryModes": ["GPU_PUSH"], "renderEngines": ["OPENGL"],
            "pluginDirectory": "lib/vlc/plugins",
        }
        files = [
            {"path": path, "size": 1, "sha256": "0" * 64, "component": "kmediavlc", "licenseSpdx": "LGPL-2.1-or-later",
             "role": role, "source": "https://example.invalid/source.tar.gz", "linkage": "DYNAMIC"}
            for path, role in (("bin/libkmediavlc_bridge.dylib", "BRIDGE"), ("bin/libvlc.12.dylib", "LIBVLC"))
        ]
        text = BUILD.manifest_text(
            inventory, files, "kmediavlc4-0123456789abcdef", "https://example.invalid/source.tar.gz", "a" * 40,
            {"bin/libvlc.12.dylib": EXPECTED_DIGEST},
        )
        self.assertIn(f"file.1.signatureIndependentSha256={EXPECTED_DIGEST}", text.splitlines())
        self.assertNotIn("file.0.signatureIndependentSha256", text)

if __name__ == "__main__":
    unittest.main()
