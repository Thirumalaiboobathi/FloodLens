"""Sensitivity analysis of the exposure index for a pilot region.

Usage: python scripts/run_sensitivity.py --region chennai [--step 3]
Decimates by ``--step`` to stay within 8 GB RAM. Writes
data/processed/exposure/<region>/sensitivity.json. This is a stability analysis, not a validation.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from floodlens_geo.exposure import engine, sensitivity
from floodlens_geo.exposure.config import ExposureConfig
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
# Approximate boxes (lon0, lat0, lon1, lat1) used only to summarise class distributions in
# localities with reported flooding. They are NOT flood extents.
AREAS = {"velachery": (80.205, 12.965, 80.240, 13.000),
         "pallikaranai_adjacent": (80.195, 12.920, 80.235, 12.955),
         "t_nagar": (80.225, 13.030, 80.245, 13.050),
         "kolathur_perambur": (80.200, 13.100, 80.250, 13.130)}


def main(region: str, step: int) -> None:
    terr = ROOT / "data/processed/terrain" / region
    exp = ROOT / "data/processed/exposure" / region
    paths = {"elevation": terr / "dem_land_utm.tif", "slope": terr / "slope_deg.tif",
             "twi": terr / "whitebox/twi.tif", "flow_accumulation": terr / "whitebox/sca.tif",
             "hand": terr / "whitebox/hand.tif", "water_distance": exp / "water_distance_m.tif"}
    layers = {k: engine.read_decimated(p, step) for k, p in paths.items()}
    land = engine.read_decimated(exp / "permanent_water_mask.tif", step) != 1
    land &= np.isfinite(layers["elevation"])
    with rasterio.open(paths["elevation"]) as r:
        tf, shape = r.transform, layers["elevation"].shape
        to_utm = Transformer.from_crs(4326, r.crs, always_xy=True)
    areas = {}
    for name, (x0, y0, x1, y1) in AREAS.items():
        c0, r1 = ~tf * to_utm.transform(x0, y0)
        c1, r0 = ~tf * to_utm.transform(x1, y1)
        m = np.zeros(shape, bool)
        m[max(int(r0 // step), 0):int(r1 // step) + 1, max(int(c0 // step), 0):int(c1 // step) + 1] = True
        areas[name] = m & land
    res = sensitivity.run_all(layers, land, ExposureConfig(), areas)
    res["_meta"] = {"step": step, "cell_size_m": 30 * step, "scored_cells": int(land.sum()),
                    "areas_bbox_lonlat": AREAS,
                    "note": "Stability of relative classes under heuristic perturbation; not a "
                            "validation against observed floods."}
    (exp / "sensitivity.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print("baseline mean class by area:",
          {k: v["mean_class"] for k, v in res["baseline"]["areas"].items()})
    for grp in ("leave_one_out", "weight_x0.5", "weight_x1.5", "scale_x0.5", "scale_x2",
                "dem_vertical_shift"):
        print(f"\n[{grp}]")
        for k, v in res[grp].items():
            print(f"  {k:18s} rho={v['spearman']:.3f} same={v['class_unchanged']:.2f} "
                  f"top-jacc={v['top_class_overlap_jaccard']:.2f} "
                  f"velachery={v['areas']['velachery']['mean_class']:.2f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="chennai")
    ap.add_argument("--step", type=int, default=3)
    a = ap.parse_args()
    main(a.region, a.step)
