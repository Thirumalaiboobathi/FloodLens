"""Per-indicator diagnosis of a location (screening-index sensitivity aid, not validation).

Usage: python scripts/diagnose_point.py --lon 80.22 --lat 12.98 [--radius 500]
Prints raw indicator values, 0..1 scores and percentile within the scored land for the cell and its
neighbourhood. Read-only.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import rasterio
from floodlens_geo.exposure import engine
from floodlens_geo.exposure.config import ExposureConfig
from pyproj import Transformer
from rasterio.windows import Window

ROOT = Path(__file__).resolve().parents[1]
PLACES = {  # approximate centres; used only as sampling anchors
    "velachery": (80.2207, 12.9815), "t_nagar": (80.2341, 13.0418), "mudichur": (80.0700, 12.9000),
    "pallikaranai": (80.2130, 12.9400), "adyar_mouth": (80.2700, 13.0100),
    "kolathur": (80.2130, 13.1200),
}


def main(lon: float, lat: float, radius_m: int) -> None:
    terr = ROOT / "data/processed/terrain/chennai"
    exp = ROOT / "data/processed/exposure/chennai"
    files = {"elevation": terr / "dem_land_utm.tif", "slope": terr / "slope_deg.tif",
             "twi": terr / "whitebox/twi.tif", "flow_accumulation": terr / "whitebox/sca.tif",
             "hand": terr / "whitebox/hand.tif", "water_distance": exp / "water_distance_m.tif"}
    cfg = ExposureConfig()
    with rasterio.open(files["elevation"]) as r:
        x, y = Transformer.from_crs(4326, r.crs, always_xy=True).transform(lon, lat)
        row, col = r.index(x, y)
        cell = r.res[0]
    n = max(int(radius_m / cell), 0)
    win = Window(col - n, row - n, 2 * n + 1, 2 * n + 1)
    raw = {k: engine.read_masked(p, win) for k, p in files.items()}
    full = {k: engine.read_decimated(p) for k, p in files.items()}
    lm = engine.read_decimated(exp / "permanent_water_mask.tif") != 1
    norm = engine.derive_normalisation(np.where(lm, full["twi"], np.nan),
                                       np.where(lm, full["flow_accumulation"], np.nan),
                                       cfg.transforms)
    sc = engine.indicator_scores(raw, cfg.transforms, norm)
    idx, _ = engine.combine(sc, cfg.weights, cfg.min_completeness)
    with rasterio.open(exp / "baseline_class.tif") as r:
        cls = r.read(1, window=win)
    c = n
    print(f"cell row={row} col={col} (±{n} cells = ±{n * cell:.0f} m)")
    print(f"{'indicator':18s}{'centre':>10s}{'nbhd median':>13s}{'nbhd min':>10s}{'nbhd max':>10s}"
          f"{'score(c)':>10s}{'land pctile':>13s}")
    for k, a in raw.items():
        v = a[c, c]
        ref = full[k][lm & np.isfinite(full[k])]
        pct = 100 * (ref < v).mean() if np.isfinite(v) else float("nan")
        s = sc[k][c, c] if sc.get(k) is not None else float("nan")
        print(f"{k:18s}{v:10.2f}{np.nanmedian(a):13.2f}{np.nanmin(a):10.2f}{np.nanmax(a):10.2f}"
              f"{s:10.3f}{pct:13.1f}")
    print(f"index(centre)={idx[c, c]:.3f}  class(centre)={cls[c, c]}  "
          f"class share in window: { {int(k): round(float((cls == k).mean()), 2) for k in np.unique(cls)} }")
    print("score-weighted contribution:",
          {k: round(float(sc[k][c, c]) * w, 3) for k, w in cfg.weights.as_dict().items()
           if sc.get(k) is not None})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--lon", type=float)
    ap.add_argument("--lat", type=float)
    ap.add_argument("--place", choices=sorted(PLACES))
    ap.add_argument("--radius", type=int, default=500)
    a = ap.parse_args()
    lo, la = PLACES[a.place] if a.place else (a.lon, a.lat)
    main(lo, la, a.radius)
