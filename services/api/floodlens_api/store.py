"""Read-only access to the prebuilt artifact tree (data/api/<region>/). Pure stdlib + json."""

from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path

from floodlens_api.schemas import Bbox

TILE_DEG_DEFAULT = 0.05
RAINFALL_ID = re.compile(r"^obs_(final|late)_\d{8}_\d{8}$")
_EMPTY_STREAMS = [
    {"stream": "historical_final", "label": "Historical Final Run",
     "product_identifier": "GPM_3IMERGDF", "status": "unavailable", "scenario_ids": [], "rejected": []},
    {"stream": "recent_late", "label": "Recent Late Run",
     "product_identifier": "GPM_3IMERGDL", "status": "unavailable", "scenario_ids": [], "rejected": []}]


class ArtifactError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def artifact_root() -> Path:
    env = os.environ.get("FLOODLENS_ARTIFACT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[3] / "data" / "api"


def parse_bbox(raw: str) -> Bbox:
    try:
        w, s, e, n = (float(x) for x in raw.split(","))
    except ValueError as exc:
        raise ArtifactError(422, "invalid_bbox",
                            "bbox must be 'west,south,east,north' in decimal degrees") from exc
    if not all(map(math.isfinite, (w, s, e, n))) or not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ArtifactError(422, "invalid_bbox", "bbox out of range or not west<east, south<north",
                            {"bbox": raw})
    return Bbox(west=w, south=s, east=e, north=n)


def _coords_bounds(c) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    stack = [c]
    while stack:
        cur = stack.pop()
        if isinstance(cur[0], (int, float)):
            xs.append(cur[0])
            ys.append(cur[1])
        else:
            stack.extend(cur)
    return min(xs), min(ys), max(xs), max(ys)


def intersects(feature: dict, b: Bbox) -> bool:
    """Feature bounding box vs query bbox (cheap prefilter; may include near misses)."""
    w, s, e, n = _coords_bounds(feature["geometry"]["coordinates"])
    return not (e < b.west or w > b.east or n < b.south or s > b.north)


class Store:
    def __init__(self, root: Path):
        self.root = root

    @lru_cache(maxsize=64)  # noqa: B019 - one Store per process
    def _json(self, rel: str):
        p = self.root / rel
        if not p.is_file():
            raise ArtifactError(404, "not_found", f"artifact not available: {rel}")
        return json.loads(p.read_text(encoding="utf-8"))

    def regions(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.name for p in self.root.iterdir() if (p / "index.json").is_file())

    def index(self, region: str) -> dict:
        if region not in self.regions():  # also blocks path traversal
            raise ArtifactError(404, "region_not_found", f"unknown region '{region}'",
                                {"available": self.regions()})
        return self._json(f"{region}/index.json")

    def scenario_ids(self, region: str) -> list[str]:
        return self.index(region)["scenarios"]

    def require_scenario(self, region: str, scenario: str) -> str:
        if scenario not in self.scenario_ids(region):
            raise ArtifactError(404, "scenario_not_found", f"unknown scenario '{scenario}'",
                                {"available": self.scenario_ids(region)})
        return scenario

    def require_boundary(self, region: str, boundary: str | None) -> str:
        bs = self.index(region)["boundaries"]
        if boundary is None:
            return bs[0]
        if boundary not in bs:
            raise ArtifactError(404, "boundary_not_found", f"unknown boundary '{boundary}'",
                                {"available": bs})
        return boundary

    def doc(self, region: str, name: str):
        self.index(region)
        return self._json(f"{region}/{name}")

    def road_features(self, region: str, scenario: str, bbox: Bbox, detail: str) -> list[dict]:
        tile = self.index(region).get("tile_deg", TILE_DEG_DEFAULT)
        seen: set[int] = set()
        out: list[dict] = []
        for ix in range(math.floor(bbox.west / tile), math.floor(bbox.east / tile) + 1):
            for iy in range(math.floor(bbox.south / tile), math.floor(bbox.north / tile) + 1):
                rel = f"{region}/roads/{scenario}/{detail}/{ix}_{iy}.json"
                if not (self.root / rel).is_file():
                    continue
                for f in self._json(rel)["features"]:
                    oid = f["properties"]["osm_id"]
                    if oid not in seen and intersects(f, bbox):
                        seen.add(oid)
                        out.append(f)
        return out

    # ---- observed rainfall (additive; artifacts built by scripts/build_api_artifacts.py) ----------

    def rainfall_catalog(self, region: str) -> dict:
        """Published rainfall index; a region built before rainfall existed has none published."""
        self.index(region)
        if not (self.root / region / "rainfall" / "catalog.json").is_file():
            return {"streams": _EMPTY_STREAMS, "scenario_ids": [], "rejected": []}
        return self._json(f"{region}/rainfall/catalog.json")

    def require_rainfall(self, region: str, scenario_id: str) -> str:
        """Only ids present in the published catalog resolve; callers never name a path."""
        ids = self.rainfall_catalog(region)["scenario_ids"]
        if not RAINFALL_ID.match(scenario_id) or scenario_id not in ids:
            raise ArtifactError(404, "rainfall_not_found",
                                f"no published observed-rainfall scenario '{scenario_id}'",
                                {"available": ids})
        return scenario_id

    def rainfall_doc(self, region: str, scenario_id: str, name: str):
        self.require_rainfall(region, scenario_id)
        return self._json(f"{region}/rainfall/{scenario_id}/{name}.json")

    def rainfall_raster(self, region: str, scenario_id: str) -> Path:
        self.require_rainfall(region, scenario_id)
        p = (self.root / region / "rainfall" / scenario_id / "raster.tif").resolve()
        if self.root.resolve() not in p.parents or not p.is_file():
            raise ArtifactError(404, "not_found", "raster not available")
        return p

    # ---- water bodies and waterways (additive; built from the validated OSM water file) -----------

    def water_doc(self, region: str, name: str):
        """Published water artifact; a region built without validated water has none published."""
        self.index(region)
        if not (self.root / region / "water" / name).is_file():
            raise ArtifactError(404, "water_not_published",
                                "no validated water dataset is published for this region")
        return self._json(f"{region}/water/{name}")
