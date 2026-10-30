"""Copernicus DEM tile discovery and download (AWS Open Data, anonymous access).

GLO-30 (30 m) is preferred; GLO-90 (90 m) is used per tile only when GLO-30 is absent.
The resolution actually used is returned per tile so it can be recorded in manifests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore import UNSIGNED
from botocore.config import Config

BUCKET_REGION = "eu-central-1"
GLO30 = ("copernicus-dem-30m", "Copernicus_DSM_COG_10_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM")
GLO90 = ("copernicus-dem-90m", "Copernicus_DSM_COG_30_{ns}{lat:02d}_00_{ew}{lon:03d}_00_DEM")

BBox = tuple[float, float, float, float]  # west, south, east, north (EPSG:4326)


@dataclass(frozen=True)
class DemTile:
    name: str
    bucket: str
    key: str
    product: str  # "GLO-30" | "GLO-90"
    resolution_m: int


def _tile_name(template: str, lat: int, lon: int) -> str:
    return template.format(
        ns="N" if lat >= 0 else "S", lat=abs(lat), ew="E" if lon >= 0 else "W", lon=abs(lon)
    )


def candidate_cells(bbox: BBox) -> list[tuple[int, int]]:
    """1x1 degree cells (south-west corner) intersecting the bbox."""
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError(f"invalid bbox {bbox}")
    return [
        (lat, lon)
        for lat in range(math.floor(s), math.ceil(n))
        for lon in range(math.floor(w), math.ceil(e))
    ]


def _client():
    return boto3.client(
        "s3", region_name=BUCKET_REGION, config=Config(signature_version=UNSIGNED)
    )


def _exists(s3, bucket: str, key: str) -> bool:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=key, MaxKeys=1)
    return resp.get("KeyCount", 0) > 0


def discover_tiles(bbox: BBox, s3=None) -> list[DemTile]:
    """Tiles covering bbox: GLO-30 where it exists, else GLO-90, else omitted."""
    s3 = s3 or _client()
    tiles: list[DemTile] = []
    for lat, lon in candidate_cells(bbox):
        for (bucket, tmpl), product, res in ((GLO30, "GLO-30", 30), (GLO90, "GLO-90", 90)):
            name = _tile_name(tmpl, lat, lon)
            key = f"{name}/{name}.tif"
            if _exists(s3, bucket, key):
                tiles.append(DemTile(name, bucket, key, product, res))
                break
    return tiles


def download_tiles(tiles: list[DemTile], dest: Path, s3=None) -> list[Path]:
    s3 = s3 or _client()
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    for t in tiles:
        out = dest / f"{t.name}.tif"
        if not out.exists():
            s3.download_file(t.bucket, t.key, str(out))
        paths.append(out)
    return paths


def download_aux(tiles: list[DemTile], dest: Path, kind: str = "WBM", s3=None) -> list[Path]:
    """Copernicus auxiliary layer (WBM water body mask, EDM edit mask, HEM error, FLM fill).

    Only published for GLO-30 tiles here; GLO-90 fallback tiles are skipped and reported by omission.
    """
    s3 = s3 or _client()
    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for t in tiles:
        if t.product != "GLO-30":
            continue
        stem = t.name.removesuffix("_DEM")
        path = dest / f"{stem}_{kind}.tif"
        if not path.exists():
            s3.download_file(t.bucket, f"{t.name}/AUXFILES/{stem}_{kind}.tif", str(path))
        out.append(path)
    return out
