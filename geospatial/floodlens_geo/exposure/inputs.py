"""Derived input layers for the exposure engine that are not terrain derivatives."""

from __future__ import annotations

import numpy as np
from rasterio import features
from scipy.ndimage import distance_transform_edt


def water_distance(water: np.ndarray, cell_m: float) -> np.ndarray:
    """Distance (m) from each cell to the nearest water cell; 0 on water. NaN if no water at all."""
    if not water.any():
        return np.full(water.shape, np.nan)
    return distance_transform_edt(~water, sampling=cell_m)


def rasterize_geometries(geoms, shape, transform) -> np.ndarray:
    """Boolean raster of geometries (already in the raster CRS); touched cells count."""
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros(shape, bool)
    return features.rasterize(((g, 1) for g in geoms), out_shape=shape, transform=transform,
                              fill=0, all_touched=True, dtype="uint8").astype(bool)
