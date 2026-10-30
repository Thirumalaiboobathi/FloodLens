"""observed_info() must find the dated Final Run manifests that ingest_imerg.py writes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location("build_api_artifacts", ROOT / "scripts" / "build_api_artifacts.py")
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def write(d: Path, name: str, **kw):
    doc = {"dataset_id": name, "processing_status": "processed", **kw}
    (d / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


def test_dated_final_manifest_is_discovered(tmp_path):
    write(tmp_path, "imerg-v07-total-chennai-2023-12-03_2023-12-05",
          observation_period="2023-12-03/2023-12-05 (UTC days)", run_type="final")
    got = build.observed_info("chennai", tmp_path)
    assert got["dataset_id"] == "imerg-v07-total-chennai-2023-12-03_2023-12-05"
    assert got["period"] == "2023-12-03/2023-12-05 (UTC days)"


def test_legacy_manifest_without_run_type_is_still_final(tmp_path):
    write(tmp_path, "imerg-v07-total-chennai-2023-12-03_2023-12-05", observation_period="p")
    assert build.observed_info("chennai", tmp_path)["dataset_id"].startswith("imerg-v07-total-chennai")


def test_undated_legacy_name_still_works(tmp_path):
    write(tmp_path, "imerg-v07-total-chennai", observation_period="p")
    assert build.observed_info("chennai", tmp_path)["dataset_id"] == "imerg-v07-total-chennai"


def test_latest_final_period_wins(tmp_path):
    write(tmp_path, "imerg-v07-total-chennai-2023-12-03_2023-12-05", observation_period="2023-12-03/2023-12-05")
    write(tmp_path, "imerg-v07-total-chennai-2024-01-01_2024-01-02", observation_period="2024-01-01/2024-01-02")
    assert build.observed_info("chennai", tmp_path)["period"] == "2024-01-01/2024-01-02"


def test_late_manifest_is_never_used_as_final(tmp_path):
    write(tmp_path, "imerg-v07-late-total-chennai-2026-10-22_2026-10-28",
          observation_period="2026-10-22/2026-10-28", run_type="late")
    assert build.observed_info("chennai", tmp_path) is None
    # even a mislabelled name cannot smuggle a Late manifest in
    write(tmp_path, "imerg-v07-total-chennai-2026-10-22_2026-10-28",
          observation_period="2026-10-22/2026-10-28", run_type="late")
    assert build.observed_info("chennai", tmp_path) is None


@pytest.mark.parametrize("status", ["downloaded", "planned"])
def test_unprocessed_or_other_region_is_not_available(tmp_path, status):
    write(tmp_path, "imerg-v07-total-chennai-2023-12-03_2023-12-05", processing_status=status)
    write(tmp_path, "imerg-v07-total-mumbai-2023-12-03_2023-12-05", observation_period="p")
    assert build.observed_info("chennai", tmp_path) is None


def test_real_repository_manifest_is_found():
    got = build.observed_info("chennai")
    if (ROOT / "data" / "manifests" / "imerg-v07-total-chennai-2023-12-03_2023-12-05.json").exists():
        assert got["dataset_id"] == "imerg-v07-total-chennai-2023-12-03_2023-12-05"
    else:
        assert got is None
