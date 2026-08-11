# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import importlib.util
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "create_android_link_audit", ROOT / "scripts/create_android_link_audit.py"
)
AUDIT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(AUDIT)


@unittest.skipUnless(os.name == "posix", "Android source-build audit requires a POSIX host")
class AndroidLinkAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.vlc = self.base / "vlc"
        self.ndk = self.base / "ndk"
        self.tuple_name = "aarch64-linux-android"
        self.build = self.vlc / f"build-android-{self.tuple_name}"
        self.plugins = self.build / "install/lib/vlc/plugins"
        self.contrib = self.vlc / f"contrib/{self.tuple_name}/lib"
        self.ndk_prebuilt = self.ndk / "toolchains/llvm/prebuilt/linux-x86_64"
        for directory in (
            self.plugins,
            self.contrib,
            self.ndk_prebuilt,
            self.build / "ndk",
            self.build / "lib/.libs",
            self.build / "src/.libs",
            self.build / "compat/.libs",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.policy = AUDIT.static_component_policy(ROOT)
        for component in self.policy["contribComponents"].values():
            for source in component["sourceArchives"]:
                self.write_source_archive(
                    self.vlc / "contrib/tarballs" / source,
                    self.policy["licenseEvidence"][source],
                )
        for evidence in ("NOTICE", "NOTICE.toolchain", "source.properties"):
            self.write(self.ndk / evidence, evidence)
        self.write(
            self.ndk_prebuilt / "AndroidVersion.txt",
            "21.0.0\nbased on r563880c\n"
            "for additional information on LLVM revision and cherry-picks, "
            "see clang_source_info.md\n",
        )
        self.write(
            self.ndk_prebuilt / "clang_source_info.md",
            "Base revision: "
            f"[{AUDIT.LLVM_PROJECT_REVISION}]"
            "(https://github.com/llvm/llvm-project/commits/"
            f"{AUDIT.LLVM_PROJECT_REVISION})\n\n"
            "- [patch](https://android.googlesource.com/toolchain/llvm_android/+/"
            f"{AUDIT.LLVM_ANDROID_REVISION}/patches/runtime.patch)\n",
        )
        self.modules = sorted(AUDIT.required_modules(ROOT))
        manifest = self.build / "ndk/libvlcjni-modules.c"
        with manifest.open("w", encoding="utf-8", newline="\n") as target:
            target.write("const void *vlc_static_modules[] = {\n")
            for module in self.modules:
                target.write(f" vlc_entry__{module},\n")
            target.write(" NULL\n};\n")
        self.archives = []
        for module in self.modules:
            self.archives.append(self.write(self.plugins / f"lib{module}_plugin.a", module))
        self.archives.extend(
            [
                self.write(self.build / "lib/.libs/libvlc.a", "libvlc"),
                self.write(self.build / "src/.libs/libvlccore.a", "libvlccore"),
                self.write(self.build / "compat/.libs/libcompat.a", "libcompat"),
            ]
        )
        for canonical in self.policy["contribArchives"]:
            relative = canonical.removeprefix("vlc-contrib/")
            self.archives.append(
                self.write(self.vlc / f"contrib/{self.tuple_name}" / relative, canonical)
            )
        for canonical in AUDIT.expanded_ndk_archive_components(
            self.ndk, "arm64-v8a", self.policy
        ):
            relative = canonical.removeprefix("ndk/")
            self.archives.append(self.write(self.ndk / relative, canonical))
        self.libvlc = self.write(self.base / "libvlc.so", "elf")
        self.link_map = self.base / "libvlc.map"
        self.write_map(self.archives)
        self.readelf = self.tool(
            "readelf",
            """#!/bin/sh
if [ "$1" = "-d" ]; then
  echo ' 0x0000000000000001 (NEEDED) Shared library: [libc.so]'
  echo ' 0x0000000000000001 (NEEDED) Shared library: [liblog.so]'
else
  echo '  LOAD 0x000000 0x000000 0x000000 0x100 0x100 R E 0x4000'
fi
""",
        )
        self.nm = self.tool(
            "nm",
            "#!/bin/sh\nprintf '000 T %s\\n' JNI_OnLoad libvlc_get_changeset libvlc_get_version libvlc_new libvlc_video_set_output_callbacks\n",
        )
        self.strings = self.tool(
            "strings",
            f"#!/bin/sh\necho '{AUDIT.LGPL_TEXT}'\n",
        )
        self.output = self.base / "audit.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def write(path: Path, value: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode("ascii"))
        return path

    @staticmethod
    def write_source_archive(path: Path, evidence_paths: list[str]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.name.endswith(".tar.gz"):
            mode = "w:gz"
        elif path.name.endswith(".tar.bz2"):
            mode = "w:bz2"
        else:
            mode = "w:xz"
        with tarfile.open(path, mode=mode) as archive:
            for evidence_path in evidence_paths:
                data = f"license evidence for {evidence_path}\n".encode("ascii")
                member = tarfile.TarInfo(f"source/{evidence_path}")
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return path

    def write_map(self, archives: list[Path]) -> None:
        with self.link_map.open("w", encoding="utf-8", newline="\n") as target:
            target.write(" VMA LMA Size Align Out In Symbol\n")
            for index, archive in enumerate(archives):
                target.write(f" {index:x} {index:x} 1 1 {archive}(member-{index}.o):(.text)\n")

    def tool(self, name: str, body: str) -> Path:
        path = self.base / name
        with path.open("w", encoding="utf-8", newline="\n") as target:
            target.write(body)
        path.chmod(0o755)
        return path

    def create(self) -> dict:
        return AUDIT.create(
            ROOT,
            self.vlc,
            self.ndk,
            "arm64-v8a",
            self.libvlc,
            self.link_map,
            self.readelf,
            self.nm,
            self.strings,
            self.output,
        )

    def test_creates_path_free_candidate_from_exact_link_inputs(self) -> None:
        result = self.create()
        self.assertEqual(
            "candidate-source-mapped-license-review-pending", result["reviewStatus"]
        )
        self.assertEqual(self.modules, [entry["name"] for entry in result["modules"]])
        self.assertEqual(
            {"CONTRIB", "NDK_TOOLCHAIN", "VLC_CORE", "VLC_MODULE"},
            {entry["kind"] for entry in result["staticArchives"]},
        )
        self.assertNotIn(str(self.base), self.output.read_text(encoding="utf-8"))
        self.assertEqual(0x4000, result["libvlc"]["loadAlignment"])
        self.assertEqual("LGPL-2.1-or-later", result["libvlc"]["declaredVlcLicenseSpdx"])
        self.assertIsNone(result["libvlc"]["effectiveLicenseSpdx"])
        self.assertEqual(
            "patches/libvlcjni/0001-kmediavlc-android-static-module-policy.patch",
            result["evidence"]["libvlcjniPatch"]["path"],
        )
        self.assertRegex(result["evidence"]["libvlcjniPatch"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            "compliance/policy/android-static-components.json",
            result["evidence"]["staticComponentPolicy"]["path"],
        )
        self.assertEqual(
            set(self.policy["contribComponents"]) | set(self.policy["ndkComponents"]),
            {entry["id"] for entry in result["staticComponents"]},
        )
        self.assertTrue(
            all(
                source["licenseEvidence"]
                for entry in result["staticComponents"]
                if entry["kind"] == "VLC_CONTRIB"
                for source in entry["sourceArchives"]
            )
        )
        self.assertTrue(
            all(
                entry["candidateLicenseSpdx"]
                and entry["licenseReviewStatus"] == "pending-linked-member-review"
                for entry in result["staticComponents"]
            )
        )
        self.assertTrue(
            all(entry.get("component") for entry in result["staticArchives"] if entry["kind"] in {"CONTRIB", "NDK_TOOLCHAIN"})
        )
        ndk_component = next(
            entry for entry in result["staticComponents"] if entry["kind"] == "NDK_TOOLCHAIN"
        )
        self.assertEqual(AUDIT.NDK_SOURCE_STATUS, ndk_component["sourceStatus"])
        self.assertEqual(5, len(ndk_component["evidenceFiles"]))
        self.assertEqual(
            sorted(AUDIT.EXPECTED_NDK_SOURCE_INPUTS),
            [entry["id"] for entry in ndk_component["sourceInputs"]],
        )
        self.assertEqual("linux-x86_64", ndk_component["binaryProvenance"]["prebuilt"]["hostTag"])
        self.assertTrue(
            all(
                entry["sourcePaths"]
                for entry in result["staticArchives"]
                if entry["kind"] == "NDK_TOOLCHAIN"
            )
        )

    def test_rejects_changed_clang_source_revision(self) -> None:
        self.write(
            self.ndk_prebuilt / "clang_source_info.md",
            "Base revision: [0000000000000000000000000000000000000000]"
            "(https://github.com/llvm/llvm-project/commits/"
            "0000000000000000000000000000000000000000)\n",
        )
        with self.assertRaisesRegex(ValueError, "different LLVM base revision"):
            self.create()

    def test_rejects_archive_outside_closed_build_roots(self) -> None:
        foreign = self.write(self.base / "foreign/libforeign.a", "foreign")
        self.archives.append(foreign)
        self.write_map(self.archives)
        with self.assertRaisesRegex(ValueError, "outside the closed roots"):
            self.create()

    def test_rejects_missing_source_mapped_contrib_archive(self) -> None:
        removed = next(
            archive
            for archive in self.archives
            if archive.name == "libFLAC.a"
        )
        self.archives.remove(removed)
        self.write_map(self.archives)
        with self.assertRaisesRegex(ValueError, "contrib link graph differs"):
            self.create()

    def test_rejects_missing_contrib_source_archive(self) -> None:
        source = self.vlc / "contrib/tarballs/flac-1.5.0.tar.xz"
        source.unlink()
        with self.assertRaisesRegex(ValueError, "VLC contrib source archive"):
            self.create()

    def test_rejects_missing_license_evidence_member(self) -> None:
        source = self.vlc / "contrib/tarballs/flac-1.5.0.tar.xz"
        self.write_source_archive(source, ["WRONG-LICENSE"])
        with self.assertRaisesRegex(ValueError, "license evidence is missing"):
            self.create()

    def test_rejects_gpl_module_marker(self) -> None:
        self.strings = self.tool(
            "strings-gpl",
            f"#!/bin/sh\necho '{AUDIT.LGPL_TEXT}'\necho '{AUDIT.GPL_TEXT}'\n",
        )
        with self.assertRaisesRegex(ValueError, "closed LGPL module marker"):
            self.create()

    def test_accepts_garbage_collected_final_lgpl_marker(self) -> None:
        self.strings = self.tool(
            "strings-final-gc",
            f"""#!/bin/sh
case "$1" in
  *.so) echo 'no retained module marker' ;;
  *) echo '{AUDIT.LGPL_TEXT}' ;;
esac
""",
        )
        result = self.create()
        self.assertFalse(result["libvlc"]["lgplModuleMarkerRetained"])

    def test_rejects_gpl_marker_in_final_library(self) -> None:
        self.strings = self.tool(
            "strings-final-gpl",
            f"""#!/bin/sh
case "$1" in
  *.so) echo '{AUDIT.GPL_TEXT}' ;;
  *) echo '{AUDIT.LGPL_TEXT}' ;;
esac
""",
        )
        with self.assertRaisesRegex(ValueError, "retains a forbidden GPL module marker"):
            self.create()

    def test_rejects_forbidden_dynamic_dependency(self) -> None:
        self.readelf = self.tool(
            "readelf-forbidden",
            """#!/bin/sh
if [ "$1" = "-d" ]; then
  echo ' 0x1 (NEEDED) Shared library: [libc++_shared.so]'
else
  echo '  LOAD 0 0 0 0 0 R E 0x4000'
fi
""",
        )
        with self.assertRaisesRegex(ValueError, "forbidden DT_NEEDED"):
            self.create()

    def test_preserves_ndk_frontend_symlink_name(self) -> None:
        multiplexer = self.tool(
            "llvm-readobj",
            """#!/bin/sh
case "${0##*/}" in
  llvm-readelf)
    if [ "$1" = "-d" ]; then
      echo ' 0x1 (NEEDED) Shared library: [libc.so]'
    else
      echo '  LOAD 0 0 0 0 0 R E 0x4000'
    fi
    ;;
  *) exit 2 ;;
esac
""",
        )
        self.readelf = self.base / "llvm-readelf"
        self.readelf.symlink_to(multiplexer.name)
        result = self.create()
        self.assertEqual(0x4000, result["libvlc"]["loadAlignment"])


if __name__ == "__main__":
    unittest.main()
