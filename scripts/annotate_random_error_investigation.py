"""Additively record the 2026-10-22 half-hourly randomError reconstruction in the Late manifests.

Adds ONE new top-level key, ``randomError_investigation``. No existing key or value is changed or
removed (checked before writing; the script aborts otherwise). Idempotent. Status stays
``as_delivered_unverified``.

    python scripts/annotate_random_error_investigation.py <manifest.json> [<manifest.json> ...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

KEY = "randomError_investigation"

INVESTIGATION = {
    "status": "as_delivered_unverified",
    "summary": "One-day half-hourly reconstruction; not an official uncertainty estimate and not NASA-confirmed.",
    "date_investigated": "2026-10-30",
    "day_examined": "2026-10-22",
    "confirmed_reconstruction_result": {
        "scope": "one day (2026-10-22), product V07C Late, global grid",
        "daily_product": "GPM_3IMERGDL 07, variable randomError (labelled mm/day), randomError_cnt",
        "half_hourly_product": "GPM_3IMERGHHL 07, variable randomError (mm/hr), Grid group",
        "half_hourly_granules": "48/48, 00:00:00-23:30:00 UTC at 30 min steps, no gaps",
        "half_hourly_total_bytes": 357080310,
        "valid_global_cells": 6479924,
        "reconstruction": "sum over valid half-hours of (half-hourly randomError)^2, no scaling",
        "correlation_with_stored_daily": 1.0,
        "max_absolute_difference": 0.107,
        "max_absolute_difference_note": "float32 rounding, at stored values up to about 1.3e5",
        "chennai_aoi_cells": 16,
        "chennai_aoi_max_absolute_difference": 0.0001,
        "count_check": "randomError_cnt equals the valid half-hour count in every daily-valid cell",
        "raw_files": "data/raw/investigations/imerg-late-halfhourly-20261022/ (SHA256SUMS.txt, download_log.json)",
    },
    "conflict_with_published_formula": {
        "published_daily_definition": "Perr_daily = sqrt(sum(Perr_i^2 * valid) / N_err * 24), RMS, mm/day",
        "published_formula_reproduces_stored_values": False,
        "global_mean_stored": 258.78,
        "global_mean_published_formula": 5.2,
        "global_relative_difference_median": -0.5747,
        "global_correlation_published_formula": 0.839,
        "chennai_mean_stored": 750.33,
        "chennai_mean_published_formula": 16.72,
        "dimensional_note": "with half-hourly inputs in mm/hr the published expression has units "
                            "mm/hr x sqrt(24), not mm/day; unresolved and not scaled here",
    },
    "pending_nasa_confirmation": [
        ("whether the stored daily value is intended to be the unnormalised sum of squares or is a "
         "processing defect or a documentation error"),
        "whether the same holds on other days, versions (V07B/V07C) and runs (Late/Final)",
        "the intended unit of the daily randomError",
    ],
    "restrictions": "Do not label the reconstructed quantity an official or validated uncertainty "
                    "estimate. Not exposed in the API or frontend, not used in exposure scores or "
                    "confidence, never summed across days.",
    "separate_discrepancy": "half-hourly file header ProductVersion=07B while file names say V07C",
}


def annotate(doc: dict) -> dict:
    """Return a copy of ``doc`` with ``randomError_investigation`` added; raises if anything else differs."""
    out = json.loads(json.dumps(doc))
    out[KEY] = INVESTIGATION
    stripped = {k: v for k, v in out.items() if k != KEY}
    if stripped != {k: v for k, v in doc.items() if k != KEY}:
        raise SystemExit("an original value would change; refusing to annotate")
    return out


def main(paths: list[str]) -> None:
    if not paths:
        raise SystemExit(__doc__)
    for p in map(Path, paths):
        doc = json.loads(p.read_text(encoding="utf-8"))
        p.write_text(json.dumps(annotate(doc), indent=2), encoding="utf-8")
        print(f"annotated {p.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
