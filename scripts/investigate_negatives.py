"""Classify negative-elevation cells in the processed Chennai DEM against Copernicus auxiliary layers.

Reference layers (AWS Open Data, same tile folders): WBM water-body mask, EDM editing mask,
HEM height-error mask, FLM filling mask; plus the independent GLO-90 product and OSM quarries.
Writes data/processed/terrain/<region>/negative_cells_report.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
import geopandas as gpd
import numpy as np
import rasterio
from botocore import UNSIGNED
from botocore.config import Config
from floodlens_geo.ingestion import osm
from scipy.ndimage import label
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[1]
REGION = sys.argv[1] if len(sys.argv) > 1 else "chennai"
TILES = ["N12_00_E080_00", "N13_00_E080_00"]
AUX = ["EDM", "WBM", "HEM", "FLM"]
raw = ROOT / "data" / "raw" / "dem" / REGION
auxdir = raw / "aux"
auxdir.mkdir(exist_ok=True)
s3 = boto3.client("s3", region_name="eu-central-1", config=Config(signature_version=UNSIGNED))
for t in TILES:
    base = f"Copernicus_DSM_COG_10_{t}_DEM"
    for a in AUX:
        out = auxdir / f"{t}_{a}.tif"
        if not out.exists():
            s3.download_file("copernicus-dem-30m", f"{base}/AUXFILES/Copernicus_DSM_COG_10_{t}_{a}.tif", str(out))
    g90 = raw / f"{t}_GLO90.tif"
    if not g90.exists():
        b9 = f"Copernicus_DSM_COG_30_{t}_DEM"
        s3.download_file("copernicus-dem-90m", f"{b9}/{b9}.tif", str(g90))

with rasterio.open(ROOT / "data/processed/terrain" / REGION / "dem_land_utm.tif") as r:
    a = r.read(1, masked=True)
    tr, crs = r.transform, r.crs
neg = (a < 0).filled(False)
lab, n = label(neg, structure=np.ones((3, 3)))
rows, cols = np.where(neg)
xs, ys = rasterio.transform.xy(tr, rows, cols)
pts = gpd.GeoSeries([Point(x, y) for x, y in zip(xs, ys, strict=True)], crs=crs).to_crs(4326)
lonlat = list(zip(pts.x, pts.y, strict=True))


def sample(kind: str) -> np.ndarray:
    out = np.full(len(lonlat), np.nan)
    for t in TILES:
        p = (auxdir / f"{t}_{kind}.tif") if kind in AUX else (raw / f"{t}_GLO90.tif")
        with rasterio.open(p) as d:
            for i, (lo, la) in enumerate(lonlat):
                if d.bounds.left <= lo < d.bounds.right and d.bounds.bottom <= la < d.bounds.top:
                    out[i] = next(d.sample([(lo, la)], masked=False))[0]
    return out


wbm, edm, hem, flm, g90 = (sample(k) for k in (*AUX, "GLO90"))
z = a.filled(np.nan)[rows, cols]
q = osm.fetch(osm.build_query('nwr["landuse"="quarry"];nwr["natural"="quarry"]', "geom", (80.05, 12.83, 80.35, 13.27)))
quarries = osm.to_geodataframe(q, "quarry")
in_q = np.zeros(len(pts), bool)
if len(quarries):
    qu = quarries.geometry.union_all()
    in_q = np.array([qu.buffer(0.0003).contains(p) for p in pts])

cat = np.full(len(pts), "unexplained", dtype=object)
cat[(g90 < 0) & (cat == "unexplained")] = "also negative in GLO-90 (independent)"
cat[in_q] = "OSM quarry"
cat[wbm > 0] = "Copernicus water-body mask (water surface)"
lab_at = lab[rows, cols]
summary = {k: int((cat == k).sum()) for k in np.unique(cat)}
clusters = []
for k in range(1, n + 1):
    i = np.where(lab_at == k)[0]
    if len(i) < 10:
        continue
    clusters.append({
        "cells": len(i), "lon": round(float(pts.x.iloc[i].mean()), 4),
        "lat": round(float(pts.y.iloc[i].mean()), 4), "min_m": round(float(np.min(z[i])), 1),
        "glo90_median_m": round(float(np.nanmedian(g90[i])), 1),
        "wbm_water_share": round(float((wbm[i] > 0).mean()), 2),
        "edm_nonzero_share": round(float((edm[i] > 0).mean()), 2),
        "hem_median_m": round(float(np.nanmedian(hem[i])), 1),
        "in_quarry_share": round(float(in_q[i].mean()), 2),
    })
rep = {
    "negative_cells": int(neg.sum()), "below_minus_5m": int((z < -5).sum()), "clusters": n,
    "categories": summary,
    "wbm_water": int((wbm > 0).sum()), "edm_edited": int((edm > 0).sum()),
    "glo90_negative": int((g90 < 0).sum()), "in_osm_quarry": int(in_q.sum()),
    "hem_median_m": round(float(np.nanmedian(hem)), 2), "large_clusters": clusters,
    "quarries_in_osm": len(quarries),
}
print(json.dumps(rep, indent=1))
(ROOT / "data/processed/terrain" / REGION / "negative_cells_report.json").write_text(json.dumps(rep, indent=2))
