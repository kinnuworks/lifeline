"""Trace where water released at a point would run, and list the settlements along the way.

Method
1. Fill depressions in the DEM (morphological reconstruction, the standard pit-filling
   result) so every cell drains to the raster edge or to a no-data cell.
2. Step downstream from the start cell along D8 steepest descent on the filled DEM. On flat
   ground left by the filling, a breadth-first search crosses the flat to the nearest cell
   that has a lower neighbour.
3. Estimate the valley floor around the path: cells within `max_halfwidth_m` of the path that
   are less than `valley_m` above it (HAND when hand.tif is present, otherwise height above
   the nearest path cell). The corridor half-width is taken per stretch of path from the
   farthest valley cells (90th percentile), clipped to [min_halfwidth_m, max_halfwidth_m].
4. Settlements inside the corridor are listed in order of distance along the path.

This shows where the valley floor is, not how deep a flood would be or when it arrives.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from scipy import ndimage
from shapely.geometry import LineString, mapping

NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def fill_depressions(dem: np.ndarray) -> np.ndarray:
    """Pit-filled copy of `dem`. NaN cells and the raster border act as outlets."""
    from skimage.morphology import reconstruction

    z = dem.astype(np.float64).copy()
    nan = ~np.isfinite(z)
    low = np.nanmin(z) - 1.0 if (~nan).any() else 0.0
    z[nan] = low
    seed = np.full_like(z, z.max())
    seed[0, :], seed[-1, :], seed[:, 0], seed[:, -1] = z[0, :], z[-1, :], z[:, 0], z[:, -1]
    seed[nan] = low
    filled = reconstruction(seed, z, method="erosion")
    filled[nan] = np.nan
    return filled


def d8_path(filled: np.ndarray, r0: int, c0: int, pixel_m: float = 30.0, stop_mask: np.ndarray | None = None):
    """List of (row, col) from the start cell to the raster edge, a no-data cell or stop_mask."""
    H, W = filled.shape
    diag = np.sqrt(2.0)
    path = [(r0, c0)]
    seen = {(r0, c0)}
    r, c = r0, c0

    def outlet(rr, cc):
        if rr <= 0 or cc <= 0 or rr >= H - 1 or cc >= W - 1:
            return True
        if stop_mask is not None and stop_mask[rr, cc]:
            return True
        return False

    def lower_neighbour(rr, cc):
        z = filled[rr, cc]
        best, bd = None, 0.0
        for dr, dc in NB:
            rn, cn = rr + dr, cc + dc
            zn = filled[rn, cn]
            if not np.isfinite(zn):
                return (rn, cn)  # drains into no-data
            drop = (z - zn) / (diag if dr and dc else 1.0)
            if drop > bd + 1e-9 and (rn, cn) not in seen:
                best, bd = (rn, cn), drop
        return best

    for _ in range(H * W):
        if outlet(r, c) or not np.isfinite(filled[r, c]):
            break
        nxt = lower_neighbour(r, c)
        if nxt is None:
            # flat: breadth-first search across equal cells to one with a lower neighbour
            z = filled[r, c]
            prev = {(r, c): None}
            q = deque([(r, c)])
            goal = None
            while q:
                cur = q.popleft()
                if cur != (r, c) and (outlet(*cur) or lower_neighbour(*cur) is not None):
                    goal = cur
                    break
                for dr, dc in NB:
                    rn, cn = cur[0] + dr, cur[1] + dc
                    if 0 <= rn < H and 0 <= cn < W and (rn, cn) not in prev and (rn, cn) not in seen \
                            and np.isfinite(filled[rn, cn]) and abs(filled[rn, cn] - z) < 1e-6:
                        prev[(rn, cn)] = cur
                        q.append((rn, cn))
            if goal is None:
                break
            chain = []
            x = goal
            while x is not None and x != (r, c):
                chain.append(x)
                x = prev[x]
            for x in reversed(chain):
                path.append(x)
                seen.add(x)
            r, c = goal
            continue
        if not np.isfinite(filled[nxt]):
            break
        path.append(nxt)
        seen.add(nxt)
        r, c = nxt
    return path


def _py(v):
    if v is None:
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and np.isnan(v):
        return None
    return v


def _rowcol(transform, x, y):
    from rasterio.transform import rowcol
    r, c = rowcol(transform, x, y)
    return int(r), int(c)


def trace_flowpath(dem: np.ndarray, transform, crs, lon: float, lat: float, hand: np.ndarray | None = None,
                   places=None, valley_m: float = 10.0, max_halfwidth_m: float = 1500.0,
                   min_halfwidth_m: float = 60.0, chunk_cells: int = 20) -> dict:
    """Return {"path": LineString (raster CRS), "corridor": Polygon, "settlements": [...], ...}."""
    from rasterio.transform import xy as rc_xy

    pixel = float(abs(transform.a))
    to_r = Transformer.from_crs(4326, crs, always_xy=True)
    x0, y0 = to_r.transform(lon, lat)
    H, W = dem.shape
    r0, c0 = _rowcol(transform, x0, y0)
    if not (0 <= r0 < H and 0 <= c0 < W):
        raise ValueError("start point is outside the DEM")
    filled = fill_depressions(dem)
    cells = d8_path(filled, r0, c0, pixel)
    rr = np.array([p[0] for p in cells])
    cc = np.array([p[1] for p in cells])
    xs, ys = rc_xy(transform, rr, cc)
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    if len(xs) == 1:
        xs, ys = np.r_[xs, xs + 1e-3], np.r_[ys, ys]
    line = LineString(np.column_stack([xs, ys]))

    # valley mask in a window around the path
    m = int(np.ceil(max_halfwidth_m / pixel)) + 1
    ra, rb = max(rr.min() - m, 0), min(rr.max() + m + 1, H)
    ca, cb = max(cc.min() - m, 0), min(cc.max() + m + 1, W)
    win = (slice(ra, rb), slice(ca, cb))
    onpath = np.zeros((rb - ra, cb - ca), dtype=bool)
    onpath[rr - ra, cc - ca] = True
    dist, (ir, ic) = ndimage.distance_transform_edt(~onpath, return_indices=True)
    dist *= pixel
    z = dem[win].astype(float)
    if hand is not None:
        above = hand[win].astype(float)
        basis = "HAND"
    else:
        above = z - z[ir, ic]
        basis = "height above path"
    valley = (above <= valley_m) & np.isfinite(above) & (dist <= max_halfwidth_m)
    valley |= onpath
    lab, _ = ndimage.label(valley, structure=np.ones((3, 3)))
    keep = np.unique(lab[onpath])
    valley &= np.isin(lab, keep[keep > 0])

    # per path cell: 90th percentile distance of the valley cells it is nearest to
    idx_of = -np.ones(onpath.shape, dtype=int)
    idx_of[rr - ra, cc - ca] = np.arange(len(rr))
    owner = idx_of[ir, ic][valley]
    dv = dist[valley]
    n = len(rr)
    nchunk = max(1, int(np.ceil(n / chunk_cells)))
    chunk_of = np.minimum(owner // chunk_cells, nchunk - 1)
    widths = np.full(nchunk, min_halfwidth_m)
    for k in range(nchunk):
        d = dv[chunk_of == k]
        if len(d):
            widths[k] = np.clip(np.percentile(d, 90) + pixel / 2, min_halfwidth_m, max_halfwidth_m)
    pieces = []
    for k in range(nchunk):
        a, b = k * chunk_cells, min((k + 1) * chunk_cells + 1, n)
        seg = np.column_stack([xs[a:b], ys[a:b]])
        geom = LineString(seg) if len(seg) > 1 else shapely.Point(seg[0])
        pieces.append(geom.buffer(widths[k]))
    corridor = shapely.union_all(pieces)

    found = []
    if places is not None and len(places):
        from lifeline.access.montecarlo import place_name
        pg = places.to_crs(crs)
        pts = pg.geometry.representative_point().values
        inside = shapely.contains(corridor, pts)
        for i in np.flatnonzero(inside):
            row = places.iloc[i]
            found.append({
                "name": place_name(row),
                "osm_id": _py(row.get("osm_id")),
                "place": _py(row.get("place")),
                "km": round(float(line.project(pts[i])) / 1000.0, 2),
                "offset_m": round(float(line.distance(pts[i])), 0),
                "geom": pts[i],
            })
        found.sort(key=lambda d: d["km"])
        for k, d in enumerate(found, 1):
            d["order"] = k
    zs = filled[rr, cc]
    return {
        "path": line, "corridor": corridor, "settlements": found, "crs": str(crs),
        "length_km": round(line.length / 1000.0, 2),
        "drop_m": round(float(zs[0] - zs[-1]), 1) if np.isfinite(zs[[0, -1]]).all() else None,
        "median_halfwidth_m": round(float(np.median(widths)), 0),
        "valley_basis": basis, "valley_m": valley_m,
        "start": [lon, lat],
    }


def to_geojson(res: dict) -> dict:
    t = Transformer.from_crs(res["crs"], 4326, always_xy=True)
    def tf(g):
        return shapely.transform(g, lambda x, y: t.transform(x, y), interleaved=False)
    feats = [
        {"type": "Feature", "geometry": mapping(tf(res["path"])),
         "properties": {"kind": "path", "length_km": res["length_km"], "drop_m": res["drop_m"],
                        "start_lon": res["start"][0], "start_lat": res["start"][1]}},
        {"type": "Feature", "geometry": mapping(tf(res["corridor"].simplify(5.0))),
         "properties": {"kind": "corridor", "median_halfwidth_m": res["median_halfwidth_m"],
                        "valley_basis": res["valley_basis"], "valley_m": res["valley_m"]}},
    ]
    for s in res["settlements"]:
        feats.append({"type": "Feature", "geometry": mapping(tf(s["geom"])),
                      "properties": {"kind": "settlement", "name": s["name"], "osm_id": s["osm_id"],
                                     "place": s["place"], "order": s["order"], "km": s["km"],
                                     "offset_m": s["offset_m"]}})
    return {"type": "FeatureCollection", "features": feats}


def run_flowpath(run_dir: str | Path, lon: float, lat: float, write: bool = True, **kw) -> dict:
    """Trace from (lon, lat) using runs/<id>/rasters/dem.tif and write access/flowpath.geojson."""
    import geopandas as gpd
    import rasterio

    run_dir = Path(run_dir)
    with rasterio.open(run_dir / "rasters" / "dem.tif") as src:
        dem = src.read(1).astype(np.float32)
        if src.nodata is not None:
            dem[dem == src.nodata] = np.nan
        transform, crs = src.transform, src.crs
    hand = None
    hp = run_dir / "rasters" / "hand.tif"
    if hp.exists():
        with rasterio.open(hp) as src:
            h = src.read(1).astype(np.float32)
            if src.nodata is not None:
                h[h == src.nodata] = np.nan
            if h.shape == dem.shape:
                hand = h
    pp = run_dir / "osm" / "places.geojson"
    places = gpd.read_file(pp) if pp.exists() else None
    if places is not None and places.crs is None:
        places = places.set_crs(4326)
    res = trace_flowpath(dem, transform, crs, lon, lat, hand=hand, places=places, **kw)
    if write:
        out = run_dir / "access" / "flowpath.geojson"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(to_geojson(res), ensure_ascii=False))
    return res
