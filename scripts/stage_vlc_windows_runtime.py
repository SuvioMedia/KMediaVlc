# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path


PINNED_REVISION = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
MODULE_NAME = re.compile(r"[a-z0-9_]+")
ALLOWED_FAMILIES = {
    "access",
    "access/http",
    "audio_filter",
    "audio_mixer",
    "audio_output",
    "codec",
    "demux",
    "hw/d3d11",
    "keystore",
    "logger",
    "misc",
    "packetizer",
    "stream_filter",
    "text_renderer",
    "video_chroma",
    "video_output",
    "video_output/win32",
}


def fail(message: str) -> None:
    raise SystemExit(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_plain_file(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*relative.split("/"))
    if candidate.is_symlink() or not candidate.is_file():
        fail(f"Required source-build file is missing or unsafe: {relative}")
    try:
        candidate.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        fail(f"Source-build file escapes its install root: {relative}")
    return candidate


def load_policy(
    root: Path, allow_audit_candidate: bool
) -> tuple[dict, dict, list[tuple[str, str]]]:
    path = root / "compliance/policy/windows-x86_64-playback-modules.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schemaVersion") != 1 or policy.get("target") != "windows-x86_64":
        fail("Unsupported Windows playback module policy.")
    if policy.get("vlcRevision") != PINNED_REVISION:
        fail("Windows playback modules target a different VLC revision.")
    if policy.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("Windows playback module dependencies have not completed review.")
    binary_policy = json.loads(
        (root / "compliance/policy/windows-x86_64-binary-components.json").read_text(
            encoding="utf-8"
        )
    )
    if (
        binary_policy.get("schemaVersion") != 1
        or binary_policy.get("target") != "windows-x86_64"
        or binary_policy.get("vlcRevision") != PINNED_REVISION
    ):
        fail("Unsupported Windows binary component policy.")
    if binary_policy.get("reviewStatus") != "approved" and not allow_audit_candidate:
        fail("Windows binary link inputs have not completed review.")
    if policy.get("primaryLicenseSpdx") != "LGPL-2.1-or-later":
        fail("Windows playback modules must retain their reviewed primary license.")
    families = policy.get("modulesByFamily")
    if not isinstance(families, dict) or set(families) != ALLOWED_FAMILIES:
        fail("Windows playback module families are incomplete or overbroad.")
    modules: list[tuple[str, str]] = []
    seen: set[str] = set()
    for family in sorted(families):
        names = families[family]
        if not isinstance(names, list) or names != sorted(names) or not names:
            fail(f"Windows playback module family is not a closed sorted list: {family}")
        for name in names:
            if not isinstance(name, str) or not MODULE_NAME.fullmatch(name) or name in seen:
                fail(f"Invalid or duplicate Windows playback module: {name!r}")
            seen.add(name)
            modules.append((family, name))
    additional = policy.get("additionalDirectSourceLicenses")
    if not isinstance(additional, dict) or not set(additional).issubset(seen):
        fail("Additional direct-source licenses reference an unknown module.")
    return policy, binary_policy, modules


def copy_file(source: Path, destination: Path) -> dict:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return {
        "path": destination,
        "size": destination.stat().st_size,
        "sha256": sha256(destination),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--install", type=Path, required=True)
    parser.add_argument("--bridge", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--allow-audit-candidate", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve(strict=True)
    install = args.install.resolve(strict=True)
    bridge = args.bridge.resolve(strict=True)
    output = args.output.resolve()
    report = args.report.resolve()
    if output.exists():
        fail("Windows runtime staging output must not already exist.")
    if report.exists():
        fail("Windows runtime staging report must not already exist.")
    if bridge.is_symlink() or not bridge.is_file():
        fail("The MSVC bridge input is missing or unsafe.")

    policy, binary_policy, modules = load_policy(root, args.allow_audit_candidate)
    copied: list[dict] = []
    fixed_files = [
        (require_plain_file(install, "bin/libvlc.dll"), "bin/libvlc.dll", "LIBVLC"),
        (require_plain_file(install, "bin/libvlccore-9.dll"), "bin/libvlccore-9.dll", "CORE"),
        (bridge, "bin/kmediavlc_bridge.dll", "BRIDGE"),
    ]
    for source, relative, role in fixed_files:
        result = copy_file(source, output.joinpath(*relative.split("/")))
        copied.append({**result, "path": relative, "role": role})

    plugin_source = install / "lib/vlc/plugins"
    plugin_destination = output / "lib/vlc/plugins"
    selected_names: list[str] = []
    for family, name in modules:
        filename = f"lib{name}_plugin.dll"
        source = require_plain_file(plugin_source, filename)
        relative = f"lib/vlc/plugins/{filename}"
        result = copy_file(source, plugin_destination / filename)
        copied.append({**result, "path": relative, "role": "PLUGIN", "family": family, "module": name})
        selected_names.append(name)

    raw_plugins = list(plugin_source.glob("lib*_plugin.dll"))
    if len(raw_plugins) < len(selected_names):
        fail("The source-build plugin set is smaller than the closed playback policy.")
    report.parent.mkdir(parents=True, exist_ok=True)
    report_payload = (
        json.dumps(
            {
                "schemaVersion": 1,
                "target": policy["target"],
                "vlcRevision": policy["vlcRevision"],
                "reviewStatus": policy["reviewStatus"],
                "binaryReviewStatus": binary_policy["reviewStatus"],
                "auditCandidate": (
                    policy["reviewStatus"] != "approved"
                    or binary_policy["reviewStatus"] != "approved"
                ),
                "selectedPluginCount": len(selected_names),
                "rawPluginCount": len(raw_plugins),
                "excludedPluginCount": len(raw_plugins) - len(selected_names),
                "files": copied,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    with report.open("w", encoding="utf-8", newline="\n") as destination:
        destination.write(report_payload)
    print(f"Staged {len(selected_names)} closed Windows playback plugins from {len(raw_plugins)} candidates.")


if __name__ == "__main__":
    main()
