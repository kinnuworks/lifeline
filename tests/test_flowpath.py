"""Flow path on a synthetic valley: water runs down the valley floor and lists the villages in order."""

import json

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import Point

from lifeline.flowpath.trace import fill_depressions, run_flowpath

CRS = "EPSG:32645"
X0, Y0, PX = 300000.0, 3100000.0, 30.0


def _valley(h=200, w=160):
    r, c = np.mgrid[0:h, 0:w]
    centre = 80 + 10 * np.sin(r / 25.0)              # a gently winding valley
    z = 2000.0 - 3.0 * r + 0.8 * np.abs(c - centre) ** 1.3
    centre = centre[:, 0]
    z[100, int(centre[100])] -= 15.0                  # a pit the filling must remove
    return z.astype(np.float32), centre


def _to_ll(row, col):
    t = Transformer.from_crs(CRS, 4326, always_xy=True)
    return t.transform(X0 + (col + 0.5) * PX, Y0 - (row + 0.5) * PX)


def _make_run(tmp_path):
    z, centre = _valley()
    (tmp_path / "rasters").mkdir()
    (tmp_path / "osm").mkdir()
    with rasterio.open(tmp_path / "rasters" / "dem.tif", "w", driver="GTiff", height=z.shape[0], width=z.shape[1],
                       count=1, dtype="float32", crs=CRS, transform=from_origin(X0, Y0, PX, PX)) as dst:
        dst.write(z, 1)
    pts = {
        "Upper": (40, centre[40] + 1),
        "Middle": (110, centre[110] - 2),
        "Lower": (170, centre[170] + 2),
        "Ridge": (110, centre[110] + 60),             # high on the valley side, must be excluded
    }
    rows = [{"name": k, "place": "village", "osm_id": i} for i, k in enumerate(pts)]
    geoms = [Point(*_to_ll(r, c)) for r, c in pts.values()]
    gpd.GeoDataFrame(rows, geometry=geoms, crs=4326).to_file(tmp_path / "osm" / "places.geojson", driver="GeoJSON")
    return centre


def test_fill_removes_pit():
    z, centre = _valley()
    f = fill_depressions(z)
    assert f[100, int(centre[100])] >= z[100, int(centre[100])] + 10


def test_flowpath_lists_villages_in_order(tmp_path):
    centre = _make_run(tmp_path)
    lon, lat = _to_ll(5, centre[5] + 20)              # start on the valley side near the top
    res = run_flowpath(tmp_path, lon, lat)
    names = [s["name"] for s in res["settlements"]]
    assert names == ["Upper", "Middle", "Lower"]
    kms = [s["km"] for s in res["settlements"]]
    assert kms == sorted(kms) and kms[0] > 0.5
    assert res["length_km"] > 5
    gj = json.loads((tmp_path / "access" / "flowpath.geojson").read_text())
    kinds = [f["properties"]["kind"] for f in gj["features"]]
    assert kinds[:2] == ["path", "corridor"] and kinds.count("settlement") == 3
    assert gj["features"][0]["geometry"]["type"] == "LineString"
    assert gj["features"][1]["geometry"]["type"] in ("Polygon", "MultiPolygon")
