"""Run the FloodLens Exposure Screening Engine for a pilot region and write all products.

Usage: python scripts/run_exposure.py --region chennai [--config config.json]

Inputs : data/processed/terrain/<region>/*, Copernicus WBM water mask, OSM layers, boundaries,
         optional IMERG rainfall GeoTIFF (data/processed/rainfall/imerg-v07-total-<region>-*.tif).
Outputs: data/processed/exposure/<region>/ and data/manifests/exposure-screening-<region>.json
This is a screening index, NOT a validated flood probability, depth or extent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from floodlens_geo.exposure import engine, inputs, outputs
from floodlens_geo.exposure.config import ExposureConfig
from floodlens_geo.ingestion.dem import discover_tiles, download_aux
from floodlens_geo.metadata.manifest import (
    DatasetKind,
    DatasetManifest,
    ProcessingStatus,
)
from floodlens_geo.terrain.derivatives import water_mask_from_wbm

ROOT = Path(__file__).resolve().parents[1]
REGIONS = {"chennai": (80.05, 12.85, 80.35, 13.25)}
DISCLAIMER = (
    "Relative screening index from terrain, hydrology and mapped water. Not a flood probability, "
    "depth, extent, forecast or official warning; not validated against observed floods."
)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(region: str, cfg: ExposureConfig) -> None:
    t0 = time.time()
    terr = ROOT / "data" / "processed" / "terrain" / region
    out = ROOT / "data" / "processed" / "exposure" / region
    out.mkdir(parents=True, exist_ok=True)
    ref = terr / "dem_land_utm.tif"
    with rasterio.open(ref) as r:
        shape, tr, crs, cell = (r.height, r.width), r.transform, r.crs, r.res[0]

    # -- inputs on one grid -------------------------------------------------------------
    src = {"elevation": ref, "slope": terr / "slope_deg.tif", "twi": terr / "whitebox" / "twi.tif",
           "flow_accumulation": terr / "whitebox" / "sca.tif", "hand": terr / "whitebox" / "hand.tif"}
    files: dict[str, Path | None] = {}
    for k, p in src.items():
        if engine.same_grid(p, ref):
            files[k] = p
        else:  # different grid: warp, never assume
            a = engine.align_to(p, ref)
            files[k] = out / f"aligned_{k}.tif"
            with rasterio.open(ref) as rr:
                prof = rr.profile
            prof.update(dtype="float32", nodata=np.nan)
            with rasterio.open(files[k], "w", **prof) as d:
                d.write(a, 1)

    aux = ROOT / "data" / "raw" / "dem" / region / "aux"
    wbm = sorted(aux.glob("*_WBM.tif")) or download_aux(discover_tiles(REGIONS[region]), aux)
    water_dem = water_mask_from_wbm(wbm, ref)
    with rasterio.open(ref) as r:
        sea = r.read(1, masked=True).mask  # sea cells were set to nodata in the land DEM
    permanent_water = water_dem | sea

    osm_dir = ROOT / "data" / "processed" / "osm" / region
    water_osm = gpd.read_file(osm_dir / "water.geojson").to_crs(crs)
    osm_water_mask = inputs.rasterize_geometries(water_osm.geometry, shape, tr)
    dist = inputs.water_distance(permanent_water | osm_water_mask, cell)

    def save(name: str, arr: np.ndarray, dtype: str, nodata) -> Path:
        p = out / f"{name}.tif"
        with rasterio.open(ref) as rr:
            prof = rr.profile
        prof.update(dtype=dtype, nodata=nodata, compress="deflate")
        with rasterio.open(p, "w", **prof) as d:
            d.write(arr.astype(dtype), 1)
        return p

    files["water_distance"] = save("water_distance_m", np.nan_to_num(dist, nan=-9999.0), "float32",
                                   -9999.0)
    exclude = save("permanent_water_mask", permanent_water, "uint8", 255)
    print(f"permanent water excluded: {permanent_water.mean():.1%} of grid")

    # -- rainfall (real only) -------------------------------------------------------------
    rain_files = sorted((ROOT / "data" / "processed" / "rainfall").glob(f"imerg-v07-total-{region}-*.tif"))
    rain_aligned = None
    if rain_files:
        a = engine.align_to(rain_files[0], ref, rasterio.enums.Resampling.nearest)
        rain_aligned = save("observed_rainfall_mm", np.nan_to_num(a, nan=-9999.0), "float32", -9999.0)
        print("observed rainfall sample:", rain_files[0].name)
    else:
        print("observed rainfall: PENDING (no IMERG product); scenarios use hypothetical depths only")

    # -- normalisation, baseline, class thresholds -----------------------------------------
    land = ~permanent_water
    twi = engine.read_decimated(files["twi"])
    sca = engine.read_decimated(files["flow_accumulation"])
    lm = engine.read_decimated(exclude) != 1
    norm = engine.derive_normalisation(np.where(lm, twi, np.nan), np.where(lm, sca, np.nan),
                                       cfg.transforms)
    kw = {"files": files, "ref_path": ref, "out_dir": out, "cfg": cfg, "norm": norm,
          "exclude": exclude}
    base_idx, _, _ = engine.run_scenario("baseline", rainfall=None, reach_mm=None, thresholds=None, **kw)
    thr = engine.quantile_thresholds(engine.read_decimated(base_idx, 2), cfg.n_classes)
    n = cfg.n_classes
    labels = {k: f"Relative exposure class {k} of {n}" + (" (lowest)" if k == 1 else " (highest)" if k == n else "")
              for k in range(1, n + 1)}
    scen: dict[str, dict] = {}
    runs = {"baseline": (None, None)}
    if rain_aligned is not None:
        med = float(np.nanmedian(engine.read_masked(rain_aligned)[land]))
        runs["observed_sample"] = (rain_aligned, med)
    for mm in cfg.scenario_rainfall_mm:
        runs[f"rain_{mm:g}mm"] = (mm, mm)
    for name, (rain, reach) in runs.items():
        scen[name] = dict(zip(("index", "class", "completeness"), engine.run_scenario(
            name, rainfall=rain, reach_mm=reach, thresholds=thr, **kw), strict=True))

    # -- vectors and summaries --------------------------------------------------------------
    roads = gpd.read_file(osm_dir / "roads.geojson").to_crs(crs)
    hosp = gpd.read_file(osm_dir / "hospitals.geojson")
    schools = gpd.read_file(osm_dir / "schools.geojson")
    regions = {
        "chennai_core_geoboundaries": gpd.read_file(
            ROOT / "data/processed/boundaries/chennai-district.geojson").to_crs(crs).geometry.iloc[0],
        "chennai_district_osm": gpd.read_file(
            ROOT / "data/processed/boundaries/chennai-district-osm.geojson").to_crs(crs).geometry.iloc[0],
    }
    products: dict[str, Path] = {}
    summary: dict = {"scenarios": {}}
    for name, f in scen.items():
        products[f"{name}_index"] = f["index"]
        products[f"{name}_class"] = f["class"]
        products[f"{name}_completeness"] = f["completeness"]
        poly = outputs.class_polygons(f["class"], cfg.simplify_tolerance_m, cfg.sieve_min_cells, labels,
                                       cfg.polygon_smooth_cells)
        p = out / f"{name}_polygons.geojson"
        outputs.to_wgs84_geojson(poly, p)
        products[f"{name}_polygons"] = p
        rd = outputs.road_exposure(roads, f["index"], f["class"], n)
        hi = rd[rd["exp_class_max"] >= n]  # ways touching the highest relative class
        top2_km = float(rd.loc[rd["exp_class_max"] >= n - 1, "length_m"].sum()) / 1000
        p = out / f"{name}_roads_exposed.geojson"
        outputs.to_wgs84_geojson(hi[["osm_id", "name", "class", "exp_mean", "exp_max",
                                     "exp_class_max", "top_class_share", "length_m", "geometry"]],
                                 p, simplify_m=3.0)
        products[f"{name}_roads_exposed"] = p
        fac = []
        for kind, g in (("hospital_or_clinic", hosp), ("school", schools)):
            fe = outputs.facility_exposure(g, f["index"], f["class"], files["hand"],
                                           files["water_distance"])
            fe["facility_type"] = kind
            fac.append(fe[["osm_id", "name", "class", "facility_type", "exp_index", "exp_class",
                           "hand_m", "water_dist_m", "geometry"]])
        fac_gdf = gpd.GeoDataFrame(pd.concat(fac, ignore_index=True), crs=crs)
        p = out / f"{name}_facilities.geojson"
        outputs.to_wgs84_geojson(fac_gdf, p)
        products[f"{name}_facilities"] = p
        s = {"regions": {rn: outputs.region_summary(rg, f["class"], f["index"], f["completeness"], n)
                         for rn, rg in regions.items()}}
        s["road_length_km_touching_top_class"] = round(float(hi["length_m"].sum()) / 1000, 1)
        s["road_length_km_touching_top2_classes"] = round(top2_km, 1)
        s["road_length_km_all"] = round(float(rd["length_m"].sum()) / 1000, 1)
        s["facilities_in_top_class"] = {
            kind: int(((x["exp_class"] == n)).sum()) for kind, x in
            zip(("hospital_or_clinic", "school"), fac, strict=True)}
        s["facilities_total"] = {kind: len(x) for kind, x in
                                 zip(("hospital_or_clinic", "school"), fac, strict=True)}
        summary["scenarios"][name] = s
        print(f"{name:14s} top-class share (core) = "
              f"{s['regions']['chennai_core_geoboundaries']['share_by_class'][n]}")

    summary["class_thresholds_baseline_index"] = thr
    summary["normalisation"] = norm.to_dict()
    summary["config"] = json.loads(cfg.model_dump_json())
    summary["disclaimer"] = DISCLAIMER
    sp = out / "exposure_summary.json"
    sp.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    products["summary"] = sp
    products["water_distance"] = files["water_distance"]
    products["permanent_water_mask"] = exclude

    # -- manifest ------------------------------------------------------------------------------
    parents = [f"terrain-derivatives-{region}", f"osm-{region}-roads", f"osm-{region}-hospitals",
               f"osm-{region}-schools", f"osm-{region}-water", "chennai-district-boundary", "chennai-district-boundary-osm",
               f"copernicus-dem-{region}"]
    m = DatasetManifest(
        dataset_id=f"exposure-screening-{region}",
        dataset_name=f"FloodLens exposure screening products, {region.title()}",
        kind=DatasetKind.DERIVED, derived_from=parents,
        source_url="s3://copernicus-dem-30m/",
        provider="FloodLens (derived from Copernicus DEM, OpenStreetMap, geoBoundaries)",
        license="Mixed: Copernicus DEM licence; ODbL 1.0 (OSM, geoBoundaries ADM2) share-alike applies",
        attribution="Contains modified Copernicus DEM data; (c) OpenStreetMap contributors; geoBoundaries.",
        acquisition_timestamp=datetime.now(UTC),
        observation_period="terrain-based; hypothetical rainfall scenarios"
        + ("; includes IMERG observed sample" if rain_aligned else "; observed rainfall pending"),
        spatial_resolution=f"{cell:.0f} m raster; vectors simplified to {cfg.simplify_tolerance_m:g} m",
        coordinate_reference_system=f"{crs} (rasters); EPSG:4326 (GeoJSON)",
        processing_version="floodlens-geo exposure 0.1.0",
        geographical_coverage=f"{region.title()} AOI bbox {REGIONS[region]}",
        processing_status=ProcessingStatus.PROCESSED,
        limitations=[
            DISCLAIMER,
            "Classes are relative quantiles of the baseline index, not absolute risk levels",
            "Weights and transform scales are uncalibrated heuristics (see docs/METHODOLOGY.md)",
            "Surface-model terrain at 30 m cannot represent drains, culverts or embankments",
            "Rainfall scenarios are hypothetical depths; no hydraulic routing or drainage capacity",
            "Region boundaries: geoBoundaries core polygon is smaller than the OSM district polygon",
        ],
        checksum_sha256=sha256(sp),
    )
    doc = json.loads(m.model_dump_json())
    doc["file"] = sp.relative_to(ROOT).as_posix()
    doc["products"] = {k: {"file": p.relative_to(ROOT).as_posix(), "sha256": sha256(p)}
                       for k, p in products.items()}
    doc["config"] = summary["config"]
    (ROOT / "data" / "manifests" / f"exposure-screening-{region}.json").write_text(
        json.dumps(doc, indent=2), encoding="utf-8")
    sizes = sum(p.stat().st_size for p in products.values()) / 2**20
    print(f"done in {time.time() - t0:.1f}s; {len(products)} products, {sizes:.1f} MiB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="chennai", choices=list(REGIONS))
    ap.add_argument("--config", type=Path, help="JSON file overriding ExposureConfig fields")
    a = ap.parse_args()
    cfg = ExposureConfig.model_validate_json(a.config.read_text()) if a.config else ExposureConfig()
    main(a.region, cfg)
