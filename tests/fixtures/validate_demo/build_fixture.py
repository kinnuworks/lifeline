"""Build a tiny synthetic run and a matching synthetic EMS reference folder.

Everything is laid out on a 50 x 50 grid of 20 m pixels in UTM 45N so that the
expected metrics can be worked out by hand (see tests/test_validate_metrics.py).

Grid origin (top-left) x=640000, y=3110000. AOI01 covers columns 5..44 and rows 5..44.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from affine import Affine
from shapely.geometry import LineString, Point, box

CRS = "EPSG:32645"
X0, Y0, PX, N = 640000.0, 3110000.0, 20.0, 50
T = Affine(PX, 0, X0, 0, -PX, Y0)


def col_x(c):
    return X0 + c * PX


def row_y(r):
    return Y0 - r * PX


AOI = box(col_x(5), row_y(45), col_x(45), row_y(5))


def _sq(x, y, half=5.0):
    return box(x - half, y - half, x + half, y + half)


def _write_tif(path: Path, arr, dtype):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=N, height=N, count=1, dtype=dtype,
                       crs=CRS, transform=T) as dst:
        dst.write(arr.astype(dtype), 1)


def _gj(path: Path, rows, crs=CRS):
    path.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(rows, geometry="geometry", crs=crs).to_crs(4326).to_file(path, driver="GeoJSON")


def build(dest: Path) -> tuple[Path, Path]:
    run = dest / "runs" / "demo_run"
    ref = dest / "reference"
    (run / "rasters").mkdir(parents=True, exist_ok=True)
    (run / "grid.json").write_text(json.dumps(
        {"crs": CRS, "transform": list(T)[:6], "width": N, "height": N, "pixel_m": PX}))
    _gj(run / "aoi.geojson", [{"geometry": box(X0, Y0 - N * PX, X0 + N * PX, Y0)}])

    # rasters: EMS event = cols 10..29, ours = cols 15..34, radar blind on cols 0..12
    fc = np.zeros((N, N), np.uint8)
    fc[:, 15:35] = 1
    obs = np.full((N, N), 2, np.uint8)
    obs[:, :13] = 0
    fc[:, :13] = 255
    prob = np.where(np.isin(fc, (1, 2)), 0.9, 0.1).astype("float32")
    _write_tif(run / "rasters" / "flood_class.tif", fc, "uint8")
    _write_tif(run / "rasters" / "observed.tif", obs, "uint8")
    _write_tif(run / "rasters" / "flood_prob.tif", prob, "float32")

    # buildings: ours are 10 m squares; EMS points sit 5 m off our centroids
    yb = 3109500.0
    ours_b = [
        (640200, "destroyed_likely"), (640260, "destroyed_likely"), (640320, "damaged_possible"),
        (640380, "clear"), (640440, "unobserved"), (640600, "clear"), (640660, "damaged_possible"),
    ]
    rows = [{"osm_id": i, "status": s, "hit_prob": 0.5, "geometry": _sq(x, yb)} for i, (x, s) in enumerate(ours_b)]
    rows.append({"osm_id": 99, "status": "destroyed_likely", "hit_prob": 0.9, "geometry": _sq(640800, 3109300)})
    _gj(run / "damage" / "buildings.geojson", rows)

    # roads we graded, 5 m north of the EMS lines at y=3109700
    yr = 3109705.0
    segs = [(640100, 640300, "destroyed_likely"), (640300, 640400, "clear"),
            (640400, 640600, "clear"), (640600, 640800, "damaged_possible")]
    _gj(run / "damage" / "roads.geojson",
        [{"osm_id": 100 + i, "status": s, "hit_prob": 0.5, "bridge": False, "highway": "secondary",
          "geometry": LineString([(a, yr), (b, yr)])} for i, (a, b, s) in enumerate(segs)])
    _gj(run / "damage" / "bridges.geojson", [
        {"osm_id": 200, "status": "destroyed_likely", "geometry": LineString([(640240, 3109300), (640260, 3109300)])},
        {"osm_id": 201, "status": "clear", "geometry": LineString([(640690, 3109300), (640710, 3109300)])},
    ])

    # pre-event network for the cut-off check: one road along y=3109800
    _gj(run / "osm" / "roads.geojson", [
        {"osm_id": 300, "highway": "secondary", "geometry": LineString([(640150, 3109800), (640850, 3109800)])}])
    _gj(run / "osm" / "health.geojson", [{"osm_id": 400, "name": "Health post", "kind": "health_post",
                                          "geometry": Point(640150, 3109800)}])
    _gj(run / "access" / "settlements.geojson", [
        {"osm_id": 500, "name": "West", "place": "village", "cutoff": False, "p_cutoff": 0.1, "geometry": Point(640300, 3109810)},
        {"osm_id": 501, "name": "East", "place": "village", "cutoff": True, "p_cutoff": 0.9, "geometry": Point(640850, 3109810)},
        {"osm_id": 502, "name": "Mid", "place": "hamlet", "cutoff": True, "p_cutoff": 0.7, "geometry": Point(640200, 3109790)},
        {"osm_id": 503, "name": "Far", "place": "hamlet", "cutoff": False, "p_cutoff": 0.0, "geometry": Point(643000, 3112000)},
    ])

    # reference
    base = {"aoi": "AOI01", "name": "", "product": "GRA_PRODUCT", "product_id": 1, "stage": "first",
            "image_dates": "2026-08-27T05:05:00", "source_file": "synthetic", "kind": "", "detail": ""}
    f = []

    def add(layer, grade, geom, **kw):
        f.append({**base, "layer": layer, "grade": grade, "geometry": geom, **kw})

    add("observed_event", "", box(col_x(10), row_y(45), col_x(30), row_y(5)), kind="6-Mass Movement")
    for x, g in [(640200, "destroyed"), (640260, "destroyed"), (640320, "destroyed"), (640380, "destroyed"),
                 (640440, "damaged"), (640500, "destroyed"),
                 (640600, "no_visible_damage"), (640660, "no_visible_damage")]:
        add("built_up", g, Point(x + 5, yb))
    add("transportation", "destroyed", LineString([(640100, 3109700), (640400, 3109700)]))
    add("transportation", "no_visible_damage", LineString([(640400, 3109700), (640800, 3109700)]))
    add("transportation", "destroyed", LineString([(640500, 3109800), (640600, 3109800)]))
    add("bridge", "destroyed", Point(640250, 3109300))
    add("bridge", "destroyed", Point(640700, 3109300))
    add("bridge", "damaged", Point(640500, 3109200))
    add("bridge", "possibly_damaged", Point(640550, 3109200))
    # a monitoring product that must be ignored when stage="first"
    f.append({**base, "layer": "built_up", "grade": "destroyed", "geometry": Point(640605, yb),
              "product": "GRA_MONIT01", "stage": "monitoring"})
    _gj(ref / "features.geojson", f)
    _gj(ref / "aois.geojson", [
        {"aoi": "AOI01", "name": "Demo", "status": "delivered", "products": "GRA_PRODUCT,GRA_MONIT01", "geometry": AOI},
        {"aoi": "AOI04", "name": "Cancelled demo", "status": "cancelled", "products": "",
         "geometry": box(650000, 3100000, 651000, 3101000)},
    ])
    return run, ref


if __name__ == "__main__":
    import sys
    r, f = build(Path(sys.argv[1]))
    print(r, f)
