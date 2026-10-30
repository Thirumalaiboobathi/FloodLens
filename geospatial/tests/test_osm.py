"""OSM conversion tests on SYNTHETIC Overpass-shaped JSON (not real map data)."""

import pytest
import requests

from floodlens_geo.ingestion import osm


def ln(*pts):
    return [{"lat": la, "lon": lo} for lo, la in pts]


RING = ln((80, 13), (80.01, 13), (80.01, 13.01), (80, 13.01), (80, 13))


def test_build_query_bbox_order_and_union():
    q = osm.build_query('nwr["natural"="water"];way["waterway"="river"]', "geom", (80, 12, 81, 13))
    assert q.count("(12,80,13,81)") == 2  # Overpass wants south,west,north,east
    assert q.endswith("out geom tags;")


def test_roads_become_lines_and_water_ring_becomes_polygon():
    data = {"elements": [
        {"type": "way", "id": 1, "tags": {"highway": "primary", "name": "A"},
         "geometry": ln((80, 13), (80.1, 13.1))},
        {"type": "way", "id": 2, "tags": {"natural": "water"}, "geometry": RING},
        {"type": "way", "id": 3, "tags": {"highway": "x"}, "geometry": ln((80, 13))},  # degenerate
    ]}
    g = osm.to_geodataframe(data, "t")
    assert sorted(g.geom_type) == ["LineString", "Polygon"]
    assert g.crs.to_epsg() == 4326
    assert g.set_index("osm_id").loc[1, "class"] == "primary"


def test_center_points_and_multipolygon_relation():
    data = {"elements": [
        {"type": "way", "id": 5, "tags": {"amenity": "hospital"}, "center": {"lat": 13.0, "lon": 80.2}},
        {"type": "node", "id": 6, "tags": {"amenity": "school"}, "lat": 13.1, "lon": 80.1},
        {"type": "relation", "id": 7, "tags": {"natural": "water"},
         "members": [{"role": "outer", "geometry": RING}]},
    ]}
    g = osm.to_geodataframe(data, "t").set_index("osm_id")
    assert g.loc[5, "geometry"].geom_type == "Point"
    assert g.loc[6, "geometry"].x == 80.1
    assert g.loc[7, "geometry"].geom_type == "Polygon"


def test_empty_input_gives_empty_frame():
    assert osm.to_geodataframe({"elements": []}, "t").empty


def test_fetch_rotates_mirrors_and_raises(monkeypatch):
    monkeypatch.setattr(osm.time, "sleep", lambda _: None)

    class S:
        def __init__(self):
            self.urls = []

        def post(self, url, **kw):
            self.urls.append(url)
            raise requests.ConnectionError("x")

    s = S()
    with pytest.raises(osm.OverpassError, match="ConnectionError"):
        osm.fetch("q", s, retries=1)
    assert s.urls == list(osm.ENDPOINTS)


def test_multipolygon_relation_assembled_from_several_outer_ways():
    # one square split into two open ways: must be joined into a single polygon
    a = ln((80, 13), (80.01, 13), (80.01, 13.01))
    b = ln((80.01, 13.01), (80, 13.01), (80, 13))
    data = {"elements": [{"type": "relation", "id": 9, "tags": {"natural": "water"},
                          "members": [{"role": "outer", "geometry": a},
                                      {"role": "outer", "geometry": b}]}]}
    g = osm.to_geodataframe(data, "t")
    assert len(g) == 1 and g.geometry.iloc[0].geom_type == "Polygon"
    assert g.geometry.iloc[0].area == pytest.approx(0.01 * 0.01)
