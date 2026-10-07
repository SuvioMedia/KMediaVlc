#!/usr/bin/env python3
# SPDX-License-Identifier: LGPL-2.1-or-later

"""Scan a conservative source closure for the closed desktop playback policies."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path


REVISION = "04d555a9d391f009d4f510f508fef58c39bdf810"
BASELINE = "e439692079a75cacb5f07310d1ec2dc20bfd1fe0"
SOURCE_SUFFIXES = {".c", ".cpp", ".cc", ".m", ".mm", ".h", ".hpp", ".s", ".l", ".y"}
# These files have no standalone license header. Their exact bytes and project
# context were checked. A changed file must be reviewed again instead of
# silently inheriting this exception. The update-check public-key data header
# is included conservatively even though update checking is disabled.
PROJECT_CONTEXT = {
    "compat/dummy.c": "5b692e48dccad75abaf76453ea2b8eb5a1d56190b629b4d9042034da82eb7657",
    "compat/lfind.c": "a8064a8dbd90b839b56f92eecd28322cbefde45835d36edd3aed5ee52fc494ef",
    "include/vlc_pgpkey.h": "facffbdc8a941a63fd25cef799c455b3b028933292725859fda5e893835f6f47",
    "modules/codec/vt_utils_native.m": "05597fb294ca6871443f42ad278d885ffc8bf342b98965c13ea1ece201584ec4",
    "modules/demux/adaptive/logic/RoundRobinLogic.cpp": "14348749f18e2ec7d5fa9943d8494a1910c0a419d3b099b23069d1c4e8d2cc3c",
    "modules/demux/adaptive/logic/RoundRobinLogic.hpp": "0202e502d5a2c54801e14ed38d24dc261e625e40acd2bdca449bc3c9e8c0f087",
}
ALLOWED_SOURCE_LICENSES = {
    "BSD-2-Clause", "BSD-3-Clause", "ISC", "LGPL-2.1-or-later",
    "LicenseRef-Public-Domain", "MIT",
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(relative: str, data: bytes) -> tuple[str, str]:
    header = re.split(r"#\s*(?:include|import)", data.decode("utf-8", "replace")[:8192], 1)[0]
    spdx = re.search(r"SPDX-License-Identifier:\s*([^\r\n]+)", header)
    header = re.sub(r"\s*\*?\s*\n\s*\*?\s*", " ", header)
    if spdx:
        identifier, basis = spdx[1].split("*/", 1)[0].strip(), "SPDX-License-Identifier"
    elif re.search(r"GNU (?:Lesser|Library) General Public License", header, re.I):
        identifier, basis = "LGPL-2.1-or-later", "LGPL-library-header"
    elif re.search(r"GNU General Public License|Affero General Public License", header, re.I):
        raise ValueError(f"Forbidden source license: {relative}")
    elif re.search(r"MIT license|Permission is (?:hereby )?granted", header, re.I):
        identifier, basis = "MIT", "MIT-header"
    elif re.search(r"Permission to use, copy", header, re.I):
        identifier, basis = "ISC", "ISC-header"
    elif re.search(r"Redistribution and use in source and binary forms", header, re.I):
        identifier = "BSD-3-Clause" if re.search(r"endorse|Neither the name", header, re.I) else "BSD-2-Clause"
        basis = "BSD-header"
    elif relative == "compat/tfind.c" and b"Totally public domain." in data:
        identifier, basis = "LicenseRef-Public-Domain", "explicit-public-domain"
    elif PROJECT_CONTEXT.get(relative) == digest(data):
        identifier, basis = "LGPL-2.1-or-later", "exact-bytes-project-library-context"
    else:
        raise ValueError(f"Unresolved source license: {relative}")
    if identifier not in ALLOWED_SOURCE_LICENSES:
        raise ValueError(f"Unapproved source license {identifier}: {relative}")
    return identifier, basis


def scan(root: Path, source: Path) -> dict:
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != REVISION:
        raise ValueError("The source scan must use the exact pinned VLC revision.")
    variables: dict[str, list[str]] = {}
    for path in sorted((source / "modules").rglob("Makefile.am")):
        text = re.sub(r"\\\n\s*", " ", path.read_text(encoding="utf-8"))
        for line in text.splitlines():
            match = re.match(r"^([A-Za-z0-9_]+)\s*(\+=|=)\s*(.*)", line)
            if match:
                key, _, value = match.groups()
                # Retain every conditional branch. This intentionally scans
                # more sources than any one host compiles.
                variables.setdefault(key, []).append(value.split("#", 1)[0])

    def expand(key: str, seen: frozenset[str] = frozenset()) -> str:
        if key in seen:
            raise ValueError(f"Cyclic source variable: {key}")
        value = " ".join(variables.get(key, []))
        return re.sub(r"\$\(([A-Za-z0-9_]+)\)", lambda match: expand(match[1], seen | {key}), value)

    def closure(name: str, seen: frozenset[str] = frozenset()) -> set[Path]:
        if name in seen:
            return set()
        result: set[Path] = set()
        for token in expand(name + "_SOURCES").split():
            path = source / "modules" / token
            if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES:
                path = path.resolve(strict=True)
                path.relative_to(source)
                result.add(path)
        for token in expand(name + "_LIBADD").split():
            if token.endswith(".la") and "/" not in token:
                result |= closure(token[:-3].replace("-", "_") + "_la", seen | {name})
        return result

    selected: set[Path] = set()
    module_counts: dict[str, int] = {}
    for platform in ("windows-x86_64", "linux", "macos-aarch64"):
        policy = json.loads((root / f"compliance/policy/{platform}-playback-modules.json").read_text())
        if policy["vlcRevision"] != revision:
            raise ValueError("Source and playback policies use different VLC revisions.")
        module_counts[platform] = 0
        for modules in policy["modulesByFamily"].values():
            for module in modules:
                files = closure(f"lib{module}_plugin_la")
                if not files:
                    raise ValueError(f"Unresolved selected module sources: {platform}/{module}")
                selected |= files
                module_counts[platform] += 1
    # Scan all platform variants of library code, excluding upstream tests.
    for directory in ("src", "lib", "compat"):
        selected |= {
            path for path in (source / directory).rglob("*")
            if path.is_file() and "/test/" not in str(path)
            and path.suffix.lower() in SOURCE_SUFFIXES - {".h", ".hpp"}
        }
    queue = list(selected)
    while queue:
        path = queue.pop()
        for name in re.findall(r'^\s*#\s*(?:include|import)\s*[<"]([^>"]+)[>"]', path.read_text(errors="replace"), re.M):
            for parent in (path.parent, source / "include", source / "modules", source / "src", source / "compat", source):
                included = parent / name
                if included.is_file() and not included.is_symlink():
                    included = included.resolve(strict=True)
                    included.relative_to(source)
                    if included not in selected:
                        selected.add(included)
                        queue.append(included)
                    break
    files = []
    counts: Counter[str] = Counter()
    for path in sorted(selected):
        relative = path.relative_to(source).as_posix()
        data = path.read_bytes()
        identifier, basis = classify(relative, data)
        counts[identifier] += 1
        files.append({"path": relative, "sha256": digest(data), "licenseSpdx": identifier, "basis": basis})
    changed = set(subprocess.check_output(["git", "-C", str(source), "diff", "--name-only", BASELINE, revision], text=True).splitlines())
    return {
        "schemaVersion": 1, "vlcRevision": revision,
        "vlcTree": subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], text=True).strip(),
        "baselineRevision": BASELINE, "scope": "conservative-selected-modules-and-library-source-closure",
        "moduleCounts": module_counts, "sourceFileCount": len(files),
        "changedSourceFileCount": len(changed & {entry["path"] for entry in files}),
        "licenseCounts": dict(sorted(counts.items())), "unresolvedModuleCount": 0,
        "forbiddenLicenseDetected": False, "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--vlc-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = scan(arguments.root.resolve(strict=True), arguments.vlc_source.resolve(strict=True))
    arguments.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Desktop source license scan passed: {report['sourceFileCount']} files, {report['changedSourceFileCount']} changed; no unresolved module or forbidden license.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
