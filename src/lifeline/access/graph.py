"""Build a routable road graph from the pre-event OSM roads with damage attributes.

Input: runs/<id>/damage/roads.geojson (LineStrings with osm_id, highway, name, bridge,
hit_prob, status). Optional: damage/bridges.geojson (per-bridge hit_prob that overrides
the road copy when higher) and rasters/slope.tif (terrain slope, used as a speed penalty).

Noding rules
- lines are split at vertices shared by two or more lines and at line crossings,
  except crossings that involve a bridge (a bridge passes over things, it does not meet them);
- dangling endpoints within `snap_m` (15 m) of another endpoint or another line are joined;
- bridges always stay as their own edges, so a bridge can be closed on its own.

Speeds are rough figures for Nepal hill roads, not measured ones.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np
import shapely
from scipy.spatial import cKDTree
from shapely.geometry import LineString
from shapely.ops import substring

# km/h by OSM highway class (the "_link" suffix is stripped first)
DRIVE_KMH = {
    "motorway": 40.0,
    "trunk": 35.0,
    "primary": 35.0,
    "secondary": 25.0,
    "tertiary": 20.0,
    "unclassified": 12.0,
    "residential": 12.0,
    "road": 12.0,
    "track": 12.0,
    "service": 10.0,
    "living_street": 10.0,
}
FLAG_AT = 0.4   # same line as damaged_possible in damage/exposure.py
WALK_ONLY = {"path", "footway", "steps", "pedestrian", "bridleway", "cycleway", "trail"}
WALK_KMH = 3.0
MAIN_ROADS = {"motorway", "trunk", "primary", "secondary"}


def highway_class(h) -> str:
    h = str(h or "").strip().lower()
    if h.endswith("_link"):
        h = h[: -len("_link")]
    return h


def drive_slope_factor(slope_deg: np.ndarray) -> np.ndarray:
    """Speed multiplier for vehicles. Terrain slope is not road grade (roads switch back),
    so the penalty is mild: 30 degrees of terrain halves the speed, never worse than that."""
    return np.clip(1.0 - np.nan_to_num(slope_deg) / 60.0, 0.5, 1.0)


def walk_slope_factor(slope_deg: np.ndarray) -> np.ndarray:
    return np.clip(1.0 - np.nan_to_num(slope_deg) / 50.0, 0.4, 1.0)


@dataclass
class RoadGraph:
    crs: str
    nodes_xy: np.ndarray            # (N, 2) metres in `crs`
    u: np.ndarray                   # (E,) int
    v: np.ndarray                   # (E,) int
    length_m: np.ndarray
    highway: np.ndarray             # object, normalised class
    drivable: np.ndarray            # bool
    bridge: np.ndarray              # bool
    osm_id: np.ndarray              # object
    name: np.ndarray                # object
    hit_prob: np.ndarray            # float, NaN when missing
    status: np.ndarray              # object
    p_close: np.ndarray             # closure probability used by the simulation
    unobserved: np.ndarray          # bool, p_close came from the prior
    slope_deg: np.ndarray
    drive_min: np.ndarray           # inf for walk-only ways
    walk_min: np.ndarray
    geoms: np.ndarray               # shapely LineStrings in `crs`
    stats: dict = field(default_factory=dict)

    @property
    def n_nodes(self) -> int:
        return len(self.nodes_xy)

    @property
    def n_edges(self) -> int:
        return len(self.u)


def _metric_crs(run_dir: Path | None, gdf: gpd.GeoDataFrame) -> str:
    if run_dir is not None:
        gj = Path(run_dir) / "grid.json"
        if gj.exists():
            try:
                crs = json.loads(gj.read_text()).get("crs")
                if crs:
                    return str(crs)
            except (ValueError, OSError):
                pass
    return str(gdf.to_crs(4326).estimate_utm_crs())


def _as_bool(x) -> bool:
    if isinstance(x, str):
        return x.strip().lower() not in ("", "no", "false", "0", "none", "nan")
    if x is None:
        return False
    try:
        if isinstance(x, float) and math.isnan(x):
            return False
    except TypeError:
        pass
    return bool(x)


class _UF:
    def __init__(self, n):
        self.p = np.arange(n)

    def find(self, a):
        p = self.p
        while p[a] != a:
            p[a] = p[p[a]]
            a = p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def build_graph(
    roads: gpd.GeoDataFrame,
    crs: str | None = None,
    snap_m: float = 15.0,
    unobserved_prior: float = 0.15,
    bridge_damage: gpd.GeoDataFrame | None = None,
    slope_path: Path | None = None,
) -> RoadGraph:
    """Node the road lines and return a RoadGraph. `roads` may be in any CRS."""
    if roads.crs is None:
        roads = roads.set_crs(4326)
    crs = crs or str(roads.to_crs(4326).estimate_utm_crs())
    g = roads.to_crs(crs)
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    g = g.explode(index_parts=False).reset_index(drop=True)
    g = g[g.geom_type == "LineString"].reset_index(drop=True)
    g["geometry"] = shapely.force_2d(g.geometry.values)

    n_lines = len(g)
    geoms = np.asarray(g.geometry.values)
    cols = g.columns
    get = lambda c, d=None: (g[c].tolist() if c in cols else [d] * n_lines)  # noqa: E731
    hw = [highway_class(h) for h in get("highway", "")]
    is_bridge = np.array([_as_bool(b) for b in get("bridge", False)], dtype=bool)
    osm = get("osm_id", None)
    # lines without an OSM id get their own key so they are never grouped with each other
    osm = [o if (o is not None and str(o) not in ("", "nan", "None")) else f"line{i}" for i, o in enumerate(osm)]
    names = [n if isinstance(n, str) else "" for n in get("name", "")]
    hit = np.array([np.nan if h is None else float(h) for h in get("hit_prob", None)], dtype=float)
    status = [s if isinstance(s, str) else "unobserved" for s in get("status", "unobserved")]

    # bridges.geojson may carry its own hit_prob for the same osm_id; keep the larger
    if bridge_damage is not None and len(bridge_damage) and "osm_id" in bridge_damage.columns:
        bmap = {}
        for _, r in bridge_damage.iterrows():
            hp = r.get("hit_prob")
            if hp is None or (isinstance(hp, float) and math.isnan(hp)):
                continue
            bmap[str(r["osm_id"])] = (float(hp), r.get("status") or "")
        for i in range(n_lines):
            key = str(osm[i])
            if key in bmap:
                is_bridge[i] = True
                hp, st = bmap[key]
                if np.isnan(hit[i]) or hp > hit[i]:
                    hit[i] = hp
                    status[i] = st or status[i]

    # 1. split positions along each line, as distances from the start
    splits: list[list[float]] = [[0.0, float(gm.length)] for gm in geoms]

    # 1a. vertices shared between two or more lines
    coords, idx = shapely.get_coordinates(geoms, return_index=True)
    key = np.round(coords, 2)
    _, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    pair = np.unique(np.stack([inv, idx], 1), axis=0)
    counts = np.bincount(pair[:, 0], minlength=inv.max() + 1 if len(inv) else 0)
    shared = counts[inv] >= 2
    if shared.any():
        pts = shapely.points(coords[shared])
        d = shapely.line_locate_point(geoms[idx[shared]], pts)
        for li, dd in zip(idx[shared], d):
            splits[li].append(float(dd))

    # 1b. crossings between non-bridge lines
    tree = shapely.STRtree(geoms)
    a, b = tree.query(geoms, predicate="intersects")
    m = (a < b) & ~is_bridge[a] & ~is_bridge[b]
    a, b = a[m], b[m]
    n_cross = 0
    if len(a):
        inter = shapely.intersection(geoms[a], geoms[b])
        parts, pidx = shapely.get_parts(inter, return_index=True)
        # points, or the vertices of overlapping pieces
        pc, pci = shapely.get_coordinates(parts, return_index=True)
        owner = pidx[pci]
        if len(pc):
            pts = shapely.points(pc)
            da = shapely.line_locate_point(geoms[a[owner]], pts)
            db = shapely.line_locate_point(geoms[b[owner]], pts)
            for la, lb, x, y in zip(a[owner], b[owner], da, db):
                splits[la].append(float(x))
                splits[lb].append(float(y))
            n_cross = len(pc)

    # 1c. dangling endpoints that stop short of another line: insert a split there
    starts = shapely.get_point(geoms, 0)
    ends = shapely.get_point(geoms, -1)
    ep = np.concatenate([starts, ends])
    ep_line = np.concatenate([np.arange(n_lines), np.arange(n_lines)])
    qi, qt = tree.query(ep, predicate="dwithin", distance=snap_m)
    keep = qt != ep_line[qi]
    qi, qt = qi[keep], qt[keep]
    n_snap_line = 0
    if len(qi):
        dist = shapely.distance(ep[qi], geoms[qt])
        mm = dist > 0.01
        for i_ep, t, dd in zip(qi[mm], qt[mm], dist[mm]):
            pos = float(shapely.line_locate_point(geoms[t], ep[i_ep]))
            L = geoms[t].length
            # only snap onto the interior; endpoint-to-endpoint gaps are handled by clustering
            if 1.0 < pos < L - 1.0:
                splits[t].append(pos)
                n_snap_line += 1

    # 2. cut each line into segments
    seg_geoms, seg_line = [], []
    for li, gm in enumerate(geoms):
        L = gm.length
        s = np.unique(np.clip(np.round(np.array(splits[li]), 3), 0, L))
        if len(s) < 2:
            continue
        for x0, x1 in zip(s[:-1], s[1:]):
            if x1 - x0 < 0.01:
                continue
            sg = substring(gm, float(x0), float(x1)) if (x0 > 0 or x1 < L) else gm
            if sg.geom_type != "LineString":
                continue
            seg_geoms.append(sg)
            seg_line.append(li)
    seg_geoms = np.asarray(seg_geoms, dtype=object)
    seg_line = np.asarray(seg_line, dtype=int)

    # 3. node clustering: endpoints within snap_m become one node
    s0 = shapely.get_coordinates(shapely.get_point(seg_geoms, 0))
    s1 = shapely.get_coordinates(shapely.get_point(seg_geoms, -1))
    allp = np.vstack([s0, s1])
    kd = cKDTree(allp)
    pairs = kd.query_pairs(snap_m, output_type="ndarray")
    uf = _UF(len(allp))
    exact = 0
    for i, j in pairs:
        uf.union(int(i), int(j))
    roots = np.array([uf.find(i) for i in range(len(allp))])
    uniq, node_of = np.unique(roots, return_inverse=True)
    nodes_xy = np.zeros((len(uniq), 2))
    np.add.at(nodes_xy, node_of, allp)
    nodes_xy /= np.bincount(node_of)[:, None]
    ns = len(seg_geoms)
    u = node_of[:ns]
    v = node_of[ns:]
    # count endpoint joins that were gaps, not exact matches
    if len(pairs):
        dd = np.hypot(*(allp[pairs[:, 0]] - allp[pairs[:, 1]]).T)
        exact = int((dd > 0.5).sum())

    ok = u != v
    u, v, seg_geoms, seg_line = u[ok], v[ok], seg_geoms[ok], seg_line[ok]

    # drop nodes no edge uses
    used = np.unique(np.concatenate([u, v]))
    remap = -np.ones(len(nodes_xy), dtype=int)
    remap[used] = np.arange(len(used))
    nodes_xy = nodes_xy[used]
    u, v = remap[u], remap[v]

    hw_e = np.array([hw[i] for i in seg_line], dtype=object)
    drivable = np.array([h not in WALK_ONLY for h in hw_e])
    bridge_e = is_bridge[seg_line]
    hit_e = hit[seg_line]
    status_e = np.array([status[i] for i in seg_line], dtype=object)
    unobs = (status_e == "unobserved") | np.isnan(hit_e)
    # Only roads the damage map flags (hit >= 0.4) or could not see can close. A clear road's
    # score is not a calibrated chance of failure: summed over the dozens of stretches on a
    # long route it would cut off villages far from any damage.
    flagged = np.nan_to_num(hit_e) >= FLAG_AT
    p_close = np.where(unobs, unobserved_prior, np.where(flagged, np.nan_to_num(hit_e), 0.0))
    p_close = np.clip(p_close, 0.0, 1.0)
    length = shapely.length(seg_geoms).astype(float)

    slope = np.zeros(len(u))
    has_slope = False
    if slope_path is not None and Path(slope_path).exists():
        slope = _sample_slope(slope_path, seg_geoms, crs)
        has_slope = True

    speed = np.array([DRIVE_KMH.get(h, 12.0) for h in hw_e]) * drive_slope_factor(slope)
    drive_min = np.where(drivable, length / 1000.0 / speed * 60.0, np.inf)
    walk_min = length / 1000.0 / (WALK_KMH * walk_slope_factor(slope)) * 60.0

    rg = RoadGraph(
        crs=crs, nodes_xy=nodes_xy, u=u, v=v, length_m=length, highway=hw_e,
        drivable=drivable, bridge=bridge_e,
        osm_id=np.array([osm[i] for i in seg_line], dtype=object),
        name=np.array([names[i] for i in seg_line], dtype=object),
        hit_prob=hit_e, status=status_e, p_close=p_close, unobserved=unobs,
        slope_deg=slope, drive_min=drive_min, walk_min=walk_min, geoms=seg_geoms,
    )
    rg.stats = graph_stats(rg)
    rg.stats.update(
        input_lines=n_lines,
        crossing_splits=int(n_cross),
        endpoint_gap_joins=int(exact),
        endpoint_to_line_snaps=int(n_snap_line),
        snap_m=snap_m,
        slope_penalty=has_slope,
        unobserved_prior=unobserved_prior,
    )
    return rg


def _sample_slope(path: Path, geoms: np.ndarray, crs: str) -> np.ndarray:
    import rasterio
    from pyproj import Transformer

    mids = shapely.line_interpolate_point(geoms, 0.5, normalized=True)
    xy = shapely.get_coordinates(mids)
    with rasterio.open(path) as src:
        if src.crs is not None and str(src.crs) != crs:
            t = Transformer.from_crs(crs, src.crs, always_xy=True)
            xs, ys = t.transform(xy[:, 0], xy[:, 1])
            xy = np.column_stack([xs, ys])
        vals = np.array([s[0] for s in src.sample([tuple(p) for p in xy])], dtype=float)
        nod = src.nodata
    if nod is not None:
        vals[vals == nod] = np.nan
    vals[(vals < 0) | (vals > 90)] = np.nan
    return np.nan_to_num(vals, nan=0.0)


def graph_stats(rg: RoadGraph) -> dict:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    def comps(mask):
        if not mask.any():
            return 0, 0.0
        m = coo_matrix((np.ones(mask.sum()), (rg.u[mask], rg.v[mask])), shape=(rg.n_nodes, rg.n_nodes))
        n, lab = connected_components(m, directed=False)
        nodes = np.unique(np.concatenate([rg.u[mask], rg.v[mask]]))
        cnt = np.bincount(lab[nodes])
        return int((cnt > 0).sum()), float(cnt.max() / len(nodes))

    km_by_class: dict[str, float] = {}
    for h, L in zip(rg.highway, rg.length_m):
        km_by_class[h or "unknown"] = km_by_class.get(h or "unknown", 0.0) + L / 1000.0
    nc, big = comps(rg.drivable)
    return {
        "nodes": rg.n_nodes,
        "edges": rg.n_edges,
        "drivable_edges": int(rg.drivable.sum()),
        "walk_only_edges": int((~rg.drivable).sum()),
        "bridge_edges": int(rg.bridge.sum()),
        "bridges": int(len({str(o) for o, b in zip(rg.osm_id, rg.bridge) if b})),
        "km_total": round(float(rg.length_m.sum()) / 1000.0, 2),
        "km_by_class": {k: round(v, 2) for k, v in sorted(km_by_class.items(), key=lambda kv: -kv[1])},
        "drivable_components": nc,
        "largest_drivable_component_share": round(big, 3),
        "unobserved_edges": int(rg.unobserved.sum()),
        "unobserved_km": round(float(rg.length_m[rg.unobserved].sum()) / 1000.0, 2),
    }


def load_graph(run_dir: str | Path, snap_m: float = 15.0, unobserved_prior: float = 0.15) -> RoadGraph:
    run_dir = Path(run_dir)
    roads = gpd.read_file(run_dir / "damage" / "roads.geojson")
    crs = _metric_crs(run_dir, roads)
    bpath = run_dir / "damage" / "bridges.geojson"
    bridges = gpd.read_file(bpath) if bpath.exists() else None
    return build_graph(
        roads, crs=crs, snap_m=snap_m, unobserved_prior=unobserved_prior,
        bridge_damage=bridges, slope_path=run_dir / "rasters" / "slope.tif",
    )
