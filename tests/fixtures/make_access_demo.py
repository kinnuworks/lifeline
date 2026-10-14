r"""Write the synthetic run folder tests/fixtures/access_demo/ that reproduces the brief's sketch.

Layout (offsets in hundredths of a degree from 85.30 E, 28.00 N; about 1 km each):

            D (0,4)
            |  upper half not seen by radar (unobserved)
            |
 A ---------H--------[X bridge, destroyed]---- B (4,0)
 (-4,0)     |  \
            |   \\ footpath
            Y destroyed stretch \
            |                     \
            +------------------- C (4,-4)

H is the town with the district hospital. A road crossing near A tests intersection splitting,
and the H-D road starts 10 m off the hospital junction to test endpoint snapping.
B and C are cut off; A and D are not. Reopening bridge X reconnects B (1500 people) and ranks
above stretch Y, which reconnects C (400 people).
"""

import json
from pathlib import Path

LON0, LAT0 = 85.30, 28.00
OUT = Path(__file__).parent / "access_demo"


def ll(x, y):
    return [round(LON0 + x / 100.0, 6), round(LAT0 + y / 100.0, 6)]


def road(osm_id, pts, highway, hit, status, bridge=False, name=""):
    return {
        "type": "Feature",
        "properties": {"osm_id": osm_id, "highway": highway, "name": name, "bridge": bridge,
                       "hit_prob": hit, "status": status},
        "geometry": {"type": "LineString", "coordinates": [ll(*p) for p in pts]},
    }


def point(props, x, y):
    return {"type": "Feature", "properties": props, "geometry": {"type": "Point", "coordinates": ll(x, y)}}


def fc(feats):
    return {"type": "FeatureCollection", "features": feats}


def main():
    roads = [
        road(1, [(0, 0), (-2.5, 0), (-4, 0)], "primary", 0.02, "clear", name="Pasang Lhamu Highway"),
        road(2, [(-2, -1), (-2, 1)], "unclassified", 0.03, "clear"),          # crosses road 1, no shared vertex
        road(3, [(0.0102, 0.0), (0, 2)], "secondary", 0.05, "clear", name="Dhunche - Gatlang Road"),
        road(4, [(0, 2), (0, 4)], "secondary", None, "unobserved", name="Dhunche - Gatlang Road"),
        road(5, [(0, 0), (2, 0)], "primary", 0.04, "clear", name="Pasang Lhamu Highway"),
        road(6, [(2, 0), (2.2, 0)], "primary", 0.92, "destroyed_likely", bridge=True, name="Thade Khola"),
        road(7, [(2.2, 0), (4, 0)], "primary", 0.05, "clear", name="Pasang Lhamu Highway"),
        road(8, [(0, 0), (0, -2)], "tertiary", 0.03, "clear", name="Ramche Road"),
        road(9, [(0, -2), (0, -3)], "tertiary", 0.85, "destroyed_likely", name="Ramche Road"),
        road(10, [(0, -3), (2, -4), (4, -4)], "tertiary", 0.04, "clear", name="Ramche Road"),
        road(11, [(4, -4), (2, -2), (0.5, -0.5), (0, 0)], "path", 0.1, "clear"),
    ]
    places = [
        point({"osm_id": 100, "name": "Dhunche", "place": "town", "population": "5000"}, 0.05, 0.05),
        point({"osm_id": 101, "name": "A", "place": "village", "population": "800"}, -4, 0.2),
        point({"osm_id": 102, "name": "B", "place": "village", "population": "1500"}, 4, 0.2),
        point({"osm_id": 103, "name": "C", "place": "village", "population": "400"}, 4, -4.2),
        point({"osm_id": 104, "name": "D", "place": "village", "population": "600"}, 0.2, 4),
        point({"osm_id": 105, "name": "E", "place": "hamlet"}, -6, -6),     # far from any road
    ]
    health = [point({"osm_id": 200, "name": "District Hospital", "kind": "hospital"}, 0.02, 0.01)]
    aoi = fc([{"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[
        ll(-8, -8), ll(8, -8), ll(8, 8), ll(-8, 8), ll(-8, -8)]]}}])
    (OUT / "damage").mkdir(parents=True, exist_ok=True)
    (OUT / "osm").mkdir(parents=True, exist_ok=True)
    (OUT / "damage" / "roads.geojson").write_text(json.dumps(fc(roads), indent=1))
    (OUT / "osm" / "places.geojson").write_text(json.dumps(fc(places), indent=1))
    (OUT / "osm" / "health.geojson").write_text(json.dumps(fc(health), indent=1))
    (OUT / "aoi.geojson").write_text(json.dumps(aoi, indent=1))


if __name__ == "__main__":
    main()
