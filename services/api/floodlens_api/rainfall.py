"""Observed-rainfall response logic (stdlib only; no geospatial imports so Lambda stays small).

The builder writes time-independent documents. Anything that depends on the clock (freshness) is
computed here per request, with an injectable ``now`` so it is deterministic in tests.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

LATE_RECENT_DAYS = 14
STREAM_LABELS = {"historical_final": "Historical Final Run", "recent_late": "Recent Late Run"}
STREAM_NOTES = {
    "historical_final": "IMERG Final Run (GPM_3IMERGDF): gauge-adjusted research-grade estimate.",
    "recent_late": "IMERG Late Run (GPM_3IMERGDL): near-real-time provisional estimate, no monthly "
                   "gauge adjustment; may be superseded by the Final Run.",
}


def now_utc() -> datetime:
    return datetime.now(UTC)


def freshness(stream: str, observation_end: date, now: datetime) -> dict:
    age = (now.date() - observation_end).days
    if stream == "historical_final":
        status = "historical_archive"
        note = "Historical observation period; not live or real-time."
    elif age <= LATE_RECENT_DAYS:
        status = "recent_provisional"
        note = (f"Observation ended {age} day(s) before this request. Provisional Late Run estimate; "
                "not live or real-time and not a forecast.")
    else:
        status = "aged_provisional"
        note = (f"Observation ended {age} day(s) before this request. Provisional Late Run estimate "
                "that may since have been superseded by the Final Run; not live or real-time.")
    return {"status": status, "as_of_utc": now, "observation_end": observation_end, "age_days": age,
            "is_live": False, "note": note}


def with_freshness(meta: dict, now: datetime) -> dict:
    end = date.fromisoformat(meta["observation_end"])
    return {**meta, "data_freshness": freshness(meta["rainfall_stream"], end, now)}


def utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    return (datetime.combine(start, time(0, 0, 0), UTC),
            datetime.combine(end, time(23, 59, 59), UTC))


def product_version_label(versions: list[str]) -> str:
    return versions[0] if len(versions) == 1 else "mixed: " + ", ".join(versions)


def overlaps(meta: dict, start: date | None, end: date | None) -> bool:
    s, e = date.fromisoformat(meta["observation_start"]), date.fromisoformat(meta["observation_end"])
    return not ((end is not None and s > end) or (start is not None and e < start))


def stream_availability(catalog: dict) -> list[dict]:
    out = []
    for s in catalog["streams"]:
        status = s["status"]
        note = STREAM_NOTES[s["stream"]]
        if status == "pending":
            note += " Candidate datasets exist but failed validation, so none is published."
        elif status == "unavailable":
            note += " No dataset has been published for this region."
        out.append({"stream": s["stream"], "label": STREAM_LABELS[s["stream"]],
                    "product_identifier": s["product_identifier"], "status": status,
                    "scenario_ids": s["scenario_ids"], "note": note})
    return out


def coverage(catalog: dict, metas: list[dict]) -> list[dict]:
    out = []
    for s in catalog["streams"]:
        periods = [{"scenario_id": m["scenario_id"], "observation_start": m["observation_start"],
                    "observation_end": m["observation_end"], "n_days": m["n_days"],
                    "product_version": product_version_label(m["product_versions"])}
                   for m in metas if m["rainfall_stream"] == s["stream"]]
        out.append({"stream": s["stream"], "status": s["status"], "periods": periods,
                    "earliest": min((p["observation_start"] for p in periods), default=None),
                    "latest": max((p["observation_end"] for p in periods), default=None)})
    return out


def scenario_catalog(region: str, scenarios: list[dict], metas: list[dict]) -> dict:
    out = []
    for s in scenarios:
        t = s["scenario_type"]
        group = "terrain_baseline" if t == "terrain_baseline" else "hypothetical_rainfall"
        label = ("Terrain baseline (no rainfall)" if t == "terrain_baseline"
                 else f"Hypothetical {s['rainfall_accumulation_mm']:g} mm accumulation (not data)")
        out.append({"scenario_id": s["scenario_id"], "scenario_type": t, "group": group,
                    "label": label, "has_exposure_outputs": True,
                    "rainfall_accumulation_mm": s.get("rainfall_accumulation_mm")})
    for m in metas:
        recent = m["rainfall_stream"] == "recent_late"
        out.append({"scenario_id": m["scenario_id"], "scenario_type": "observed_rainfall",
                    "group": "recent_observed" if recent else "historical_observed",
                    "label": m["label"], "has_exposure_outputs": False,
                    "rainfall_stream": m["rainfall_stream"],
                    "observation_start": m["observation_start"],
                    "observation_end": m["observation_end"]})
    return {"region_id": region, "scenarios": out}


def _dataset_row(m: dict, summ: dict) -> dict:
    acc = summ["accumulation_mm"]
    return {"scenario_id": m["scenario_id"], "rainfall_stream": m["rainfall_stream"],
            "label": m["label"], "observation_start": m["observation_start"],
            "observation_end": m["observation_end"], "n_days": m["n_days"],
            "product_version": m["product_version"], "mean_accumulation_mm": acc["mean_mm"],
            "max_accumulation_mm": acc["max_mm"],
            "mean_daily_rate_mm_per_day": summ["mean_daily_rate_mm_per_day"]}


def pairwise(a_meta: dict, b_meta: dict, a_cells: dict, b_cells: dict, a_sum: dict, b_sum: dict) -> dict:
    ga, gb = a_meta["spatial_coverage"], b_meta["spatial_coverage"]
    same_grid = (ga["bbox"] == gb["bbox"] and ga["cells_x"] == gb["cells_x"]
                 and ga["cells_y"] == gb["cells_y"])
    cells = []
    mean_diff = None
    if same_grid:
        bmap = {(f["properties"]["row"], f["properties"]["col"]): f["properties"]["accumulation_mm"]
                for f in b_cells["features"]}
        diffs = []
        for f in a_cells["features"]:
            p = f["properties"]
            av, bv = p["accumulation_mm"], bmap.get((p["row"], p["col"]))
            d = None if av is None or bv is None else round(bv - av, 4)
            if d is not None:
                diffs.append(d)
            cells.append({"row": p["row"], "col": p["col"], "a_mm": av, "b_mm": bv, "b_minus_a_mm": d})
        mean_diff = round(sum(diffs) / len(diffs), 3) if diffs else None
    ra, rb = a_sum["mean_daily_rate_mm_per_day"], b_sum["mean_daily_rate_mm_per_day"]
    same_duration = a_meta["n_days"] == b_meta["n_days"]
    caution = ("Differences are b minus a. "
               + ("" if same_duration else
                  f"The periods differ in length ({a_meta['n_days']} vs {b_meta['n_days']} days), so "
                  "accumulations are not like-for-like; compare the mean daily rates. ")
               + ("" if a_meta["rainfall_stream"] == b_meta["rainfall_stream"] else
                  "Different streams are different estimates (Final vs provisional Late). ")
               + "This is not a validation of anything.")
    return {"a": a_meta["scenario_id"], "b": b_meta["scenario_id"], "same_grid": same_grid,
            "same_duration": same_duration, "mean_difference_mm": mean_diff,
            "mean_daily_rate_difference_mm_per_day":
                None if ra is None or rb is None else round(rb - ra, 3),
            "cells": cells, "caution": caution.strip()}


def comparison(region: str, selected: list[dict], summaries: dict[str, dict],
               cells: dict[str, dict], scenarios: list[dict]) -> dict:
    hyp = [{"scenario_id": s["scenario_id"], "scenario_type": s["scenario_type"],
            "rainfall_accumulation_mm": s["rainfall_accumulation_mm"],
            "accumulation_period": s.get("accumulation_period")}
           for s in scenarios if s["scenario_type"] == "hypothetical_rainfall_accumulation"]
    pair = None
    if len(selected) == 2:
        a, b = selected
        pair = pairwise(a, b, cells[a["scenario_id"]], cells[b["scenario_id"]],
                        summaries[a["scenario_id"]], summaries[b["scenario_id"]])
    return {"region_id": region,
            "observed": [_dataset_row(m, summaries[m["scenario_id"]]) for m in selected],
            "hypothetical_reference": hyp, "pairwise": pair}
