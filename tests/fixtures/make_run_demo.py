"""Write a small synthetic run folder that follows docs/CONTRACTS.md.

All values are invented for tests. Usage: python tests/fixtures/make_run_demo.py [out_dir]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

SETTLEMENTS = [
    # name, name_ne, lon, lat, place, population, p_cutoff, nearest_health, min_before, min_after, walk_after
    ("Dhunche", "धुन्चे", 85.296, 28.107, "town", 6200, 0.05, "Rasuwa District Hospital", 6, 6, 20),
    ("Syafrubesi", "स्याफ्रुबेसी", 85.336, 28.160, "village", 2100, 0.22, "Rasuwa District Hospital", 35, 41, 160),
    ("Thuman", "थुमन", 85.335, 28.210, "village", 712, 0.81, "Timure Health Post", 25, None, 140),
    ("Gatlang", "गत्लाङ", 85.284, 28.179, "village", 1840, 0.74, "Rasuwa District Hospital", 40, None, 210),
    ("Bridim", "ब्रिदिम", 85.370, 28.190, "village", 640, 0.66, "Timure Health Post", 30, None, 125),
    ("Haku", "हाकु", 85.255, 28.090, "village", 1215, 0.93, "Rasuwa District Hospital", 45, None, 260),
    ("Ramche", "राम्चे", 85.238, 28.020, "village", None, 0.12, "Kalikasthan Health Post", 20, 22, 95),
    ("Betrawati", "बेत्रावती", 85.185, 27.970, "village", 3000, 0.03, "Kalikasthan Health Post", 15, 15, 70),
    ("Kalikasthan", "कालिकास्थान", 85.200, 27.990, "village", 1450, 0.08, "Kalikasthan Health Post", 3, 3, 10),
    ("Mailung", "माइलुङ", 85.210, 28.050, "hamlet", 388, 0.58, "Kalikasthan Health Post", 18, None, 85),
]

BRIDGES = [
    ("Mailung Khola Bridge", "destroyed_likely", 0.91),
    ("Bhote Koshi Bridge", "destroyed_likely", 0.84),
    ("Trishuli Bridge", "damaged_possible", 0.55),
    ("Dhunche Bridge", "clear", 0.08),
    ("Gatlang Bridge", "clear", 0.12),
]

ROADS = [
    # name, highway, length_m, status
    ("Pasang Lhamu Highway", "primary", 4200, "destroyed_likely"),
    ("Pasang Lhamu Highway", "primary", 6100, "damaged_possible"),
    ("Pasang Lhamu Highway", "primary", 12800, "clear"),
    ("Gatlang Road", "tertiary", 2300, "destroyed_likely"),
    ("Gatlang Road", "tertiary", 3900, "clear"),
    ("Thuman Road", "track", 1700, "damaged_possible"),
    ("Thuman Road", "track", 5100, "unobserved"),
    ("Haku Road", "track", 2600, "destroyed_likely"),
]

BUILDING_STATUS = ["destroyed_likely"] * 6 + ["damaged_possible"] * 9 + ["clear"] * 22 + ["unobserved"] * 3


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def pt(lon, lat, props):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props}


def line(lon, lat, props, d=0.01):
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[lon, lat], [lon + d, lat + d]]},
            "properties": props}


def write(out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for sub in ("rasters", "osm", "damage", "access"):
        (out / sub).mkdir(exist_ok=True)
    meta = {
        "run_id": "run_demo",
        "area_name": "Rasuwa / Nuwakot corridor",
        "area_name_ne": "रसुवा / नुवाकोट करिडोर",
        "event_date": "2026-08-26",
        "osm_snapshot": "2026-07-27",
        "pairs": [
            {"platform": "Sentinel-1D", "track": 85, "direction": "ascending", "before": "2026-08-16", "after": "2026-08-28"},
            {"platform": "Sentinel-1D", "track": 121, "direction": "descending", "before": "2026-08-19", "after": "2026-08-31"},
        ],
        "warnings": ["synthetic test data"],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))
    (out / "grid.json").write_text(json.dumps({"crs": "EPSG:32645", "transform": [20, 0, 330000, 0, -20, 3110000],
                                               "width": 100, "height": 100, "pixel_m": 20}))
    (out / "aoi.geojson").write_text(json.dumps(fc([{"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[85.10, 27.85], [85.45, 27.85], [85.45, 28.30], [85.10, 28.30], [85.10, 27.85]]]}}])))

    places, settl = [], []
    for i, (n, ne, lon, lat, place, pop, p, h, mb, ma, wa) in enumerate(SETTLEMENTS):
        base = {"osm_id": 1000 + i, "name": n, "name_ne": ne, "place": place}
        if pop is not None:
            base["population"] = pop
        places.append(pt(lon, lat, base))
        settl.append(pt(lon, lat, {**base, "p_cutoff": p, "cutoff": p >= 0.5, "nearest_health": h,
                                   "minutes_before": mb, "minutes_after": ma, "walk_minutes_after": wa,
                                   "reason": "road cut by flood" if p >= 0.5 else ""}))
    (out / "osm" / "places.geojson").write_text(json.dumps(fc(places), ensure_ascii=False))
    (out / "access" / "settlements.geojson").write_text(json.dumps(fc(settl), ensure_ascii=False))
    (out / "osm" / "health.geojson").write_text(json.dumps(fc([
        pt(85.297, 28.108, {"osm_id": 2001, "name": "Rasuwa District Hospital", "kind": "hospital"}),
        pt(85.340, 28.200, {"osm_id": 2002, "name": "Timure Health Post", "kind": "health_post"}),
        pt(85.201, 27.991, {"osm_id": 2003, "name": "Kalikasthan Health Post", "kind": "health_post"}),
    ])))

    br = [line(85.2 + i * 0.03, 28.0 + i * 0.03, {"osm_id": 3000 + i, "name": n, "highway": "secondary",
                                                  "hit_prob": hp, "status": st}, 0.0005)
          for i, (n, st, hp) in enumerate(BRIDGES)]
    (out / "damage" / "bridges.geojson").write_text(json.dumps(fc(br)))
    (out / "osm" / "bridges.geojson").write_text(json.dumps(fc([{**b, "properties": {k: b["properties"][k] for k in ("osm_id", "name", "highway")}} for b in br])))
    rd = [line(85.2 + i * 0.02, 28.0 + i * 0.02, {"osm_id": 4000 + i, "name": n, "highway": hw, "bridge": False,
                                                  "length_m": L, "hit_prob": 0.9 if st == "destroyed_likely" else 0.5 if st == "damaged_possible" else 0.1,
                                                  "status": st})
          for i, (n, hw, L, st) in enumerate(ROADS)]
    (out / "damage" / "roads.geojson").write_text(json.dumps(fc(rd)))
    bl = [pt(85.25 + (i % 8) * 0.01, 28.05 + (i // 8) * 0.01, {"osm_id": 5000 + i, "hit_prob": 0.5, "status": st})
          for i, st in enumerate(BUILDING_STATUS)]
    (out / "damage" / "buildings.geojson").write_text(json.dumps(fc(bl)))

    chokes = [
        {"rank": 1, "osm_id": 3000, "kind": "bridge", "name": "Mailung Khola Bridge", "lon": 85.2, "lat": 28.0,
         "villages_reconnected": 3, "people_reconnected": 3767, "names": ["Gatlang", "Haku", "Mailung"]},
        {"rank": 2, "osm_id": 4000, "kind": "road", "name": "Pasang Lhamu Highway", "lon": 85.2, "lat": 28.0,
         "villages_reconnected": 2, "people_reconnected": 1352, "names": ["Thuman", "Bridim"]},
        {"rank": 3, "osm_id": 3001, "kind": "bridge", "name": "Bhote Koshi Bridge", "lon": 85.23, "lat": 28.03,
         "villages_reconnected": 1, "people_reconnected": 712, "names": ["Thuman"]},
    ]
    (out / "access" / "chokepoints.json").write_text(json.dumps(chokes, indent=1))
    write_rasters(out)
    return out


def write_rasters(out: Path) -> None:
    import numpy as np
    import rasterio
    from rasterio.transform import Affine
    arr = np.zeros((100, 100), dtype="uint8")
    arr.flat[:600] = 1          # 600 px * 400 m2 = 24 ha open water
    arr.flat[600:850] = 2       # 250 px = 10 ha debris
    arr.flat[-900:] = 255       # 900 px = 36 ha not observed
    prof = dict(driver="GTiff", height=100, width=100, count=1, dtype="uint8", crs="EPSG:32645",
                transform=Affine(20, 0, 330000, 0, -20, 3110000))
    with rasterio.open(out / "rasters" / "flood_class.tif", "w", **prof) as ds:
        ds.write(arr, 1)
    try:
        from PIL import Image
        img = np.full((200, 200, 3), 235, dtype="uint8")
        img[40:120, 30:160] = (40, 70, 120)
        img[120:150, 60:110] = (150, 110, 60)
        Image.fromarray(img).save(out / "rasters" / "preview_change.png")
    except Exception:
        pass


if __name__ == "__main__":
    write(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "run_demo")
