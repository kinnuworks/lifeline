"""Which repairs reconnect the most people, and which bridges were single points of failure.

Repair ranking works on the deterministic "after" network (edges with p_close >= 0.5 closed).
Candidates are closed OSM ways (a bridge or a road stretch, all of its closed edges reopened
together). Greedy: pick the candidate that reconnects the most settlements (ties broken by
people, then bridges first), reopen it, repeat. When no single candidate helps, the closed
edges along a cut-off settlement's pre-flood route are offered as one combined repair.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from ..acquire.names import DEVANAGARI, romanise
from .montecarlo import AccessResult


def _labels(rg, open_mask):
    m = open_mask
    G = coo_matrix((np.ones(m.sum(), dtype=np.int8), (rg.u[m], rg.v[m])), shape=(rg.n_nodes, rg.n_nodes))
    return connected_components(G, directed=False)[1]


def _reached(rg, lab, sett_node, dest_nodes):
    good = np.zeros(lab.max() + 1, dtype=bool)
    good[lab[dest_nodes[dest_nodes >= 0]]] = True
    out = np.zeros(len(sett_node), dtype=bool)
    k = sett_node >= 0
    out[k] = good[lab[sett_node[k]]]
    return out


def _gain(rg, lab, edges, dest_good_comp, sett_comp, eligible):
    """Settlements newly reached if `edges` are reopened, using a small union-find on labels."""
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for e in edges:
        a, b = find(int(lab[rg.u[e]])), find(int(lab[rg.v[e]]))
        if a != b:
            parent[a] = b
    if not parent:
        return np.zeros(len(sett_comp), dtype=bool)
    good_roots = {find(c) for c in parent if dest_good_comp[c]}
    out = np.zeros(len(sett_comp), dtype=bool)
    for i in np.flatnonzero(eligible):
        c = int(sett_comp[i])
        if c in parent and find(c) in good_roots:
            out[i] = True
    return out


def _population(places):
    if "population" not in places.columns:
        return None
    pop = np.array([_num(x) for x in places["population"]], dtype=float)
    return pop if np.isfinite(pop).any() else None


def _num(x):
    try:
        return float(str(x).replace(",", "").strip())
    except (TypeError, ValueError):
        return np.nan


def _candidates(rg, closed):
    groups: dict[str, list[int]] = {}
    for e in np.flatnonzero(closed):
        k = f"{rg.osm_id[e]}|{'b' if rg.bridge[e] else 'r'}"
        groups.setdefault(k, []).append(int(e))
    return list(groups.values())


def _describe(rg, edges, to4326):
    edges = list(edges)
    is_b = bool(rg.bridge[edges].any())
    main = [e for e in edges if rg.bridge[e]] or edges
    e0 = max(main, key=lambda e: rg.p_close[e])
    names = [n for n in dict.fromkeys(rg.name[edges]) if n]
    geom = shapely.union_all(rg.geoms[edges])
    pt = shapely.line_interpolate_point(shapely.line_merge(geom), 0.5, normalized=True) \
        if geom.geom_type in ("LineString", "MultiLineString") else geom.centroid
    if pt.is_empty:
        pt = geom.centroid
    lon, lat = to4326.transform(pt.x, pt.y)
    osm_ids = list(dict.fromkeys(str(o) for o in rg.osm_id[edges]))
    label = names[0] if names else f"unnamed {rg.highway[e0] or 'road'}"
    if DEVANAGARI.search(label):
        label = romanise(label)
    return {
        "osm_id": osm_ids[0] if len(osm_ids) == 1 else osm_ids[0],
        "osm_ids": osm_ids,
        "kind": "bridge" if is_b else "road",
        "name": (label + " bridge") if is_b and "bridge" not in label.lower() else label,
        "lon": round(float(lon), 6),
        "lat": round(float(lat), 6),
        "hit_prob": round(float(np.nanmax(rg.hit_prob[edges])), 3) if np.isfinite(rg.hit_prob[edges]).any() else None,
        "p_close": round(float(rg.p_close[edges].max()), 3),
        "length_m": round(float(rg.length_m[edges].sum()), 0),
        "highway": str(rg.highway[e0]),
    }


def rank_chokepoints(res: AccessResult, top: int = 10) -> tuple[list[dict], dict]:
    rg = res.graph
    to4326 = Transformer.from_crs(rg.crs, 4326, always_xy=True)
    closed = rg.drivable & ~res.open_after
    open_now = res.open_after.copy()
    dn = res.dest.nodes
    names = list(res.places["settlement_name"])
    pop = _population(res.places)
    eligible_all = res.connected_before.copy()

    cands = _candidates(rg, closed)
    out = []
    taken_edges: set[int] = set()
    for rank in range(1, top + 1):
        lab = _labels(rg, open_now)
        reached = _reached(rg, lab, res.sett_node, dn)
        eligible = eligible_all & ~reached
        if not eligible.any():
            break
        dest_good = np.zeros(lab.max() + 1, dtype=bool)
        dest_good[lab[dn[dn >= 0]]] = True
        sett_comp = np.where(res.sett_node >= 0, lab[np.maximum(res.sett_node, 0)], -1)

        def score(edges):
            g = _gain(rg, lab, edges, dest_good, sett_comp, eligible)
            people = float(np.nansum(pop[g])) if pop is not None else 0.0
            return g, (int(g.sum()), people, bool(rg.bridge[edges].any()), -len(edges))

        live = [c for c in cands if not set(c) <= taken_edges]
        best, best_key, best_g = None, None, None
        for c in live:
            g, key = score(c)
            if key[0] > 0 and (best_key is None or key > best_key):
                best, best_key, best_g = c, key, g
        # a repair of several closed stretches on one village's old route can reconnect a whole
        # valley that no single stretch can; it wins only if it reconnects strictly more villages
        combined = False
        seen_routes: set[tuple] = set()
        for i in np.flatnonzero(eligible):
            c = tuple(sorted(e for e in res.route_edges[i] if not open_now[e]))
            if len(c) < 2 or c in seen_routes:
                continue
            seen_routes.add(c)
            g, key = score(list(c))
            if key[0] > 0 and (best_key is None or key[0] > best_key[0]):
                best, best_key, best_g, combined = list(c), key, g, True
        if best is None:
            break
        d = _describe(rg, best, to4326)
        idx = np.flatnonzero(best_g)
        out.append({
            "rank": rank,
            **{k: d[k] for k in ("osm_id", "kind", "name", "lon", "lat")},
            "villages_reconnected": int(len(idx)),
            "people_reconnected": int(round(float(np.nansum(pop[idx])))) if pop is not None else None,
            "names": [names[i] for i in idx],
            "osm_ids": d["osm_ids"],
            "hit_prob": d["hit_prob"],
            "p_close": d["p_close"],
            "length_m": d["length_m"],
            "highway": d["highway"],
            "combined_repair": combined,
            "people_basis": "OSM population tags" if pop is not None else
                            "no population tags in OSM; villages_reconnected counts settlements",
        })
        open_now[best] = True
        taken_edges.update(best)

    lab = _labels(rg, open_now)
    remaining = eligible_all & ~_reached(rg, lab, res.sett_node, dn)
    info = {"still_cut_after_top_repairs": [names[i] for i in np.flatnonzero(remaining)],
            "population_tags": pop is not None}
    return out, info


def single_points_of_failure(res: AccessResult) -> list[dict]:
    """Bridges whose loss alone, on the intact pre-flood network, would cut settlements off."""
    rg = res.graph
    to4326 = Transformer.from_crs(rg.crs, 4326, always_xy=True)
    base = rg.drivable.copy()
    dn = res.dest.nodes
    lab0 = _labels(rg, base)
    reached0 = _reached(rg, lab0, res.sett_node, dn)
    names = list(res.places["settlement_name"])
    pop = _population(res.places)
    groups: dict[str, list[int]] = {}
    for e in np.flatnonzero(base & rg.bridge):
        groups.setdefault(str(rg.osm_id[e]), []).append(int(e))
    out = []
    for edges in groups.values():
        m = base.copy()
        m[edges] = False
        lost = reached0 & ~_reached(rg, _labels(rg, m), res.sett_node, dn)
        if lost.any():
            d = _describe(rg, edges, to4326)
            idx = np.flatnonzero(lost)
            out.append({
                "osm_id": d["osm_id"], "kind": "bridge", "name": d["name"], "lon": d["lon"], "lat": d["lat"],
                "villages_cut": int(len(idx)),
                "people_cut": int(round(float(np.nansum(pop[idx])))) if pop is not None else None,
                "names": [names[i] for i in idx],
                "p_close_now": d["p_close"],
            })
    out.sort(key=lambda r: (-r["villages_cut"], -(r["people_cut"] or 0)))
    return out


def write_chokepoints(chokepoints: list[dict], path: Path) -> None:
    import json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(chokepoints, indent=2, ensure_ascii=False))
