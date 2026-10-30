"""Flood Exposure Screening Index (ESI) engine.

A cell's terrain susceptibility is the weight-renormalised mean of monotone 0..1 indicator scores,
amplified by a rainfall forcing term (see ``combine``). Missing inputs
(whole layer absent, or nodata in that cell) are dropped and the remaining weights renormalised;
the share of configured weight that was present is reported as per-cell *completeness*. Cells
below ``min_completeness`` stay nodata. Layers are processed in row blocks to bound memory.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.windows import Window

from floodlens_geo.exposure.config import INPUTS, ExposureConfig, Transforms, Weights

OUT_NODATA = -9999.0
BLOCK_ROWS = 256
TERRAIN_INPUTS = ("hand", "twi", "slope", "flow_accumulation", "elevation")


class GridError(ValueError):
    """Inputs cannot be used together (missing CRS, no overlap, different grids)."""


# ---- indicator transforms ---------------------------------------------------------------


def reach_factor(rainfall_mm: float | None, t: Transforms) -> float:
    """Heuristic widening of the 'reach' of water with rainfall depth; 1.0 at the reference depth."""
    if rainfall_mm is None or rainfall_mm <= 0:
        return 1.0
    return float((rainfall_mm / t.reference_rainfall_mm) ** t.reach_exponent)


def robust_bounds(values: np.ndarray, pct: tuple[float, float]) -> tuple[float, float]:
    v = values[np.isfinite(values)]
    if v.size == 0:
        raise GridError("no valid cells to derive normalisation bounds")
    lo, hi = np.percentile(v, pct)
    if hi <= lo:  # constant layer: avoid a divide-by-zero
        return float(lo), float(lo) + 1.0
    return float(lo), float(hi)


def minmax01(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def _log10_positive(sca: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sca > 0, np.log10(sca), np.nan)


@dataclass(frozen=True)
class Normalisation:
    """Data-derived bounds, stored with outputs so a run is reproducible."""

    twi: tuple[float, float]
    log10_sca: tuple[float, float]

    def to_dict(self) -> dict:
        return {"twi": list(self.twi), "log10_sca": list(self.log10_sca)}


def derive_normalisation(twi: np.ndarray, sca: np.ndarray, t: Transforms) -> Normalisation:
    return Normalisation(robust_bounds(twi, t.robust_percentiles),
                         robust_bounds(_log10_positive(sca), t.robust_percentiles))


def indicator_scores(layers: dict[str, np.ndarray | None], t: Transforms, norm: Normalisation,
                     rainfall_mm: float | np.ndarray | None = None,
                     reach_mm: float | None = None) -> dict[str, np.ndarray | None]:
    """Map raw indicators to 0..1 exposure scores (1 = more exposed). None = layer not supplied."""
    k = reach_factor(reach_mm, t)
    out: dict[str, np.ndarray | None] = dict.fromkeys(INPUTS)
    hand, twi, slope = layers.get("hand"), layers.get("twi"), layers.get("slope")
    sca, elev = layers.get("flow_accumulation"), layers.get("elevation")
    wd = layers.get("water_distance")
    if hand is not None:
        out["hand"] = np.exp(-np.maximum(hand, 0) / (t.hand_scale_m * k))
    if twi is not None:
        out["twi"] = minmax01(twi, *norm.twi)
    if slope is not None:
        out["slope"] = 1.0 - np.minimum(np.maximum(slope, 0), t.slope_max_deg) / t.slope_max_deg
    if sca is not None:
        out["flow_accumulation"] = minmax01(_log10_positive(sca), *norm.log10_sca)
    if elev is not None:
        out["elevation"] = np.exp(-np.maximum(elev, 0) / t.elevation_scale_m)
    if wd is not None:
        out["water_distance"] = np.exp(-np.maximum(wd, 0) / (t.water_distance_scale_m * k))
    if rainfall_mm is not None:
        r = np.asarray(rainfall_mm, dtype="float64")
        shape = next((a.shape for a in out.values() if a is not None), r.shape)
        score = 1.0 - np.exp(-np.maximum(r, 0) / t.rainfall_scale_mm)
        out["rainfall"] = np.broadcast_to(score, shape).copy()
    return out


def combine(scores: dict[str, np.ndarray | None], w: Weights, min_completeness: float
            ) -> tuple[np.ndarray, np.ndarray]:
    """Terrain susceptibility amplified by rainfall. Returns (index, completeness); NaN = unscored.

    S = weighted mean of the available terrain/hydrology scores (weights renormalised per cell).
    With rainfall forcing f in [0, 1] and rainfall share rho = w_rain / sum(w):
        index = S + (1 - S) * rho * f
    so rainfall never lowers a score, cannot exceed 1, and has least effect where S is already 1.
    completeness = share of the configured weight that was actually available in the cell,
    counting the rainfall weight only when a rainfall input exists.
    """
    weights = {k: v for k, v in w.as_dict().items() if v > 0}
    total = sum(weights.values())
    terrain = {k: v for k, v in weights.items() if k != "rainfall"}
    shape = next((a.shape for a in scores.values() if a is not None), None)
    if shape is None:
        raise GridError("no input layers supplied")
    num = np.zeros(shape)
    present = np.zeros(shape)
    for name, wt in terrain.items():
        s = scores.get(name)
        if s is None:
            continue
        ok = np.isfinite(s)
        num += np.where(ok, s * wt, 0.0)
        present += np.where(ok, wt, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        susceptibility = np.where(present > 0, num / present, np.nan)
    index = susceptibility.copy()
    f = scores.get("rainfall")
    if f is not None and "rainfall" in weights:
        ok = np.isfinite(f) & np.isfinite(susceptibility)
        rho = weights["rainfall"] / total
        index = np.where(ok, susceptibility + (1 - susceptibility) * rho * np.where(ok, f, 0.0),
                         susceptibility)
        present = present + np.where(np.isfinite(f), weights["rainfall"], 0.0)
    completeness = present / total
    index[completeness < min_completeness] = np.nan
    return index, completeness


def explain(scores: dict[str, np.ndarray | None], w: Weights, row: int, col: int) -> dict:
    """Per-input contribution to one cell's index; contributions sum to the index (UI/API)."""
    weights = {k: v for k, v in w.as_dict().items() if v > 0}
    terrain = {k: v for k, v in weights.items() if k != "rainfall"
               and scores.get(k) is not None and np.isfinite(scores[k][row, col])}
    tot = sum(terrain.values())
    if not tot:
        return {}
    f = scores.get("rainfall")
    f_val = float(f[row, col]) if f is not None and np.isfinite(f[row, col]) else 0.0
    rho = weights.get("rainfall", 0.0) / sum(weights.values())
    boost = rho * f_val
    out = {k: {"score": float(scores[k][row, col]),
               "contribution": float(scores[k][row, col]) * wt / tot * (1 - boost)}
           for k, wt in terrain.items()}
    if f is not None and f_val > 0:
        out["rainfall"] = {"score": f_val, "contribution": boost}
    return out


# ---- grid handling and windowed IO ----------------------------------------------------------


def align_to(src_path: Path, ref_path: Path, resampling: Resampling = Resampling.bilinear
             ) -> np.ndarray:
    """Read src warped onto the reference grid. Different CRS/resolution are handled here."""
    with rasterio.open(ref_path) as ref, rasterio.open(src_path) as src:
        if src.crs is None or ref.crs is None:
            raise GridError(f"{src_path.name}: missing CRS, refusing to guess")
        out = np.full((ref.height, ref.width), np.nan, dtype="float32")
        reproject(rasterio.band(src, 1), out, src_nodata=src.nodata, dst_nodata=np.nan,
                  dst_transform=ref.transform, dst_crs=ref.crs, resampling=resampling)
        if not np.isfinite(out).any():
            raise GridError(f"{src_path.name} does not overlap the reference grid")
        return out


def same_grid(a: Path, b: Path) -> bool:
    with rasterio.open(a) as x, rasterio.open(b) as y:
        return (x.crs == y.crs and x.transform == y.transform
                and (x.height, x.width) == (y.height, y.width))


def windows(height: int, width: int, rows: int = BLOCK_ROWS) -> Iterator[Window]:
    for r0 in range(0, height, rows):
        yield Window(0, r0, width, min(rows, height - r0))


def read_masked(path: Path, window: Window | None = None) -> np.ndarray:
    with rasterio.open(path) as r:
        return r.read(1, window=window, masked=True).astype("float64").filled(np.nan)


def read_decimated(path: Path, step: int = 4) -> np.ndarray:
    with rasterio.open(path) as r:
        shape = (max(r.height // step, 1), max(r.width // step, 1))
        a = r.read(1, out_shape=shape, masked=True, resampling=Resampling.nearest)
    return a.astype("float64").filled(np.nan)


def quantile_thresholds(index_sample: np.ndarray, n_classes: int) -> list[float]:
    """Upper edges of relative classes: quantiles of the baseline index over scored cells."""
    v = index_sample[np.isfinite(index_sample)]
    if v.size == 0:
        raise GridError("no scored cells")
    return [float(x) for x in np.quantile(v, np.arange(1, n_classes) / n_classes)]


def classify(index: np.ndarray, thresholds: list[float]) -> np.ndarray:
    """Class 1..n (n = most exposed) using baseline thresholds; 0 where the index is missing."""
    cls = np.digitize(np.nan_to_num(index, nan=-1.0), thresholds, right=True) + 1
    return np.where(np.isfinite(index), cls, 0).astype("uint8")


def _open_like(path: Path, ref_path: Path, dtype: str, nodata: float | None):
    with rasterio.open(ref_path) as ref:
        prof = ref.profile
    for key in ("blockxsize", "blockysize", "tiled"):
        prof.pop(key, None)
    prof.update(dtype=dtype, count=1, nodata=nodata, compress="deflate")
    path.parent.mkdir(parents=True, exist_ok=True)
    return rasterio.open(path, "w", **prof)


def run_scenario(name: str, files: dict[str, Path | None], ref_path: Path, out_dir: Path,
                 cfg: ExposureConfig, norm: Normalisation, rainfall: Path | float | None,
                 reach_mm: float | None, thresholds: list[float] | None,
                 exclude: Path | None = None) -> tuple[Path, Path | None, Path]:
    """Score one scenario block by block. Returns (index_tif, class_tif or None, completeness_tif)."""
    idx_p, cls_p, cmp_p = (out_dir / f"{name}_{s}.tif" for s in ("index", "class", "completeness"))
    with rasterio.open(ref_path) as ref:
        h, w = ref.height, ref.width
    di = _open_like(idx_p, ref_path, "float32", OUT_NODATA)
    dc = _open_like(cmp_p, ref_path, "float32", OUT_NODATA)
    dcls = _open_like(cls_p, ref_path, "uint8", 0) if thresholds else None
    try:
        for win in windows(h, w):
            layers = {k: (read_masked(p, win) if p is not None else None)
                      for k, p in files.items()}
            rain = read_masked(rainfall, win) if isinstance(rainfall, Path) else rainfall
            sc = indicator_scores(layers, cfg.transforms, norm, rain, reach_mm)
            index, comp = combine(sc, cfg.weights, cfg.min_completeness)
            # cells with no terrain at all (e.g. sea) are not scored, whatever the rainfall
            terrain_ok = np.zeros(index.shape, bool)
            for k in TERRAIN_INPUTS:
                if sc.get(k) is not None:
                    terrain_ok |= np.isfinite(sc[k])
            if exclude is not None:  # e.g. permanent water: not land exposure
                terrain_ok &= ~(read_masked(exclude, win) == 1)
            index[~terrain_ok] = np.nan
            comp[~terrain_ok] = np.nan
            di.write(np.where(np.isfinite(index), index, OUT_NODATA).astype("float32"), 1,
                     window=win)
            dc.write(np.where(np.isfinite(comp), comp, OUT_NODATA).astype("float32"), 1,
                     window=win)
            if dcls is not None:
                dcls.write(classify(index, thresholds), 1, window=win)
    finally:
        di.close()
        dc.close()
        if dcls is not None:
            dcls.close()
    return idx_p, (cls_p if thresholds else None), cmp_p
