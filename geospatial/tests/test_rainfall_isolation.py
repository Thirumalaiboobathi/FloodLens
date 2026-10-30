"""Observed rainfall is a separate context layer: it must not reach the exposure engine or scores."""

from __future__ import annotations

import json
from pathlib import Path

from floodlens_geo.rainfall.catalog import discover
from floodlens_geo.rainfall.publish import publish_docs

ROOT = Path(__file__).resolve().parents[2]
EXPOSURE = ROOT / "geospatial" / "floodlens_geo" / "exposure"


def test_exposure_package_does_not_import_observed_rainfall_or_imerg():
    for p in EXPOSURE.glob("*.py"):
        text = p.read_text(encoding="utf-8").lower()
        assert "floodlens_geo.rainfall" not in text, p.name
        assert "ingestion.imerg" not in text and "ingestion import imerg" not in text, p.name


def test_exposure_scenarios_contain_no_observed_scenario():
    summary = ROOT / "data" / "processed" / "exposure" / "chennai" / "exposure_summary.json"
    if not summary.is_file():
        return
    names = list(json.loads(summary.read_text(encoding="utf-8"))["scenarios"])
    assert names == ["baseline", "rain_50mm", "rain_100mm", "rain_200mm", "rain_300mm"]


def test_published_documents_have_no_forbidden_fields_and_never_mix_streams():
    recs, rej = discover(ROOT / "data" / "manifests", ROOT, "chennai")
    if not recs:
        return
    assert not rej
    for rec in recs:
        docs = publish_docs(rec)
        text = json.dumps(docs).lower()
        assert "randomerror" not in text and "flood_depth" not in text
        meta = docs["meta"]
        assert meta["rainfall_stream"] == rec.rainfall_stream
        assert meta["product_identifier"] == ("GPM_3IMERGDF" if rec.run_type == "final" else "GPM_3IMERGDL")
        assert all(g["file"].startswith("3B-DAY-L." if rec.run_type == "late" else "3B-DAY.")
                   for g in docs["provenance"]["granules"])
    assert len({r.scenario_id for r in recs}) == len(recs)
