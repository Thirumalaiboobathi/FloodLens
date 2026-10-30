"""Hash inventory of the Git-ignored data, so protected files can be checked between phases (read-only on data).

Usage: python scripts/data_baseline.py --write   # record hashes into the Phase 6 baseline report
       python scripts/data_baseline.py --verify  # recompute and compare; exit 1 on any difference

Git cannot vouch for data/raw, data/processed or data/api (all ignored); this inventory can, from the point it was recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pre-deployment" / "phase6-git-baseline.json"
PER_FILE = ("data/raw", "data/processed")  # listed one by one; data/api (630 files) is rebuildable, so one aggregate hash
AGGREGATE = ("data/api",)


def file_sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect() -> dict:
    out: dict = {"per_file": {}, "aggregate": {}}
    for d in PER_FILE:
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file():
                out["per_file"][p.relative_to(ROOT).as_posix()] = {"bytes": p.stat().st_size, "sha256": file_sha(p)}
    for d in AGGREGATE:
        h, n = hashlib.sha256(), 0
        for p in sorted((ROOT / d).rglob("*")):
            if p.is_file():
                h.update(p.relative_to(ROOT).as_posix().encode() + b"\0" + file_sha(p).encode() + b"\n")
                n += 1
        out["aggregate"][d] = {"files": n, "sha256_of_listing": h.hexdigest()}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--write", action="store_true")
    g.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    now = collect()
    if a.write:
        doc = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
        head = subprocess.run(["git", "rev-parse", "HEAD"], check=False, cwd=ROOT, capture_output=True, text=True).stdout.strip()
        doc["ignored_data_inventory"] = {
            "recorded_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "git_head_at_recording": head,
            "note": "Recorded from disk; Git does not track these paths, so this proves 'unchanged since recording', not 'unchanged since Phase 1'.",
            **now}
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"recorded {len(now['per_file'])} files + {sum(v['files'] for v in now['aggregate'].values())} api files")
        return 0
    rec = json.loads(REPORT.read_text(encoding="utf-8"))["ignored_data_inventory"]
    bad = [k for k in rec["per_file"].keys() | now["per_file"].keys() if rec["per_file"].get(k) != now["per_file"].get(k)]
    bad += [k for k in rec["aggregate"] if rec["aggregate"][k] != now["aggregate"].get(k)]
    print("data inventory:", "UNCHANGED" if not bad else f"DIFFERENT: {sorted(bad)[:10]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
