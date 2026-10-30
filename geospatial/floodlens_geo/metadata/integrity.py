"""Manifest integrity checks: parents exist, referenced files exist and match their checksums."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_manifests(man_dir: Path, root: Path) -> list[str]:
    """Return a list of problems (empty = consistent). Paths in manifests are relative to root."""
    docs = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(man_dir.glob("*.json"))}
    ids = {d["dataset_id"] for d in docs.values()}
    problems = []
    for name, doc in docs.items():
        for parent in doc.get("derived_from", []):
            if parent not in ids:
                problems.append(f"{name}: derived_from '{parent}' has no manifest")
        if doc.get("file") and doc.get("checksum_sha256"):
            f = root / doc["file"]
            if not f.exists():
                problems.append(f"{name}: missing file {doc['file']}")
            elif sha256(f) != doc["checksum_sha256"]:
                problems.append(f"{name}: checksum mismatch for {doc['file']}")
        for rel, digest in doc.get("granule_files", {}).items():
            f = root / rel
            if not f.exists() or sha256(f) != digest:
                problems.append(f"{name}: granule {rel} missing or checksum mismatch")
        for key, info in doc.get("products", {}).items():
            f = root / info["file"]
            if not f.exists() or sha256(f) != info["sha256"]:
                problems.append(f"{name}: product {key} missing or checksum mismatch")
        if doc.get("kind") == "fixture":
            problems.append(f"{name}: synthetic fixtures must not be listed as datasets")
    return problems
