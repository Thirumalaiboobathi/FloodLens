"""OpenStreetMap extraction via the Overpass API and conversion to GeoDataFrames.

Data (c) OpenStreetMap contributors, ODbL 1.0. Queries are per feature class and bounded by a
bbox so memory stays small on a low-RAM machine. Overpass requires a descriptive User-Agent.
"""

from __future__ import annotations

import time

import geopandas as gpd
import requests
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
USER_AGENT = "FloodLens/0.1 (AWS Builder Center weekend challenge; flood screening research)"

HIGHWAY = (
    "motorway|trunk|primary|secondary|tertiary|unclassified|residential|"
    "motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
)
# class name -> (overpass selector, output mode)
QUERIES: dict[str, tuple[str, str]] = {
    "roads": (f'way["highway"~"^({HIGHWAY})$"]', "geom"),
    "hospitals": ('nwr["amenity"~"^(hospital|clinic)$"]', "center"),
    "schools": ('nwr["amenity"="school"]', "center"),
    "water": ('nwr["natural"="water"];way["waterway"~"^(river|canal|stream|drain)$"]', "geom"),
}


class OverpassError(Exception):
    pass


def build_query(selector: str, mode: str, bbox: tuple[float, float, float, float]) -> str:
    w, s, e, n = bbox
    box = f"({s},{w},{n},{e})"
    body = ";".join(f"{part}{box}" for part in selector.split(";"))
    return f"[out:json][timeout:180];({body};);out {mode} tags;"


def fetch(query: str, session: requests.Session | None = None, *, retries: int = 3) -> dict:
    """POST to Overpass, rotating mirrors and backing off on 429/504."""
    s = session or requests.Session()
    last = "no attempt"
    for attempt in range(retries * len(ENDPOINTS)):
        url = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            r = s.post(
                url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=240
            )
        except requests.RequestException as e:
            last = type(e).__name__
        else:
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    last = "invalid JSON"
            else:
                last = f"HTTP {r.status_code}"
        time.sleep(min(5 * (attempt + 1), 30))
    raise OverpassError(f"Overpass failed: {last}")


def _line(geom: list[dict]) -> LineString | None:
    pts = [(p["lon"], p["lat"]) for p in geom]
    return LineString(pts) if len(pts) >= 2 else None


def to_geodataframe(data: dict, kind: str) -> gpd.GeoDataFrame:
    """Convert Overpass JSON ('out geom'/'out center') to a GeoDataFrame in EPSG:4326."""
    rows = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        t = el["type"]
        geom = None
        if t == "node":
            geom = Point(el["lon"], el["lat"])
        elif t == "way" and "geometry" in el:
            ln = _line(el["geometry"])
            if ln is not None:
                closed = ln.is_ring and tags.get("natural") == "water"
                geom = Polygon(ln) if closed else ln
        if geom is None and "center" in el:
            geom = Point(el["center"]["lon"], el["center"]["lat"])
        if geom is None and t == "relation":
            outers = [
                _line(m["geometry"])
                for m in el.get("members", [])
                if m.get("role") == "outer" and "geometry" in m
            ]
            noded = unary_union([x for x in outers if x is not None])
            polys = list(polygonize([noded])) if not noded.is_empty else []
            geom = unary_union(polys) if polys else None
        if geom is None or geom.is_empty:
            continue
        rows.append({"osm_type": t, "osm_id": el["id"], "kind": kind, "tags": tags, "geometry": geom})
    if not rows:
        return gpd.GeoDataFrame({"geometry": []}, geometry="geometry", crs="EPSG:4326")
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    if rows:
        gdf["name"] = gdf["tags"].map(lambda d: d.get("name"))
        gdf["class"] = gdf["tags"].map(
            lambda d: d.get("highway") or d.get("amenity") or d.get("waterway") or d.get("natural")
        )
        gdf["tags"] = gdf["tags"].map(lambda d: str(d))
    return gdf
