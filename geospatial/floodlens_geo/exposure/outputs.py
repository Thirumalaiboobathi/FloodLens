"""Vector products and summaries derived from exposure rasters.

All geometry work happens in the raster's metric CRS; exports are simplified and reprojected to
EPSG:4326 with 6-decimal coordinates (~0.1 m) to keep files small.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from rasterio import features
from shapely.geometry import shape
from shapely.validation import make_valid


def _valid(g):
    return g if g.is_valid else make_valid(g)


def class_polygons(class_raster: Path, simplify_m: float, sieve_min_cells: int,
                   class_labels: dict[int, str], smooth_cells: int = 1) -> gpd.GeoDataFrame:
    """Polygons of relative exposure classes (0 = unscored is dropped), simplified, valid.

    The vector layer is *generalised*: an ordinal median filter (``smooth_cells`` wide) and a sieve
    remove single-cell speckle. The class raster itself is not modified.
    """
    from scipy.ndimage import median_filter

    with rasterio.open(class_raster) as r:
        cls = r.read(1)
        tr, crs = r.transform, r.crs
    if smooth_cells > 1:
        scored = cls > 0
        cls = np.where(scored, median_filter(cls, size=smooth_cells, mode="nearest"), 0).astype(
            "uint8")
    cls = features.sieve(cls, size=sieve_min_cells, connectivity=4)
    rows = []
    for geom, val in features.shapes(cls, mask=cls > 0, transform=tr, connectivity=4):
        g = shape(geom)
        if simplify_m > 0:
            g = g.simplify(simplify_m, preserve_topology=True)
        g = _valid(g)
        if g.is_empty:
            continue
        rows.append({"exposure_class": int(val), "label": class_labels[int(val)],
                     "area_km2": round(g.area / 1e6, 4), "geometry": g})
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)
    return gdf.to_crs(4326)


def _densified_samples(lines: gpd.GeoSeries, spacing_m: float):
    """Vertices of every line after segmentize, with the owning line's position."""
    seg = shapely.segmentize(lines.values, spacing_m)
    coords, idx = shapely.get_coordinates(seg, return_index=True)
    return coords, idx


def _sample(arr: np.ndarray, transform, coords: np.ndarray) -> np.ndarray:
    inv = ~transform
    x, y = coords[:, 0], coords[:, 1]
    cols = inv.a * x + inv.b * y + inv.c
    rows = inv.d * x + inv.e * y + inv.f
    rows = np.floor(rows).astype(int)
    cols = np.floor(cols).astype(int)
    ok = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0) & (cols < arr.shape[1])
    out = np.full(len(coords), np.nan)
    out[ok] = arr[rows[ok], cols[ok]]
    return out


def road_exposure(roads: gpd.GeoDataFrame, index_raster: Path, class_raster: Path,
                  n_classes: int, spacing_m: float = 30.0) -> gpd.GeoDataFrame:
    """Per-way exposure attributes sampled along each road at ``spacing_m`` (OSM ways, not split)."""
    with rasterio.open(index_raster) as ri, rasterio.open(class_raster) as rc:
        idx = ri.read(1, masked=True).astype("float64").filled(np.nan)
        cls = rc.read(1).astype("float64")
        tr, crs = ri.transform, ri.crs
    roads = roads.to_crs(crs).reset_index(drop=True)
    coords, owner = _densified_samples(roads.geometry, spacing_m)
    v = _sample(idx, tr, coords)
    c = _sample(cls, tr, coords)
    c[c == 0] = np.nan
    n = len(roads)
    valid = np.isfinite(v)
    cnt = np.bincount(owner[valid], minlength=n)
    ssum = np.bincount(owner[valid], weights=v[valid], minlength=n)
    vmax = np.full(n, -np.inf)
    np.maximum.at(vmax, owner[valid], v[valid])
    cmax = np.full(n, -np.inf)
    cv = np.isfinite(c)
    np.maximum.at(cmax, owner[cv], c[cv])
    top = np.bincount(owner[cv & (c >= n_classes)], minlength=n)
    with np.errstate(divide="ignore", invalid="ignore"):
        roads["exp_mean"] = np.where(cnt > 0, ssum / cnt, np.nan).round(4)
        roads["top_class_share"] = np.where(cnt > 0, top / cnt, np.nan).round(3)
    roads["exp_max"] = np.where(np.isfinite(vmax), vmax, np.nan).round(4)
    roads["exp_class_max"] = np.where(np.isfinite(cmax), cmax, 0).astype("uint8")
    roads["length_m"] = roads.geometry.length.round(1)
    roads["samples"] = cnt
    return roads


def facility_exposure(points: gpd.GeoDataFrame, index_raster: Path, class_raster: Path,
                      hand_raster: Path | None, water_dist_raster: Path | None) -> gpd.GeoDataFrame:
    """Terrain/exposure context at each facility point (no capacity or access information)."""
    with rasterio.open(index_raster) as ri:
        tr, crs = ri.transform, ri.crs
        idx = ri.read(1, masked=True).astype("float64").filled(np.nan)
    with rasterio.open(class_raster) as rc:
        cls = rc.read(1).astype("float64")
    pts = points.to_crs(crs).reset_index(drop=True)
    xy = shapely.get_coordinates(pts.geometry.values)
    pts["exp_index"] = _sample(idx, tr, xy).round(4)
    c = _sample(cls, tr, xy)
    pts["exp_class"] = np.where(np.isfinite(c), c, 0).astype("uint8")
    for name, path in (("hand_m", hand_raster), ("water_dist_m", water_dist_raster)):
        if path is not None:
            with rasterio.open(path) as r:
                a = r.read(1, masked=True).astype("float64").filled(np.nan)
            pts[name] = _sample(a, tr, xy).round(1)
    return pts


def to_wgs84_geojson(gdf: gpd.GeoDataFrame, path: Path, simplify_m: float = 0.0,
                     drop: tuple[str, ...] = ("tags",)) -> None:
    g = gdf.drop(columns=[c for c in drop if c in gdf.columns])
    if simplify_m > 0:
        g = g.assign(geometry=g.geometry.simplify(simplify_m, preserve_topology=True))
    g = g.to_crs(4326)
    g["geometry"] = shapely.set_precision(g.geometry.values, 1e-6)
    path.parent.mkdir(parents=True, exist_ok=True)
    g.to_file(path, driver="GeoJSON")


def region_summary(region_geom_utm, class_raster: Path, index_raster: Path,
                   completeness_raster: Path, n_classes: int) -> dict:
    """Class area shares, mean index and completeness inside one region polygon (raster CRS)."""
    with rasterio.open(class_raster) as rc:
        cls = rc.read(1)
        tr = rc.transform
        mask = features.geometry_mask([region_geom_utm], cls.shape, tr, invert=True,
                                      all_touched=False)
        cell_km2 = abs(tr.a * tr.e) / 1e6
    with rasterio.open(index_raster) as ri:
        idx = ri.read(1, masked=True).astype("float64").filled(np.nan)
    with rasterio.open(completeness_raster) as rm:
        comp = rm.read(1, masked=True).astype("float64").filled(np.nan)
    scored = mask & (cls > 0)
    total = int(scored.sum())
    by_class = {int(k): round(float((scored & (cls == k)).sum() * cell_km2), 3)
                for k in range(1, n_classes + 1)}
    return {
        "region_area_km2": round(float(mask.sum() * cell_km2), 2),
        "scored_land_area_km2": round(total * cell_km2, 2),
        "area_km2_by_class": by_class,
        "share_by_class": {k: (round(v / (total * cell_km2), 4) if total else None)
                           for k, v in by_class.items()},
        "mean_index": round(float(np.nanmean(idx[scored])), 4) if total else None,
        "mean_completeness": round(float(np.nanmean(comp[scored])), 4) if total else None,
    }
