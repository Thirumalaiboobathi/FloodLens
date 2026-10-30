"""Raster validation: CRS, nodata and data sanity checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import rasterio


class RasterValidationError(ValueError):
    pass


@dataclass(frozen=True)
class RasterReport:
    path: str
    crs: str
    width: int
    height: int
    resolution: tuple[float, float]
    nodata: float | None
    valid_fraction: float
    minimum: float
    maximum: float
    warnings: list[str] = field(default_factory=list)


def validate_raster(
    path: Path | str,
    *,
    expected_epsg: int | None = None,
    min_valid_fraction: float = 0.01,
    sample_max_pixels: int = 4_000_000,
) -> RasterReport:
    """Validate a single-band raster; raises RasterValidationError on hard failures.

    Statistics are computed from an overview-decimated read so large rasters stay cheap.
    NaN/inf and the declared nodata value are both treated as invalid.
    """
    with rasterio.open(path) as src:
        if src.crs is None:
            raise RasterValidationError(f"{path}: missing CRS")
        epsg = src.crs.to_epsg()
        if expected_epsg is not None and epsg != expected_epsg:
            raise RasterValidationError(f"{path}: expected EPSG:{expected_epsg}, got {epsg}")
        if src.count < 1:
            raise RasterValidationError(f"{path}: no bands")
        scale = max(1, int(np.ceil(np.sqrt(src.width * src.height / sample_max_pixels))))
        out_shape = (max(1, src.height // scale), max(1, src.width // scale))
        data = src.read(1, out_shape=out_shape, masked=False).astype("float64")
        nodata = src.nodata
        valid = np.isfinite(data)
        if nodata is not None:
            valid &= data != nodata
        frac = float(valid.mean()) if data.size else 0.0
        if frac < min_valid_fraction:
            raise RasterValidationError(
                f"{path}: only {frac:.2%} valid pixels (min {min_valid_fraction:.2%})"
            )
        warnings = []
        if nodata is None:
            warnings.append("no nodata value declared")
        return RasterReport(
            path=str(path),
            crs=f"EPSG:{epsg}" if epsg else src.crs.to_string(),
            width=src.width,
            height=src.height,
            resolution=(abs(src.res[0]), abs(src.res[1])),
            nodata=nodata,
            valid_fraction=frac,
            minimum=float(data[valid].min()),
            maximum=float(data[valid].max()),
            warnings=warnings,
        )
