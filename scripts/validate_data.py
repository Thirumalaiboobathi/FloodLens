"""Validate every dataset manifest and the files they reference (parents, checksums).

Usage: python scripts/validate_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from floodlens_geo.metadata.integrity import check_manifests
from floodlens_geo.metadata.manifest import load_all

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    man_dir = ROOT / "data" / "manifests"
    for m in load_all(man_dir):  # schema + unique ids
        print(f"ok  {m.dataset_id}")
    problems = check_manifests(man_dir, ROOT)
    for p in problems:
        print("PROBLEM", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
