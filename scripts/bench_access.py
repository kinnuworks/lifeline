"""Timing check on a synthetic network the size of the case study (about 40 x 50 km,
thousands of road segments, 180 settlements, 17 health facilities) and a 30 m DEM.

Usage: uv run python scripts/bench_access.py OUT_DIR [SPACING_M]
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from lifeline.access.pipeline import run_access
from lifeline.flowpath.trace import run_flowpath

CRS = "EPSG:32645"
X0, Y0 = 310000.0, 3135000.0   # roughly the Rasuwa / Nuwakot corridor


def main(out: Path, spacing_m: float = 1000.0):
    rng = np.random.default_rng(0)
    to_ll = Transformer.from_crs(CRS, 4326, always_xy=True)
    W, H = 40000.0, 50000.0
    # jittered lattice of junctions, 1 km apart, joined by wiggly roads with 6 vertices each
    nx_, ny_ = int(W / spacing_m) + 1, int(H / spacing_m) + 1
    gx, gy = np.meshgrid(np.linspace(0, W, nx_), np.linspace(0, H, ny_))
    jit = rng.normal(0, 150, size=(2, ny_, nx_))
    px, py = gx + jit[0], gy + jit[1]
    classes = ["primary", "secondary", "tertiary", "unclassified", "track", "path"]
    feats = []
    oid = 1

    def add(a, b, hw):
        nonlocal oid
        t = np.linspace(0, 1, 7)
        xs = a[0] + (b[0] - a[0]) * t + np.r_[0, rng.normal(0, 60, 5), 0]
        ys = a[1] + (b[1] - a[1]) * t + np.r_[0, rng.normal(0, 60, 5), 0]
        lon, lat = to_ll.transform(X0 + xs, Y0 + ys)
        bridge = rng.random() < 0.04
        r = rng.random()
        if r < 0.1:
            hit, st = None, "unobserved"
        elif r < 0.18:
            hit, st = float(rng.uniform(0.5, 0.98)), "destroyed_likely"
        elif r < 0.3:
            hit, st = float(rng.uniform(0.2, 0.5)), "damaged_possible"
        else:
            hit, st = float(rng.uniform(0, 0.1)), "clear"
        feats.append({"type": "Feature", "properties": {"osm_id": oid, "highway": hw, "name": f"road {oid}",
                      "bridge": bool(bridge), "hit_prob": hit, "status": st},
                      "geometry": {"type": "LineString", "coordinates": np.column_stack([lon, lat]).tolist()}})
        oid += 1

    for j in range(ny_):
        for i in range(nx_):
            if i + 1 < nx_ and rng.random() < 0.8:
                add((px[j, i], py[j, i]), (px[j, i + 1], py[j, i + 1]), classes[rng.integers(0, 6)])
            if j + 1 < ny_ and rng.random() < 0.8:
                add((px[j, i], py[j, i]), (px[j + 1, i], py[j + 1, i]), classes[rng.integers(0, 6)])

    def pts(n, kind):
        xs, ys = rng.uniform(0, W, n), rng.uniform(0, H, n)
        lon, lat = to_ll.transform(X0 + xs, Y0 + ys)
        return [{"type": "Feature", "properties": {"osm_id": 10_000 + k, "name": f"{kind} {k}", "place": "village",
                 "kind": "hospital" if kind == "health" else None,
                 "population": str(int(rng.integers(100, 3000)))},
                 "geometry": {"type": "Point", "coordinates": [lon[k], lat[k]]}} for k in range(n)]

    for d in ("damage", "osm", "rasters"):
        (out / d).mkdir(parents=True, exist_ok=True)
    fc = lambda f: json.dumps({"type": "FeatureCollection", "features": f})  # noqa: E731
    (out / "damage" / "roads.geojson").write_text(fc(feats))
    (out / "osm" / "places.geojson").write_text(fc(pts(180, "village")))
    (out / "osm" / "health.geojson").write_text(fc(pts(17, "health")))
    print(f"{len(feats)} road lines written")

    t = time.perf_counter()
    s = run_access(out, n_draws=1000)
    print(f"access: {time.perf_counter() - t:.1f} s total, timings {s['timings']}")
    print(f"graph: {s['graph']['nodes']} nodes, {s['graph']['edges']} edges; cut off {s['cutoff_total_p50_plus']}")

    # DEM: 40 x 50 km at 30 m with a valley and noise
    h, w = int(H / 30), int(W / 30)
    r, c = np.mgrid[0:h, 0:w]
    centre = w / 2 + 60 * np.sin(r / 150.0)
    z = 3500 - 1.5 * r + 0.6 * np.abs(c - centre) ** 1.2 + rng.normal(0, 3, (h, w))
    with rasterio.open(out / "rasters" / "dem.tif", "w", driver="GTiff", height=h, width=w, count=1,
                       dtype="float32", crs=CRS, transform=from_origin(X0, Y0 + H, 30, 30)) as dst:
        dst.write(z.astype(np.float32), 1)
    lon, lat = to_ll.transform(X0 + W / 2 + 3000, Y0 + H - 1000)
    t = time.perf_counter()
    f = run_flowpath(out, lon, lat)
    print(f"flowpath on {h}x{w} DEM: {time.perf_counter() - t:.1f} s, {f['length_km']} km, "
          f"{len(f['settlements'])} settlements in corridor")


if __name__ == "__main__":
    main(Path(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else 1000.0)
