# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "verify_desktop_runtime_reuse.py"
SPEC = importlib.util.spec_from_file_location("desktop_reuse", SOURCE)
REUSE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REUSE)


class DesktopRuntimeReuseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.java = REUSE.JAVA + "NativeBridge.java"
        self.write(self.java, REUSE.OLD_HEADER + b"class NativeBridge {}\n")
        self.write("native/bridge.cpp", b"original\n")
        self.write("build-recipes/vlc-apple.conf", b"original\n")
        self.write("compliance/policy/release-policy.json", b"{}\n")
        self.base = self.commit()

    def git(self, *args):
        return REUSE.git(self.root, *args).decode().strip()

    def write(self, path, data):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def commit(self):
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def test_reviewed_loader_and_exact_license_header_are_allowed(self):
        self.write(self.java, REUSE.NEW_HEADER + b"class NativeBridge {}\n")
        self.write(REUSE.JAVA + "VlcDesktopRuntime.java", REUSE.NEW_HEADER + b"class VlcDesktopRuntime {}\n")
        REUSE.verify(self.root, self.base, self.commit())

    def test_license_header_cannot_hide_native_binding_change(self):
        self.write(self.java, REUSE.NEW_HEADER + b"class NativeBridge { native void changed(); }\n")
        with self.assertRaisesRegex(ValueError, "NativeBridge"):
            REUSE.verify(self.root, self.base, self.commit())

    def test_native_change_requires_new_audit(self):
        self.write("native/bridge.cpp", b"changed\n")
        with self.assertRaisesRegex(ValueError, "native/bridge.cpp"):
            REUSE.verify(self.root, self.base, self.commit())

    def test_recipe_change_requires_new_audit(self):
        self.write("build-recipes/vlc-apple.conf", b"changed\n")
        with self.assertRaisesRegex(ValueError, "build-recipes"):
            REUSE.verify(self.root, self.base, self.commit())

    def test_license_policy_change_requires_new_audit(self):
        self.write("compliance/policy/release-policy.json", b'{"changed":true}\n')
        with self.assertRaisesRegex(ValueError, "compliance/policy"):
            REUSE.verify(self.root, self.base, self.commit())

    def test_java_deletion_is_rejected(self):
        (self.root / self.java).unlink()
        with self.assertRaisesRegex(ValueError, "deletion"):
            REUSE.verify(self.root, self.base, self.commit())

    def test_nonimmutable_reference_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "immutable"):
            REUSE.verify(self.root, "main", self.base)


if __name__ == "__main__":
    unittest.main()
