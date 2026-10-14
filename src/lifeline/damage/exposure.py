"""Which buildings, roads and bridges the mapped flood touched.

Each feature gets the flood probability it is exposed to: the highest
probability within a small buffer around it (OpenStreetMap geometry and the
20 m radar grid are both a few metres off). Features sitting on ground the
radar could not see are marked "unobserved" rather than clear.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from scipy import ndimage
from shapely.geometry import shape

from ..acquire.stac import Grid

STATUS_BANDS = [(0.7, "destroyed_likely"), (0.4, "damaged_possible"), (0.0, "clear")]


def _status(p: float, seen: bool) -> str:
    if not seen:
        return "unobserved"
    for lo, name in STATUS_BANDS:
        if p >= lo:
            return name
    return "clear"


def _sampler(grid: Grid, prob: np.ndarray, observed: np.ndarray, buffer_px: int):
    p = np.nan_to_num(prob, nan=0.0)
    p_max = ndimage.maximum_filter(p, size=2 * buffer_px + 1)
    seen = ndimage.maximum_filter(observed, size=2 * buffer_px + 1) > 0
    to_grid = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    inv = ~grid.affine

    def sample(lons: np.ndarray, lats: np.ndarray):
        x, y = to_grid.transform(lons, lats)
        col, row = inv * (np.asarray(x), np.asarray(y))
        r = np.clip(np.floor(row).astype(int), 0, grid.height - 1)
        c = np.clip(np.floor(col).astype(int), 0, grid.width - 1)
        inside = (row >= 0) & (row < grid.height) & (col >= 0) & (col < grid.width)
        return np.where(inside, p_max[r, c], 0.0), np.where(inside, seen[r, c], False)

    return sample


def _line_points(geom, step_deg: float = 0.0001):
    """Points every ~10 m along a line (in degrees, good enough for sampling)."""
    lines = [geom] if geom.geom_type == "LineString" else list(geom.geoms)
    xs, ys = [], []
    for ln in lines:
        n = max(2, int(ln.length / step_deg) + 1)
        for k in range(n):
            pt = ln.interpolate(k / (n - 1), normalized=True)
            xs.append(pt.x)
            ys.append(pt.y)
    return np.array(xs), np.array(ys)


def assess(run_dir: Path, grid: Grid) -> dict:
    with rasterio.open(run_dir / "rasters" / "flood_prob.tif") as src:
        prob = src.read(1)
    with rasterio.open(run_dir / "rasters" / "observed.tif") as src:
        observed = src.read(1)
    sample = _sampler(grid, prob, observed, buffer_px=1)
    out_dir = run_dir / "damage"
    out_dir.mkdir(exist_ok=True)
    summary = {}

    for layer in ("roads", "bridges"):
        fc = json.loads((run_dir / "osm" / f"{layer}.geojson").read_text())
        km = {}
        under_km = 0.0
        for f in fc["features"]:
            g = shape(f["geometry"])
            xs, ys = _line_points(g)
            p, seen = sample(xs, ys)
            # the stretch actually under mapped damage, as opposed to the whole OSM way it belongs to
            if p.size:
                stretch = float(np.mean(p >= 0.7)) * f["properties"].get("length_m", 0.0)
                f["properties"]["damaged_stretch_m"] = round(stretch, 1)
                under_km += stretch / 1000
            # a road is cut if any stretch of it is buried; use the 95th percentile so one noisy pixel does not close it
            hit = float(np.quantile(p, 0.95)) if p.size else 0.0
            share_seen = float(np.mean(seen)) if seen.size else 0.0
            f["properties"]["hit_prob"] = round(hit, 3)
            f["properties"]["observed_share"] = round(share_seen, 2)
            f["properties"]["status"] = _status(hit, share_seen >= 0.5 or hit >= 0.4)
            st = f["properties"]["status"]
            km[st] = km.get(st, 0.0) + f["properties"].get("length_m", 0.0) / 1000
        (out_dir / f"{layer}.geojson").write_text(json.dumps(fc))
        if layer == "roads":
            summary["roads_under_damage_km"] = round(under_km, 2)
        summary[layer] = {k: round(v, 2) for k, v in km.items()} if layer == "roads" else {
            st: sum(1 for f in fc["features"] if f["properties"]["status"] == st) for st in ("destroyed_likely", "damaged_possible", "clear", "unobserved")
        }

    fc = json.loads((run_dir / "osm" / "buildings.geojson").read_text())
    if fc["features"]:
        lons = np.array([f["geometry"]["coordinates"][0] for f in fc["features"]])
        lats = np.array([f["geometry"]["coordinates"][1] for f in fc["features"]])
        p, seen = sample(lons, lats)
        counts = {"destroyed_likely": 0, "damaged_possible": 0, "clear": 0, "unobserved": 0}
        for f, pi, si in zip(fc["features"], p, seen):
            st = _status(float(pi), bool(si) or pi >= 0.4)
            counts[st] += 1
            f["properties"]["hit_prob"] = round(float(pi), 3)
            f["properties"]["status"] = st
        # every building is written, clear ones included, so a check can count misses
        (out_dir / "buildings.geojson").write_text(json.dumps(fc, separators=(",", ":")))
        summary["buildings"] = counts | {"checked": len(fc["features"])}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary
