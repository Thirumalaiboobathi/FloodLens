"""Export frozen v1 JSON Schemas and real-data fixtures.

Usage: python scripts/export_schemas.py            # (re)write schemas/v1/*.schema.json + fixtures
       python scripts/export_schemas.py --check    # fail if committed schemas differ from models

Fixtures are small slices of the artifacts built from actual processed data
(scripts/build_api_artifacts.py); nothing is invented here.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from floodlens_api import rainfall as R
from floodlens_api import rainfall_schemas as RS
from floodlens_api import schemas as S
from floodlens_api import water_schemas as WS

OUT = ROOT / "schemas" / "v1"
ART = ROOT / "data" / "api" / "chennai"
VELACHERY = (80.205, 12.965, 80.240, 13.000)  # sampling box; fixture slice only
N_FEATURES = 5
FIXTURE_NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=UTC)  # fixed clock: fixtures must be stable
ALL_SCHEMAS = {**S.SCHEMAS, **RS.RAINFALL_SCHEMAS, **WS.WATER_SCHEMAS}


def j(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def in_box(geom: dict) -> bool:
    c = geom["coordinates"]
    while isinstance(c[0], list):
        c = c[0]
    return VELACHERY[0] <= c[0] <= VELACHERY[2] and VELACHERY[1] <= c[1] <= VELACHERY[3]


def sliced(col: dict) -> dict:
    col = dict(col)
    pick = [f for f in col["features"] if in_box(f["geometry"])][:N_FEATURES]
    col["features"], col["matched"] = pick, len(pick)
    col["bbox"] = dict(zip(("west", "south", "east", "north"), VELACHERY, strict=True))
    return col


def fixtures() -> dict[str, dict]:
    tile = next((ART / "roads" / "rain_200mm" / "full").glob("*.json"))
    roads = j(tile)["features"]
    roads = [f for f in roads if in_box(f["geometry"])][:N_FEATURES] or roads[:N_FEATURES]
    ref200 = next(s for s in j(ART / "scenarios.json")["scenarios"] if s["scenario_id"] == "rain_200mm")
    layers = [x for x in j(ART / "layers.json") if x["layer_id"] in ("baseline_class",)]
    sums = [x for x in j(ART / "summaries.json") if x["scenario"]["scenario_id"] == "rain_200mm"
            and x["boundary_id"] == "chennai_core_geoboundaries"]
    prov = j(ART / "provenance.json")
    prov["datasets"] = prov["datasets"][:3]
    return {
        "regional_exposure_summary": sums[0],
        "raster_layer_metadata": layers[0],
        "exposure_polygons": sliced(j(ART / "polygons" / "rain_200mm.json")),
        "road_exposure": {"schema_version": S.SCHEMA_VERSION, "region_id": "chennai",
                          "scenario": ref200, "disclaimer": S.DISCLAIMER, "detail": "full",
                          "matched": len(roads), "selection": "ways_touching_top_class",
                          "features": roads},
        "facility_proximity": sliced(j(ART / "facilities" / "rain_200mm.json")),
        "scenario_comparison": j(ART / "comparison.json")["chennai_core_geoboundaries"],
        "dataset_provenance": prov,
        "data_confidence": j(ART / "confidence.json")["chennai_core_geoboundaries"],
    }


def rainfall_fixtures() -> dict[str, dict]:
    """Real published rainfall documents (recent Late Run); freshness uses FIXTURE_NOW."""
    rdir = ART / "rainfall"
    cat = j(rdir / "catalog.json")
    sid = next(i for i in cat["scenario_ids"] if i.startswith("obs_late_"))
    meta = R.with_freshness(j(rdir / sid / "meta.json"), FIXTURE_NOW)
    metas = [j(rdir / i / "meta.json") for i in cat["scenario_ids"]]
    comp_ids = cat["scenario_ids"][:2]
    scn = j(ART / "scenarios.json")["scenarios"]
    cells = {i: j(rdir / i / "cells.json") for i in comp_ids}
    summ = {i: j(rdir / i / "summary.json") for i in comp_ids}
    return {
        "observed_rainfall_metadata": meta,
        "rainfall_catalog": {
            "region_id": "chennai", "streams": R.stream_availability(cat), "filters": {
                "stream": None, "start": None, "end": None},
            "datasets": [R.with_freshness(m, FIXTURE_NOW) for m in metas]},
        "rainfall_summary": j(rdir / sid / "summary.json"),
        "rainfall_provenance": j(rdir / sid / "provenance.json"),
        "rainfall_cells": j(rdir / sid / "cells.json"),
        "rainfall_coverage": {"region_id": "chennai", "streams": R.coverage(cat, metas)},
        "rainfall_freshness": {
            "region_id": "chennai", "as_of_utc": FIXTURE_NOW, "datasets": [
                {"scenario_id": m["scenario_id"], "rainfall_stream": m["rainfall_stream"],
                 "retrieval_timestamp": m["retrieval_timestamp"],
                 "data_freshness": R.freshness(m["rainfall_stream"],
                                               datetime.fromisoformat(m["observation_end"]).date(),
                                               FIXTURE_NOW)} for m in metas]},
        "rainfall_comparison": R.comparison(
            "chennai", [j(rdir / i / "meta.json") for i in comp_ids], summ, cells, scn),
        "scenario_catalog": {"schema_version": RS.RAINFALL_SCHEMA_VERSION,
                             **R.scenario_catalog("chennai", scn, metas)},
    }


def water_fixtures() -> dict[str, dict]:
    """Real published water features: a small page over the Adyar area, plus the provenance doc."""
    wdir = ART / "water"
    doc = j(wdir / "features.json")
    feats = [f for f in doc["features"] if f["properties"]["feature_type"] == "waterway"][:3]
    feats += [f for f in doc["features"] if f["properties"]["feature_type"] == "water_body"][:2]
    return {
        "water_features": {
            "region_id": "chennai", "bbox": {"west": 80.05, "south": 12.85, "east": 80.35, "north": 13.25},
            "feature_type": None, "water_class": None, "limit": 5, "offset": 0,
            "matched": len(doc["features"]), "returned": len(feats), "truncated": True,
            "next_offset": len(feats), "source": doc["source"], "features": feats},
        "water_provenance": j(wdir / "provenance.json"),
    }


def main(check: bool) -> int:
    bad = 0
    for name, model in ALL_SCHEMAS.items():
        text = json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        p = OUT / f"{name}.schema.json"
        if check:
            if not p.exists() or p.read_text(encoding="utf-8") != text:
                print(f"SCHEMA DRIFT: {p.name}")
                bad += 1
            continue
        OUT.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if check:
        print("schemas match models" if not bad else f"{bad} schema(s) differ")
        return 1 if bad else 0
    for name, doc in {**fixtures(), **rainfall_fixtures(), **water_fixtures()}.items():
        doc = json.loads(json.dumps(doc, default=str))  # dates/datetimes -> ISO text
        model = ALL_SCHEMAS[name].model_validate(doc)  # fixtures must satisfy the model they exemplify
        if name in RS.RAINFALL_SCHEMAS or name in WS.WATER_SCHEMAS:  # serialise as the API does, model defaults included
            doc = model.model_dump(mode="json")
        fp = OUT / "fixtures" / f"{name}.example.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(ALL_SCHEMAS)} schemas and fixtures to {OUT}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    sys.exit(main(ap.parse_args().check))
