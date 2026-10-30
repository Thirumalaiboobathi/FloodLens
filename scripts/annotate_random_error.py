"""Additively annotate existing IMERG manifests with the randomError caveat.

Reads the already-downloaded granules (read-only) and the manifest JSON. Only NEW keys are added:
``data_quality_notes`` at the top level, ``status``/``note``/``excluded_cells``/``hypothesis`` under
``daily_provenance[*].quality.randomError`` and a ``randomError_cnt`` summary. Every original value is
preserved and checked; the script aborts without writing if any would change. Idempotent.

    python scripts/annotate_random_error.py <manifest.json> [<manifest.json> ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from floodlens_geo.ingestion import imerg

ROOT = Path(__file__).resolve().parents[1]
AOI = (80.05, 12.85, 80.35, 13.25)
ORIGINAL = ("units", "aoi_cells", "valid_cells", "missing_cells", "min", "mean", "max")


def annotate(doc: dict, root: Path = ROOT, bbox=AOI) -> dict:
    """Return a copy of ``doc`` with the additive annotation; raises if an original value differs."""
    out = json.loads(json.dumps(doc))
    for day in out["daily_provenance"]:
        granule = root / out_granule_path(out, day["granule_file"])
        field = imerg.read_daily(granule)
        new = imerg.quality_stats(granule, field, bbox)
        old = day["quality"]["randomError"]
        if new["randomError"]["excluded_cells"]:
            raise SystemExit(f"{granule.name}: {new['randomError']['excluded_cells']} cells would be "
                             "excluded; original statistics would change, refusing to annotate")
        for k in ORIGINAL:
            if old[k] != new["randomError"][k]:
                raise SystemExit(f"{granule.name}: original randomError {k} {old[k]} != {new['randomError'][k]}")
        old.update({k: v for k, v in new["randomError"].items() if k not in ORIGINAL})
        day["quality"]["randomError_cnt"] = new["randomError_cnt"]
    out["data_quality_notes"] = [imerg.RANDOM_ERROR_NOTE]
    return out


def out_granule_path(doc: dict, name: str) -> str:
    """Repo-relative path of a granule, from the manifest's own ``granule_files`` map."""
    for rel in doc.get("granule_files", {}):
        if rel.endswith("/" + name):
            return rel
    raise SystemExit(f"{name}: not in granule_files; cannot locate the raw granule")


def main(paths: list[str]) -> None:
    if not paths:
        raise SystemExit(__doc__)
    for p in map(Path, paths):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if "granule_files" not in doc:  # derived manifest: take the granule map from its source
            src = ROOT / "data" / "manifests" / f"{doc['derived_from'][0]}.json"
            doc["granule_files"] = json.loads(src.read_text(encoding="utf-8"))["granule_files"]
            new = annotate(doc)
            del new["granule_files"]
        else:
            new = annotate(doc)
        p.write_text(json.dumps(new, indent=2), encoding="utf-8")
        print(f"annotated {p.name}: {len(new['daily_provenance'])} days")


if __name__ == "__main__":
    main(sys.argv[1:])
