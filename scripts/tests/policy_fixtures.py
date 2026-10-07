# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def approved_policy_fixture(destination: Path) -> Path:
    """Build a synthetic approved recipe without changing the current source policies."""
    for directory in ("compliance/policy", "build-recipes", "LICENSES"):
        shutil.copytree(ROOT / directory, destination / directory)
    for name in ("stage_vlc_linux_runtime.py", "stage_vlc_macos_runtime.py"):
        path = destination / "scripts" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / name, path)
    for path in (destination / "compliance/policy").glob("*.json"):
        policy = json.loads(path.read_text(encoding="utf-8"))
        if path.name.startswith(("linux-", "macos-aarch64-", "windows-x86_64-")):
            policy["reviewStatus"] = "approved"
            path.write_text(json.dumps(policy), encoding="utf-8")
    return destination
