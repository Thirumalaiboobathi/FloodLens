"""Build the read-only API artifact tree from processed exposure outputs (raw outputs untouched).

Usage: python scripts/build_api_artifacts.py --region chennai
Reads : data/processed/exposure/<region>/, data/manifests/*.json, data/processed/rainfall (if any)
Writes: data/api/<region>/ (JSON validated against services/api/floodlens_api/schemas.py)
Roads are re-encoded as 0.05-degree tiles at two levels of detail so clients and the API can serve
only the requested viewport: 'full' (all ways touching the top class, geometry as exported) and
'overview' (strongly top-class ways only, simplified ~12 m) - never the 9-15 MiB monolith.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from collections import defaultdict
from pathlib import Path

import rasterio
from pyproj import Transformer
from shapely.geometry import mapping as shp_mapping
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "geospatial"))

from floodlens_api import rainfall as R
from floodlens_api import rainfall_schemas as RS
from floodlens_api import schemas as S
from floodlens_api import water_schemas as WS
from floodlens_geo.rainfall.catalog import discover, stream_status
from floodlens_geo.rainfall.publish import publish_docs

TILE_DEG = 0.05
OVERVIEW_SIMPLIFY_DEG = 12 / 111_000
OVERVIEW_MIN_TOP_SHARE = 0.5
BOUNDARIES = {"chennai_core_geoboundaries": "geoBoundaries Chennai polygon (175 km2)",
              "chennai_district_osm": "OSM Chennai district polygon (441 km2 incl. water)"}
BBOX = {"chennai": (80.05, 12.85, 80.35, 13.25)}
LIMITATIONS = [
    "Surface model (DSM): buildings and vegetation distort HAND, slope and flow paths",
    "30 m cells; urban drains, culverts and embankments are not represented",
    "Weights and scales are uncalibrated heuristics; classes are relative ranks, not risk levels",
    ("Not validated against observed flood extents (no suitable open dataset confirmed; "
    "docs/VALIDATION.md)"),
    "Rainfall scenarios are hypothetical accumulations, not forecasts or flood depths",
]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def dump(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def scenario_ref(name: str, observed: dict | None) -> S.ScenarioRef:
    if name == "baseline":
        return S.ScenarioRef(scenario_id=name, scenario_type=S.ScenarioType.TERRAIN_BASELINE)
    if name.startswith("rain_"):
        mm = float(name[5:-2])
        return S.ScenarioRef(scenario_id=name,
                             scenario_type=S.ScenarioType.HYPOTHETICAL_RAINFALL,
                             rainfall_accumulation_mm=mm,
                             accumulation_period="24h hypothetical (analyst-chosen, not data)")
    if name == "observed_sample" and observed:
        return S.ScenarioRef(scenario_id=name, scenario_type=S.ScenarioType.OBSERVED_RAINFALL,
                             rainfall_accumulation_mm=observed.get("median_mm"),
                             accumulation_period=observed["period"],
                             rainfall_source=observed["dataset_id"])
    raise ValueError(f"cannot classify scenario {name!r}")


def round_coords(c, nd: int):
    return [round(v, nd) for v in c] if isinstance(c[0], (int, float)) else [round_coords(x, nd) for x in c]


def tiles_for(bounds: tuple[float, float, float, float]):
    w, s, e, n = bounds
    for ix in range(math.floor(w / TILE_DEG), math.floor(e / TILE_DEG) + 1):
        for iy in range(math.floor(s / TILE_DEG), math.floor(n / TILE_DEG) + 1):
            yield ix, iy


def layer_meta(region: str, p: Path, kind: S.LayerKind, scn: S.ScenarioRef | None,
               units: str, desc: str, root: Path, derived: list[str]) -> S.RasterLayerMetadata:
    with rasterio.open(p) as r:
        a = r.read(1, out_shape=(max(r.height // 4, 1), max(r.width // 4, 1)), masked=True)
        w, s, e, n = Transformer.from_crs(r.crs, 4326, always_xy=True).transform_bounds(*r.bounds)
        valid = a.compressed()
        return S.RasterLayerMetadata(
            layer_id=p.stem, region_id=region, kind=kind, scenario=scn,
            file=p.relative_to(root).as_posix(), crs=str(r.crs), width=r.width, height=r.height,
            resolution_m=r.res[0], bounds_native=tuple(r.bounds),
            bounds_wgs84=S.Bbox(west=w, south=s, east=e, north=n), dtype=r.dtypes[0],
            nodata=r.nodata, value_min=float(valid.min()) if valid.size else None,
            value_max=float(valid.max()) if valid.size else None, units=units,
            sha256=sha256(p), derived_from=derived, description=desc)


def observed_info(region: str, man_dir: Path | None = None) -> dict | None:
    """Real Final Run rainfall product, if one has been processed. Never synthesised.

    Matches the undated manifest name and the dated ones written by ingest_imerg.py
    (``imerg-v07-total-<region>-<start>_<end>.json``); the latest observation period wins. Late Run
    manifests (``imerg-v07-late-total-...``) never match and are not consumed in this phase.
    """
    man_dir = man_dir or ROOT / "data" / "manifests"
    found = []
    for man in [man_dir / f"imerg-v07-total-{region}.json",
                *sorted(man_dir.glob(f"imerg-v07-total-{region}-*.json"))]:
        if not man.exists():
            continue
        m = load(man)
        if m.get("processing_status") == "processed" and m.get("run_type", "final") == "final":
            found.append(m)
    if not found:
        return None
    m = max(found, key=lambda x: x.get("observation_period") or "")
    return {"dataset_id": m["dataset_id"], "period": m.get("observation_period") or "unknown",
            "median_mm": None}


def publish_water(region: str, out: Path) -> bool:
    """Publish mapped OSM water (bodies and waterways) from the validated processed file.

    The processed GeoJSON must match its manifest (SHA-256 and feature count); otherwise nothing is
    published and the API reports water as not published. Features are never invented or edited:
    polygons are water bodies, lines are waterways, and only identifying properties are kept.
    """
    mf = ROOT / "data" / "manifests" / f"osm-{region}-water.json"
    if not mf.is_file():
        print(f"water: no manifest for {region}; not published")
        return False
    m = load(mf)
    src = ROOT / m["file"]
    if not src.is_file() or sha256(src) != m["checksum_sha256"]:
        print("water: processed file missing or checksum mismatch; NOT published")
        return False
    feats = load(src)["features"]
    if len(feats) != m["feature_count"]:
        print("water: feature count differs from manifest; NOT published")
        return False
    out_feats = []
    for f in feats:
        p = f["properties"]
        gtype = f["geometry"]["type"]
        out_feats.append(WS.WaterFeature(
            geometry={"type": gtype, "coordinates": round_coords(f["geometry"]["coordinates"], 6)},
            properties={"osm_type": p["osm_type"], "osm_id": p["osm_id"], "name": p.get("name"),
                        "feature_type": "water_body" if gtype == "Polygon" else "waterway",
                        "water_class": p["class"]}).model_dump(mode="json"))
    out_feats.sort(key=lambda f: (f["properties"]["osm_type"], f["properties"]["osm_id"]))
    source = WS.WaterSource(
        dataset_id=m["dataset_id"], provider=m["provider"], source_url=m["source_url"], license=m["license"],
        attribution=m["attribution"], acquisition_timestamp=m["acquisition_timestamp"],
        snapshot=m["observation_period"], checksum_sha256=m["checksum_sha256"]).model_dump(mode="json")
    by_type: dict[str, int] = defaultdict(int)
    by_class: dict[str, int] = defaultdict(int)
    for f in out_feats:
        by_type[f["properties"]["feature_type"]] += 1
        by_class[f["properties"]["water_class"]] += 1
    prov = WS.WaterProvenance(
        region_id=region, source=source, geographical_coverage=m["geographical_coverage"],
        coordinate_reference_system=m["coordinate_reference_system"],
        processing_version=m["processing_version"], feature_count=len(out_feats),
        counts_by_type=dict(by_type), counts_by_class=dict(by_class),
        limitations=m["limitations"]).model_dump(mode="json")
    dump(out / "water" / "features.json", {"source": source, "features": out_feats})
    dump(out / "water" / "provenance.json", prov)
    print(f"water: published {len(out_feats)} features {dict(by_type)}")
    return True


def publish_rainfall(region: str, out: Path) -> list[str]:
    """Publish only validated IMERG datasets (floodlens_geo.rainfall.catalog.discover).

    Final and Late stay separate scenarios with their own documents and raster. A manifest that
    fails validation is reported and skipped; its mere existence never makes it available. Nothing
    here feeds the exposure outputs.
    """
    records, rejections = discover(ROOT / "data" / "manifests", ROOT, region)
    now = R.now_utc()
    rdir = out / "rainfall"
    for rec in records:
        docs = publish_docs(rec)
        # validate every published document against the additive contract before writing
        RS.ObservedRainfallMetadata.model_validate(R.with_freshness(docs["meta"], now))
        RS.RainfallSummary.model_validate(docs["summary"])
        RS.RainfallProvenance.model_validate(docs["provenance"])
        RS.RainfallCells.model_validate(docs["cells"])
        d = rdir / rec.scenario_id
        for name, doc in docs.items():
            dump(d / f"{name}.json", doc)
        src = ROOT / rec.processed_file
        shutil.copyfile(src, d / "raster.tif")
        if sha256(d / "raster.tif") != rec.processed_sha256:
            raise RuntimeError(f"raster copy for {rec.scenario_id} does not match its checksum")
    status = stream_status(records, rejections)
    dump(rdir / "catalog.json", {
        "streams": list(status.values()), "scenario_ids": [r.scenario_id for r in records],
        "rejected": [{"dataset_id": x.dataset_id, "code": x.code} for x in rejections]})
    for x in rejections:
        print(f"rainfall NOT published: {x.dataset_id}: {x.code}: {x.detail}")
    for rec in records:
        print(f"rainfall published: {rec.scenario_id} ({rec.rainfall_stream}, {rec.product_versions}, "
              f"raw {rec.raw_verification})")
    return [r.scenario_id for r in records]


def main(region: str) -> None:
    exp = ROOT / "data" / "processed" / "exposure" / region
    out = ROOT / "data" / "api" / region
    if out.exists():
        shutil.rmtree(out)
    summary = load(exp / "exposure_summary.json")
    names = list(summary["scenarios"])
    # "available" only if the exposure run actually contains an observed scenario
    observed = observed_info(region) if "observed_sample" in names else None
    refs = {n: scenario_ref(n, observed) for n in names}
    n_classes = summary["config"]["n_classes"]
    manifests = [load(p) for p in sorted((ROOT / "data" / "manifests").glob("*.json"))]

    # 1 + 6: summaries and comparison -----------------------------------------------------------
    summaries = []
    for n, sc in summary["scenarios"].items():
        for b, r in sc["regions"].items():
            summaries.append(S.RegionExposureSummary(
                region_id=region, boundary_id=b, scenario=refs[n], n_classes=n_classes,
                region_area_km2=r["region_area_km2"], scored_land_area_km2=r["scored_land_area_km2"],
                area_km2_by_class=r["area_km2_by_class"], share_by_class=r["share_by_class"],
                mean_index=r["mean_index"], mean_completeness=r["mean_completeness"],
                road_length_km_touching_top_class=sc["road_length_km_touching_top_class"],
                road_length_km_all=sc["road_length_km_all"],
                facilities_in_top_class=sc["facilities_in_top_class"],
                facilities_total=sc["facilities_total"]).model_dump(mode="json"))
    dump(out / "summaries.json", summaries)

    cmp_out = {}
    for b in BOUNDARIES:
        base_top = summary["scenarios"]["baseline"]["regions"][b]["share_by_class"][str(n_classes)]
        stats = []
        for n, sc in summary["scenarios"].items():
            r = sc["regions"][b]
            top = r["share_by_class"][str(n_classes)]
            stats.append(S.ScenarioStats(
                scenario=refs[n], share_by_class=r["share_by_class"], share_top_class=top,
                mean_index=r["mean_index"],
                road_length_km_touching_top_class=sc["road_length_km_touching_top_class"],
                facilities_in_top_class=sc["facilities_in_top_class"],
                delta_vs_baseline_share_top_class=None if n == "baseline" else round(top - base_top, 4)))
        cmp_out[b] = S.ScenarioComparison(
            region_id=region, boundary_id=b, baseline_scenario_id="baseline",
            class_thresholds_baseline_index=summary["class_thresholds_baseline_index"],
            scenarios=stats, observed_rainfall_status="available" if observed else "pending",
            notes=["Classes are fixed to baseline quantiles, so scenario shares show relative change.",
                   "Hypothetical scenarios use analyst-chosen rainfall accumulation, not data.",
                   "Rainfall accumulation is not flood depth.",
                   ("Scenarios under ~100 mm can score below baseline because the heuristic "
                   "'reach' narrows; compare with caution.")]).model_dump(mode="json")
    dump(out / "comparison.json", cmp_out)
    dump(out / "scenarios.json", S.ScenarioList(
        region_id=region, scenarios=list(refs.values()),
        observed_rainfall_status="available" if observed else "pending").model_dump(mode="json"))

    # 2: raster layers ----------------------------------------------------------------------------
    droot = ROOT / "data"
    derived = ["terrain-derivatives-chennai", "dem-chennai"]
    layers = []
    for n in names:
        for suffix, kind, units, desc in (
                ("index", S.LayerKind.INDEX, "unitless 0..1", "Relative exposure index"),
                ("class", S.LayerKind.CLASS, f"class 1..{n_classes}", "Relative exposure class"),
                ("completeness", S.LayerKind.COMPLETENESS, "unitless 0..1",
                 "Share of configured weight available per cell")):
            layers.append(layer_meta(region, exp / f"{n}_{suffix}.tif", kind, refs[n], units,
                                     f"{desc} ({n})", droot, derived).model_dump(mode="json"))
    layers.append(layer_meta(region, exp / "water_distance_m.tif", S.LayerKind.WATER_DISTANCE, None,
                             "m", "Distance to permanent/mapped water", droot,
                             ["dem-chennai", "osm-chennai-water"]).model_dump(mode="json"))
    layers.append(layer_meta(region, exp / "permanent_water_mask.tif", S.LayerKind.WATER_MASK, None,
                             "1 = excluded water/sea", "Cells excluded from land scoring", droot,
                             ["dem-chennai"]).model_dump(mode="json"))
    dump(out / "layers.json", layers)

    # 7: provenance ---------------------------------------------------------------------------------
    prov = []
    for m in manifests:
        d = {k: m.get(k) for k in S.DatasetProvenance.model_fields if k != "schema_version"}
        d["derived_from"] = m.get("derived_from") or []
        prov.append(d)
    dump(out / "provenance.json", S.ProvenanceList(datasets=prov).model_dump(mode="json"))

    # 8: confidence ---------------------------------------------------------------------------------
    cfg_w = summary["config"]["weights"]
    ids = {"hand": ["terrain-derivatives-chennai"], "twi": ["terrain-derivatives-chennai"],
           "slope": ["terrain-derivatives-chennai"], "flow_accumulation": ["terrain-derivatives-chennai"],
           "elevation": ["dem-chennai"], "water_distance": ["dem-chennai", "osm-chennai-water"],
           "rainfall": [observed["dataset_id"]] if observed else []}
    inputs = [S.InputLayerStatus(
        input=k, weight=w,
        status=("present" if k != "rainfall" else "present" if observed else "pending"),
        dataset_ids=ids[k],
        note=None if k != "rainfall" else
        ("Observed rainfall accumulation processed" if observed else
         "Observed IMERG rainfall pending; hypothetical scenarios use analyst-chosen values"))
        for k, w in cfg_w.items()]
    confidence = {}
    for b in BOUNDARIES:
        r = summary["scenarios"]["baseline"]["regions"][b]
        confidence[b] = S.DataConfidence(
            region_id=region, boundary_id=b, mean_completeness=r["mean_completeness"],
            scored_fraction_of_region=round(r["scored_land_area_km2"] / r["region_area_km2"], 4),
            min_completeness_to_score=summary["config"]["min_completeness"], inputs=inputs,
            validation_evidence=["docs/VALIDATION.md"],
            known_limitations=LIMITATIONS).model_dump(mode="json")
    dump(out / "confidence.json", confidence)

    # 3, 4, 5: vectors --------------------------------------------------------------------------------
    counts = {"polygons": 0, "facilities": 0, "road_tiles": 0}
    for n in names:
        ref = refs[n]
        pf = []
        for f in load(exp / f"{n}_polygons.geojson")["features"]:
            pf.append(S.PolygonFeature(geometry=f["geometry"], properties=f["properties"]))
        dump(out / "polygons" / f"{n}.json", S.ExposurePolygonCollection(
            region_id=region, scenario=ref, features=pf).model_dump(mode="json"))
        ff = []
        for f in load(exp / f"{n}_facilities.geojson")["features"]:
            p = dict(f["properties"])
            p["osm_class"] = p.pop("class")
            ff.append(S.FacilityFeature(geometry=f["geometry"], properties=p))
        dump(out / "facilities" / f"{n}.json", S.FacilityProximityCollection(
            region_id=region, scenario=ref, features=ff).model_dump(mode="json"))
        counts["polygons"] += len(pf)
        counts["facilities"] += len(ff)

        buckets: dict[tuple[str, int, int], list] = defaultdict(list)
        for f in load(exp / f"{n}_roads_exposed.geojson")["features"]:
            p = dict(f["properties"])
            p["osm_class"] = p.pop("class")
            geom = shape(f["geometry"])
            for detail in ("full", "overview"):
                g = geom
                if detail == "overview":
                    if p["top_class_share"] < OVERVIEW_MIN_TOP_SHARE:
                        continue
                    g = geom.simplify(OVERVIEW_SIMPLIFY_DEG, preserve_topology=False)
                    if g.is_empty:
                        continue
                feat = S.RoadFeature(
                    geometry={"type": g.geom_type,
                              "coordinates": round_coords(list(shp_mapping(g)["coordinates"]), 5)},
                    properties=p).model_dump(mode="json")
                for ix, iy in tiles_for(g.bounds):
                    buckets[(detail, ix, iy)].append(feat)
        for (detail, ix, iy), feats in buckets.items():
            dump(out / "roads" / n / detail / f"{ix}_{iy}.json", {"features": feats})
            counts["road_tiles"] += 1
    rain_ids = publish_rainfall(region, out)
    water_published = publish_water(region, out)
    dump(out / "index.json", {
        "schema_version": S.SCHEMA_VERSION, "region_id": region, "bbox": dict(zip(
            ("west", "south", "east", "north"), BBOX[region], strict=True)),
        "boundaries": list(BOUNDARIES), "scenarios": names, "tile_deg": TILE_DEG,
        "observed_rainfall_status": "available" if observed else "pending",
        "rainfall_scenarios": rain_ids, "water_status": "available" if water_published else "not_published"})
    total = sum(f.stat().st_size for f in out.rglob("*.json"))
    print(f"artifacts -> {out}  {counts}  total {total / 2**20:.1f} MiB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="chennai")
    main(ap.parse_args().region)
