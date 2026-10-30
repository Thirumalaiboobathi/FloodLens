"""Sensitivity analysis of the exposure index (in-memory, on a decimated grid).

Answers "how much do the relative classes depend on our heuristic choices?" It does NOT calibrate
or validate anything: with no observed flood extent, stability is the only thing we can measure.
"""

from __future__ import annotations

import numpy as np

from floodlens_geo.exposure import engine
from floodlens_geo.exposure.config import ExposureConfig, Transforms, Weights

TERRAIN_WEIGHTS = ("hand", "twi", "slope", "flow_accumulation", "elevation", "water_distance")


def _rank(a: np.ndarray) -> np.ndarray:
    return np.argsort(np.argsort(a)).astype("float64")


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3:
        return float("nan")
    return float(np.corrcoef(_rank(a[ok]), _rank(b[ok]))[0, 1])


def score_index(layers: dict[str, np.ndarray], cfg: ExposureConfig, land: np.ndarray
                ) -> np.ndarray:
    """Baseline (terrain-only) index for the given layers; cells outside ``land`` are NaN."""
    lay = {k: np.where(land, v, np.nan) for k, v in layers.items()}
    norm = engine.derive_normalisation(lay["twi"], lay["flow_accumulation"], cfg.transforms)
    sc = engine.indicator_scores(lay, cfg.transforms, norm)
    idx, _ = engine.combine(sc, cfg.weights, cfg.min_completeness)
    idx[~land] = np.nan
    return idx


def classes(index: np.ndarray, n: int) -> np.ndarray:
    """Quantile classes of this variant's own index (relative ranks, as in production)."""
    return engine.classify(index, engine.quantile_thresholds(index, n))


def compare(base_idx: np.ndarray, var_idx: np.ndarray, n: int,
            areas: dict[str, np.ndarray] | None = None) -> dict:
    cb, cv = classes(base_idx, n), classes(var_idx, n)
    ok = (cb > 0) & (cv > 0)
    out = {
        "spearman": round(spearman(base_idx, var_idx), 4),
        "class_unchanged": round(float((cb[ok] == cv[ok]).mean()), 4),
        "class_within_1": round(
            float((np.abs(cb[ok].astype(int) - cv[ok].astype(int)) <= 1).mean()), 4),
        "top_class_overlap_jaccard": round(float(
            ((cb == n) & (cv == n)).sum() / max(((cb == n) | (cv == n)).sum(), 1)), 4),
    }
    if areas:
        out["areas"] = {name: {"mean_class": round(float(cv[m & (cv > 0)].mean()), 3),
                               "top_two_share": round(float((cv[m & (cv > 0)] >= n - 1).mean()), 3)}
                        for name, m in areas.items()}
    return out


def _with_weight(cfg: ExposureConfig, key: str, value: float) -> ExposureConfig:
    w = cfg.weights.model_dump()
    w[key] = value
    return cfg.model_copy(update={"weights": Weights(**w)})


def _with_transform(cfg: ExposureConfig, key: str, value: float) -> ExposureConfig:
    t = cfg.transforms.model_dump()
    t[key] = value
    return cfg.model_copy(update={"transforms": Transforms(**t)})


def run_all(layers: dict[str, np.ndarray], land: np.ndarray, cfg: ExposureConfig,
            areas: dict[str, np.ndarray] | None = None) -> dict:
    """One-at-a-time experiments against the production configuration."""
    n = cfg.n_classes
    base = score_index(layers, cfg, land)
    result: dict = {"baseline": compare(base, base, n, areas), "leave_one_out": {},
                    "weight_x0.5": {}, "weight_x1.5": {}, "scale_x0.5": {}, "scale_x2": {},
                    "dem_vertical_shift": {}}
    weights = cfg.weights.as_dict()
    for k in TERRAIN_WEIGHTS:
        result["leave_one_out"][k] = compare(
            base, score_index(layers, _with_weight(cfg, k, 0.0), land), n, areas)
        for tag, f in (("weight_x0.5", 0.5), ("weight_x1.5", 1.5)):
            result[tag][k] = compare(
                base, score_index(layers, _with_weight(cfg, k, weights[k] * f), land), n, areas)
    for key, field in (("hand", "hand_scale_m"), ("elevation", "elevation_scale_m"),
                       ("water_distance", "water_distance_scale_m")):
        v = getattr(cfg.transforms, field)
        for tag, f in (("scale_x0.5", 0.5), ("scale_x2", 2.0)):
            result[tag][key] = compare(
                base, score_index(layers, _with_transform(cfg, field, v * f), land), n, areas)
    # Vertical-error proxy: Copernicus GLO-30 is a surface model with metre-level error.
    for tag, d in (("dem_plus1m", 1.0), ("dem_minus1m", -1.0)):
        shifted = dict(layers)
        shifted["hand"] = np.maximum(layers["hand"] + d, 0)
        shifted["elevation"] = np.maximum(layers["elevation"] + d, 0)
        result["dem_vertical_shift"][tag] = compare(base, score_index(shifted, cfg, land), n, areas)
    return result
