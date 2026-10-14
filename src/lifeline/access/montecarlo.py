"""Who is cut off: Monte Carlo reachability plus before/after travel times.

Each drivable edge is closed independently with probability p_close (its hit_prob, or the
unobserved prior where the radar could not see it). For every draw we label the connected
components of the open subgraph once, then a settlement is reachable if its component holds
any destination. p_cutoff is the share of draws in which it is not.

Independence between edges is an assumption: one landslide that closes two neighbouring
edges is counted as two separate events. Edges from the same OSM way are drawn together
(`correlate_ways=True`, default) because a single destroyed stretch is usually mapped as
one way that the noding split into several edges.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import numpy as np

WALK_LIMIT_MIN = 60.0  # minutes on foot to the nearest health facility
import shapely
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

from .graph import MAIN_ROADS, WALK_KMH, RoadGraph, load_graph

SNAP_MAX_M = 1500.0
CLOSED_AT = 0.5            # deterministic "after" scenario: edges with p_close >= this are closed
WALK_DAMAGED_FACTOR = 3.0  # walking across a damaged road (not a bridge) takes three times as long
DEST_HEALTH_KINDS = ("hospital", "clinic")


@dataclass
class Destinations:
    basis: str                     # "health", "town", "area_edge"
    names: list[str]
    nodes: np.ndarray              # drive graph node per destination
    leg_drive_min: np.ndarray      # minutes from the facility point to its node
    walk_nodes: np.ndarray
    leg_walk_min: np.ndarray
    note: str = ""


@dataclass
class AccessResult:
    graph: RoadGraph
    places: gpd.GeoDataFrame       # EPSG:4326, original properties plus computed columns
    dest: Destinations
    n_draws: int
    seed: int
    reach: np.ndarray              # (n_draws, n_places) bool, False for places without access
    has_road: np.ndarray           # snapped to a drivable road within SNAP_MAX_M
    connected_before: np.ndarray   # reachable with every edge open
    sett_node: np.ndarray          # drive node, -1 if none
    open_after: np.ndarray         # (E,) bool, deterministic after scenario
    route_edges: list = field(default_factory=list)   # pre-flood best route edge ids per place
    timings: dict = field(default_factory=dict)


# ---------------------------------------------------------------- helpers

def place_name(row) -> str:
    for k in ("name", "name_en", "name_ne"):
        v = row.get(k) if hasattr(row, "get") else None
        if isinstance(v, str) and v.strip():
            return v.strip()
    return f"unnamed {row.get('place') or 'place'} {row.get('osm_id') or ''}".strip()


def _undirected_csr(n, u, v, w):
    """CSR with both directions; parallel edges keep the minimum weight (coo would sum them)."""
    a = np.concatenate([u, v])
    b = np.concatenate([v, u])
    ww = np.concatenate([w, w])
    order = np.lexsort((ww, b, a))
    a, b, ww = a[order], b[order], ww[order]
    first = np.ones(len(a), dtype=bool)
    first[1:] = (a[1:] != a[:-1]) | (b[1:] != b[:-1])
    return csr_matrix((ww[first], (a[first], b[first])), shape=(n, n))


def snap_points(rg: RoadGraph, pts_xy: np.ndarray, edge_mask: np.ndarray, max_m: float = SNAP_MAX_M,
                mode: str = "drive"):
    """Snap points to the nearest edge in edge_mask, then to the nearer end of that edge.
    Returns node (-1 if nothing within max_m), access-leg minutes, and snap distance."""
    n = len(pts_xy)
    node = -np.ones(n, dtype=int)
    leg = np.full(n, np.inf)
    dist = np.full(n, np.inf)
    eids = np.flatnonzero(edge_mask)
    if len(eids) == 0 or n == 0:
        return node, leg, dist
    tree = shapely.STRtree(rg.geoms[eids])
    pts = shapely.points(pts_xy)
    pi, ti = tree.query_nearest(pts, max_distance=max_m, return_distance=False, all_matches=False)
    for p, t in zip(pi, ti):
        e = eids[t]
        gm = rg.geoms[e]
        d_perp = float(shapely.distance(pts[p], gm))
        pos = float(shapely.line_locate_point(gm, pts[p]))
        L = gm.length
        # which end is nearer along the road; geometry start matches node u up to snapping
        start_xy = np.array(gm.coords[0])
        u_is_start = np.hypot(*(rg.nodes_xy[rg.u[e]] - start_xy)) <= np.hypot(*(rg.nodes_xy[rg.v[e]] - start_xy))
        along_u = pos if u_is_start else L - pos
        along = min(along_u, L - along_u)
        node[p] = rg.u[e] if along_u <= L - along_u else rg.v[e]
        per_m_edge = (rg.drive_min[e] if mode == "drive" else rg.walk_min[e]) / max(L, 1e-6)
        leg[p] = d_perp / 1000.0 / WALK_KMH * 60.0 + along * per_m_edge
        dist[p] = d_perp
    return node, leg, dist


def _dijkstra_from_dests(rg, weights, mask, dest_nodes, dest_leg):
    """Shortest time from any destination to every node. Returns time, nearest-destination
    index per node (-1 if unreachable), and predecessors for route recovery."""
    N = rg.n_nodes
    S = N  # super source
    u = np.concatenate([rg.u[mask], np.full(len(dest_nodes), S)])
    v = np.concatenate([rg.v[mask], dest_nodes])
    w = np.concatenate([weights[mask], dest_leg]) + 1e-6
    good = np.isfinite(w) & (v >= 0)
    G = _undirected_csr(N + 1, u[good], v[good], w[good])
    dist, pred = dijkstra(G, directed=False, indices=S, return_predecessors=True)
    # which destination each node's route starts from: walk up the predecessor tree once
    first = -np.ones(N + 1, dtype=int)
    dn_to_idx = {}
    for i, dn in enumerate(dest_nodes):
        if dn >= 0 and (dn not in dn_to_idx or dest_leg[i] < dest_leg[dn_to_idx[dn]]):
            dn_to_idx[dn] = i
    order = np.argsort(dist[:N])
    for x in order:
        if not np.isfinite(dist[x]):
            break
        p = pred[x]
        if p == S:
            first[x] = dn_to_idx.get(x, -1)
        elif p >= 0:
            first[x] = first[p]
    return dist[:N], first[:N], pred[:N]


def _edge_lookup(rg, mask, weights):
    best = {}
    for e in np.flatnonzero(mask):
        k = (min(rg.u[e], rg.v[e]), max(rg.u[e], rg.v[e]))
        if k not in best or weights[e] < weights[best[k]]:
            best[k] = e
    return best


def _route(pred, node, lookup, N):
    edges = []
    x = node
    guard = 0
    while x >= 0 and x < N and guard < 100000:
        p = pred[x]
        if p < 0 or p >= N:
            break
        e = lookup.get((min(x, p), max(x, p)))
        if e is not None:
            edges.append(e)
        x = p
        guard += 1
    return edges


# ---------------------------------------------------------------- destinations

def _read(path: Path) -> gpd.GeoDataFrame | None:
    if path.exists():
        g = gpd.read_file(path)
        if g.crs is None:
            g = g.set_crs(4326)
        return g
    return None


def _xy(gdf: gpd.GeoDataFrame, crs: str) -> np.ndarray:
    g = gdf.to_crs(crs)
    return shapely.get_coordinates(g.geometry.representative_point().values)


def find_destinations(rg: RoadGraph, run_dir: Path, places: gpd.GeoDataFrame | None,
                      health_kinds=DEST_HEALTH_KINDS) -> Destinations:
    drive = rg.drivable
    walk = np.ones(rg.n_edges, dtype=bool)
    aoi = _read(run_dir / "aoi.geojson")
    aoi_poly = aoi.to_crs(rg.crs).union_all() if aoi is not None and len(aoi) else None

    def build(gdf, basis, note=""):
        xy = _xy(gdf, rg.crs)
        if aoi_poly is not None:
            inside = shapely.contains_xy(aoi_poly.buffer(1.0), xy[:, 0], xy[:, 1])
            gdf, xy = gdf[inside], xy[inside]
        n, leg, _ = snap_points(rg, xy, drive, mode="drive")
        wn, wleg, _ = snap_points(rg, xy, walk, mode="walk")
        ok = n >= 0
        if not ok.any():
            return None
        names = [place_name(r) for _, r in gdf.iterrows()]
        return Destinations(basis, [nm for nm, k in zip(names, ok) if k], n[ok], leg[ok], wn[ok], wleg[ok], note)

    health = _read(run_dir / "osm" / "health.geojson")
    if health is not None and len(health):
        kind = None
        for col in ("kind", "amenity", "healthcare"):
            if col in health.columns:
                kind = health[col].astype(str).str.lower() if kind is None else kind.where(kind.isin(health_kinds), health[col].astype(str).str.lower())
        sel = health[kind.isin(health_kinds)] if kind is not None else health
        if len(sel):
            d = build(sel, "health")
            if d is not None:
                return d
    if places is not None and len(places) and "place" in places.columns:
        towns = places[places["place"].astype(str).str.lower().isin(["town", "city"])]
        if len(towns):
            d = build(towns, "town", "no hospital or clinic with road access inside the area; using towns")
            if d is not None:
                return d
    # fallback: where main roads leave the area
    boundary = aoi_poly.boundary if aoi_poly is not None else shapely.convex_hull(
        shapely.multipoints(rg.nodes_xy)).boundary
    xy = rg.nodes_xy
    near = shapely.distance(shapely.points(xy), boundary) <= 500.0
    main_nodes = set()
    for e in np.flatnonzero(drive & np.isin(rg.highway, list(MAIN_ROADS))):
        main_nodes.update((int(rg.u[e]), int(rg.v[e])))
    cand = [i for i in np.flatnonzero(near) if i in main_nodes]
    if not cand:
        deg = np.bincount(np.concatenate([rg.u[drive], rg.v[drive]]), minlength=rg.n_nodes)
        cand = list(np.flatnonzero(near & (deg == 1)))
    cand = np.array(cand, dtype=int)
    names = []
    for c in cand:
        es = np.flatnonzero(drive & ((rg.u == c) | (rg.v == c)))
        nm = next((rg.name[e] for e in es if rg.name[e]), None) or (rg.highway[es[0]] + " road" if len(es) else "road")
        names.append(f"edge of the area on {nm}")
    zeros = np.zeros(len(cand))
    return Destinations("area_edge", names, cand, zeros, cand.copy(), zeros,
                        "no hospital, clinic or town found inside the area; destination is the edge of the area along the main road")


# ---------------------------------------------------------------- main analysis

def simulate(rg: RoadGraph, sett_node: np.ndarray, dest_nodes: np.ndarray, n_draws: int = 1000,
             seed: int = 7, correlate_ways: bool = True, edge_mask: np.ndarray | None = None) -> np.ndarray:
    """(n_draws, n_settlements) bool: True where the settlement reaches any destination."""
    mask = rg.drivable if edge_mask is None else edge_mask
    eids = np.flatnonzero(mask)
    u, v, p = rg.u[eids], rg.v[eids], rg.p_close[eids]
    rng = np.random.default_rng(seed)
    if correlate_ways:
        keys = np.array([f"{o}|{b}" for o, b in zip(rg.osm_id[eids], rg.bridge[eids])], dtype=object)
        _, grp = np.unique(keys, return_inverse=True)
        # one draw per way; p of the way is the max p of its edges
        gp = np.zeros(grp.max() + 1 if len(grp) else 0)
        np.maximum.at(gp, grp, p)
        # edges with lower p than the way max are opened independently given the way is hit
        rel = np.where(gp[grp] > 0, p / np.maximum(gp[grp], 1e-12), 0.0)
    N = rg.n_nodes
    ok = sett_node >= 0
    sn = sett_node[ok]
    dn = dest_nodes[dest_nodes >= 0]
    out = np.zeros((n_draws, len(sett_node)), dtype=bool)
    ones = np.ones(len(eids), dtype=np.int8)
    for k in range(n_draws):
        if correlate_ways:
            way_hit = rng.random(len(gp)) < gp
            closed = way_hit[grp] & (rng.random(len(eids)) < rel)
        else:
            closed = rng.random(len(eids)) < p
        op = ~closed
        G = coo_matrix((ones[op], (u[op], v[op])), shape=(N, N))
        _, lab = connected_components(G, directed=False)
        good = np.zeros(lab.max() + 1, dtype=bool)
        good[lab[dn]] = True
        out[k, ok] = good[lab[sn]]
    return out


def analyse(run_dir: str | Path, n_draws: int = 1000, seed: int = 7, unobserved_prior: float = 0.15,
            snap_m: float = 15.0, graph: RoadGraph | None = None) -> AccessResult:
    run_dir = Path(run_dir)
    t0 = time.perf_counter()
    rg = graph or load_graph(run_dir, snap_m=snap_m, unobserved_prior=unobserved_prior)
    t_graph = time.perf_counter() - t0

    places = _read(run_dir / "osm" / "places.geojson")
    if places is None:
        places = gpd.GeoDataFrame({"name": []}, geometry=[], crs=4326)
    places = places.to_crs(4326).reset_index(drop=True)
    dest = find_destinations(rg, run_dir, places)

    xy = _xy(places, rg.crs) if len(places) else np.zeros((0, 2))
    walk_mask = np.ones(rg.n_edges, dtype=bool)
    s_node, s_leg, s_dist = snap_points(rg, xy, rg.drivable, mode="drive")
    w_node, w_leg, _ = snap_points(rg, xy, walk_mask, mode="walk")
    has_road = s_node >= 0

    # deterministic before / after (vehicles)
    open_after = rg.drivable & (rg.p_close < CLOSED_AT)
    d_bef, near_bef, pred_bef = _dijkstra_from_dests(rg, rg.drive_min, rg.drivable, dest.nodes, dest.leg_drive_min)
    d_aft, near_aft, _ = _dijkstra_from_dests(rg, rg.drive_min, open_after, dest.nodes, dest.leg_drive_min)

    # walking after: every way, damaged bridges impassable, damaged roads slow
    damaged = rg.p_close >= CLOSED_AT
    walk_ok = ~(damaged & rg.bridge)
    walk_w = np.where(damaged, rg.walk_min * WALK_DAMAGED_FACTOR, rg.walk_min)
    d_walk, near_walk, _ = _dijkstra_from_dests(rg, walk_w, walk_ok, dest.walk_nodes, dest.leg_walk_min)

    def at(arr, nodes, legs):
        out = np.full(len(nodes), np.inf)
        k = nodes >= 0
        out[k] = arr[nodes[k]] + legs[k]
        return out

    min_before = at(d_bef, s_node, s_leg)
    min_after = at(d_aft, s_node, s_leg)
    walk_after = at(d_walk, w_node, w_leg)
    connected_before = np.isfinite(min_before)

    lookup = _edge_lookup(rg, rg.drivable, rg.drive_min)
    routes = [(_route(pred_bef, n, lookup, rg.n_nodes) if c else []) for n, c in zip(s_node, connected_before)]

    t1 = time.perf_counter()
    sim_nodes = np.where(connected_before, s_node, -1)
    reach = simulate(rg, sim_nodes, dest.nodes, n_draws=n_draws, seed=seed)
    t_mc = time.perf_counter() - t1

    p_cut = np.where(connected_before, 1.0 - reach.mean(axis=0) if n_draws else np.nan, np.nan)
    pl = places.copy()
    pl["settlement_name"] = [place_name(r) for _, r in places.iterrows()]
    pl["p_cutoff"] = p_cut
    pl["cutoff"] = connected_before & (np.nan_to_num(p_cut) >= 0.5)
    pl["nearest_health"] = [
        (dest.names[near_bef[n]] if (n >= 0 and near_bef[n] >= 0) else None) for n in s_node
    ]
    pl["nearest_health_after"] = [
        (dest.names[near_aft[n]] if (n >= 0 and near_aft[n] >= 0) else None) for n in s_node
    ]
    r1 = lambda a: [round(float(x), 1) if np.isfinite(x) else None for x in a]  # noqa: E731
    pl["minutes_before"] = r1(min_before)
    pl["minutes_after"] = r1(min_after)
    pl["walk_minutes_after"] = r1(walk_after)
    # a place whose road is cut but that can walk to care within an hour is not cut off
    # from care; rescuers need it listed separately, not mixed with the truly isolated
    walkable = np.isfinite(np.asarray(walk_after, dtype=float)) & (np.asarray(walk_after, dtype=float) <= WALK_LIMIT_MIN)
    road_cut = connected_before & (np.nan_to_num(p_cut) >= 0.5)
    pl["cutoff"] = road_cut & ~walkable
    pl["access_status"] = np.select(
        [~connected_before, road_cut & walkable, road_cut, connected_before],
        ["no_road_before", "road_cut_walkable", "cut_off", "connected"],
        default="no_road_before",
    )
    pl["snap_m"] = [round(float(d), 0) if np.isfinite(d) else None for d in s_dist]
    pl["route_uses_unobserved"] = [bool(any(rg.unobserved[e] for e in r)) for r in routes]
    pl["destination_basis"] = dest.basis

    return AccessResult(
        graph=rg, places=pl, dest=dest, n_draws=n_draws, seed=seed, reach=reach, has_road=has_road,
        connected_before=connected_before, sett_node=s_node, open_after=open_after, route_edges=routes,
        timings={"graph_s": round(t_graph, 2), "montecarlo_s": round(t_mc, 2)},
    )
