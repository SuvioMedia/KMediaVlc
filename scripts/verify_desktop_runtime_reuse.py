#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind a Java loader hotfix to unchanged audited native build inputs."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path


JAVA = "runtime-desktop/src/main/java/io/github/shusek/kmediavlc/runtime/desktop/"
TEST = "runtime-desktop/src/test/java/io/github/shusek/kmediavlc/runtime/desktop/"
PROTECTED = (
    "native", "runtime-desktop/src", "build-recipes", "patches/vlc",
    "gradle/libs.versions.toml", "compliance/policy",
    "scripts/package_native_runtime.py", "scripts/package_native_runtime_matrix.py",
)
REVIEWED_LOADER_PATHS = {
    JAVA + "NativePayloadManifest.java",
    JAVA + "VlcDesktopRuntime.java",
    TEST + "NativePayloadManifestTest.java",
    TEST + "VlcExternalRuntimeTest.java",
    "runtime-desktop/src/main/resources/META-INF/native-image/io.github.shusek/"
    "kmedia-vlc-runtime-desktop/reachability-metadata.json",
}
OLD_HEADER = b"// SPDX-License-Identifier: LGPL-2.1-or-later\n"
NEW_HEADER = b"// SPDX-License-Identifier: MIT OR LGPL-2.1-or-later\n"


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def license_header_only(path: str, old: bytes, new: bytes) -> bool:
    return (
        path.startswith(JAVA) and path.endswith(".java")
        and old.startswith(OLD_HEADER) and new.startswith(NEW_HEADER)
        and old[len(OLD_HEADER):] == new[len(NEW_HEADER):]
    )


def verify(root: Path, runtime_commit: str, release_commit: str) -> None:
    for revision in (runtime_commit, release_commit):
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError("Runtime reuse requires exact immutable commits.")
        if git(root, "rev-parse", revision + "^{commit}").decode().strip() != revision:
            raise ValueError("Runtime reuse commit does not match.")
    git(root, "merge-base", "--is-ancestor", runtime_commit, release_commit)
    changed = git(root, "diff", "--name-only", "-z", runtime_commit, release_commit, "--", *PROTECTED)
    for path in changed.decode().split("\0"):
        if not path or path in REVIEWED_LOADER_PATHS:
            continue
        if path.startswith(JAVA) and path.endswith(".java"):
            try:
                old = git(root, "show", runtime_commit + ":" + path)
                new = git(root, "show", release_commit + ":" + path)
            except subprocess.CalledProcessError:
                raise ValueError("Unreviewed Java addition or deletion: " + path) from None
            if license_header_only(path, old, new):
                continue
        raise ValueError("Native reuse requires a new audit for: " + path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--runtime-commit", required=True)
    parser.add_argument("--release-commit", required=True)
    args = parser.parse_args()
    verify(args.root, args.runtime_commit, args.release_commit)
    print("DESKTOP_NATIVE_REUSE_INPUTS_VERIFIED")


if __name__ == "__main__":
    main()
