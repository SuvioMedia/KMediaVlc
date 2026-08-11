#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import tempfile
from pathlib import Path


PACKAGER_SPEC = importlib.util.spec_from_file_location(
    "kmediavlc_package_native_runtime",
    Path(__file__).with_name("package_native_runtime.py"),
)
if PACKAGER_SPEC is None or PACKAGER_SPEC.loader is None:
    raise RuntimeError("Cannot load package_native_runtime.py")
package_native_runtime = importlib.util.module_from_spec(PACKAGER_SPEC)
PACKAGER_SPEC.loader.exec_module(package_native_runtime)


REQUIRED_TARGETS = [
    "linux-aarch64",
    "linux-x86_64",
    "macos-aarch64",
    "windows-x86_64",
]


def fail(message: str) -> None:
    raise ValueError(message)


def real_input(path: object, label: str, directory: bool) -> Path:
    if not isinstance(path, str) or not path or "\x00" in path:
        fail(f"Desktop runtime matrix contains an invalid {label} path.")
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        fail(f"Desktop runtime matrix {label} path must be absolute and non-symbolic.")
    resolved = candidate.resolve(strict=True)
    if directory and not resolved.is_dir():
        fail(f"Desktop runtime matrix {label} must be a directory.")
    if not directory and not resolved.is_file():
        fail(f"Desktop runtime matrix {label} must be a file.")
    return resolved


def load_matrix(path: Path) -> list[dict]:
    path = path.resolve(strict=True)
    if path.is_symlink() or not path.is_file():
        fail("Desktop runtime matrix must be a real file.")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "payloads"}:
        fail("Desktop runtime matrix root fields are not closed.")
    if value["schemaVersion"] != 1:
        fail("Desktop runtime matrix schema is unsupported.")
    payloads = value["payloads"]
    if not isinstance(payloads, list) or len(payloads) != len(REQUIRED_TARGETS):
        fail("Desktop runtime matrix must contain every required target exactly once.")
    targets = [entry.get("target") for entry in payloads if isinstance(entry, dict)]
    if targets != REQUIRED_TARGETS:
        fail("Desktop runtime matrix targets must be complete, unique, and sorted.")
    checked: list[dict] = []
    for entry in payloads:
        if set(entry) != {"target", "staging", "inventory"}:
            fail(f"Desktop runtime matrix payload fields are not closed: {entry!r}")
        checked.append(
            {
                "target": entry["target"],
                "staging": real_input(entry["staging"], "staging", True),
                "inventory": real_input(entry["inventory"], "inventory", False),
            }
        )
    return checked


def package_matrix(
    root: Path,
    matrix_path: Path,
    source_offer: str,
    recipe_revision: str,
    output: Path,
) -> list[str]:
    root = root.resolve(strict=True)
    payloads = load_matrix(matrix_path)
    output = output.resolve()
    if output.exists() or output.is_symlink():
        fail("Desktop runtime matrix output must not already exist.")
    if not output.parent.is_dir() or output.parent.is_symlink():
        fail("Desktop runtime matrix output parent must be a real directory.")
    runtime_ids: list[str] = []
    with tempfile.TemporaryDirectory(
        prefix=".kmediavlc-native-matrix-",
        dir=output.parent,
    ) as temporary_name:
        temporary = Path(temporary_name)
        for payload in payloads:
            target_output = temporary / payload["target"]
            runtime_ids.append(
                package_native_runtime.package(
                    root,
                    payload["staging"],
                    payload["inventory"],
                    payload["target"],
                    source_offer,
                    recipe_revision,
                    target_output,
                )
            )
        native_output = output / "META-INF/kmediavlc/native"
        native_output.mkdir(parents=True)
        for target in REQUIRED_TARGETS:
            source = temporary / target / "META-INF/kmediavlc/native" / target
            if not source.is_dir() or source.is_symlink():
                fail(f"Packager did not produce the required target: {target}")
            shutil.copytree(source, native_output / target)
    return runtime_ids


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--source-offer", required=True)
    parser.add_argument("--recipe-revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    runtime_ids = package_matrix(
        arguments.root,
        arguments.matrix,
        arguments.source_offer,
        arguments.recipe_revision,
        arguments.output,
    )
    print(
        "Packaged the complete KMediaVlc desktop matrix: "
        + ", ".join(
            f"{target}={runtime_id}"
            for target, runtime_id in zip(REQUIRED_TARGETS, runtime_ids, strict=True)
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
