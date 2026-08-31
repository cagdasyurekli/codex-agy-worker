#!/usr/bin/env python3
"""Mechanical guard rejecting new version-stamped executable algorithm copies.

Issue #108 replaces version-specific executable algorithm families with a single
manifest-driven common engine. New supported agy versions must be added via declarative
manifest entries (in compat/agy-version-manifest.json), not by copying algorithm files.

This guard scans repository script directories and enforces:
1. No newly introduced version-stamped executable algorithm scripts beyond the frozen
   legacy migration adapter allowlist.
2. Historical evidence (reviews, fixture files, data assets) is permitted.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple, Union

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]

# Frozen current-version adapters explicitly permitted for backward compatibility.
CURRENT_ADAPTER_ALLOWLIST: Set[str] = {
    "scripts/models_capture_1_1_22_classifier.py",
    "scripts/models_capture_1_1_22_profile.py",
    "scripts/models_capture_1_1_22_reprofile.py",
    "scripts/models_capture_1_1_22_runner.py",
    "scripts/models_capture_1_1_22_version_evidence.py",
}

# Regex matching any version-stamped executable script filenames across multiple naming variants.
VERSION_STAMP_PATTERNS: List[re.Pattern] = [
    re.compile(r".*_(?:[0-9]+_[0-9]+(?:_[0-9]+)?)(?:_[a-z0-9_]+)?\.py\Z"),
    re.compile(r".*_v?(?:[0-9]+(?:_[0-9]+)+)\.py\Z"),
    re.compile(r".*(?:version|models_capture|recovery).*(?:[0-9]+_[0-9]+).*\.py\Z"),
]


def audit_version_copies(repo_root: Optional[Union[Path, str]] = None) -> Tuple[bool, List[str]]:
    root = Path(repo_root) if repo_root is not None else ROOT
    violations: List[str] = []

    scan_dirs = [
        root / "scripts",
        root / "skills" / "agy-worker" / "runtime" / "scripts",
    ]

    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for entry in scan_dir.rglob("*.py"):
            rel_path = str(entry.relative_to(root))
            filename = entry.name
            is_version_stamped = any(p.match(filename) for p in VERSION_STAMP_PATTERNS)
            if is_version_stamped:
                if rel_path not in CURRENT_ADAPTER_ALLOWLIST:
                    violations.append(
                        f"disallowed version-stamped algorithm copy: {rel_path} "
                        f"(add new versions to compat/agy-version-manifest.json instead)"
                    )

    return len(violations) == 0, violations


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    target_root = Path(args[0]) if args else ROOT
    ok, violations = audit_version_copies(target_root)
    if not ok:
        for violation in violations:
            print(f"version copy guard: VIOLATION: {violation}", file=sys.stderr)
        return 1
    print("version copy guard: ok (no disallowed version-stamped algorithm copies)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
