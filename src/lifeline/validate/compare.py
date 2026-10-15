"""Compare a Bato run with the Copernicus EMS EMSR927 grading maps.

The EMS layers are reference data for checking only. This module reads them from
data/reference/emsr927/ and writes only to runs/<id>/validation/.

    compare_run(run_dir, ref_dir=None, stage="first", n_boot=1000, seed=0) -> dict

writes runs/<id>/validation/emsr927.json and emsr927.md (stage="first", the first
grading) or emsr927_latest.json/.md (stage="latest", the last monitoring update).

All metrics are computed inside each delivered EMS AOI polygon, minus the areas
EMS marked as not analysed. AOIs that EMS cancelled are listed with their status.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import pandas as pd
import rasterio
from affine import Affine
from rasterio import features as rfeatures
from shapely.geometry import LineString, box
from shapely.ops import substring, unary_union

from .fetch_ems import CREDIT, DEFAULT_OUT

OUR_FLAGGED = ("destroyed_likely", "damaged_possible")
OUR_STATUSES = ("destroyed_likely", "damaged_possible", "clear", "unobserved")
EMS_AFFECTED = ("destroyed", "damaged")
EMS_GRADES = ("destroyed", "damaged", "possibly_damaged", "no_visible_damage", "not_analysed")

BUILDING_TOL_M = 15.0
ROAD_BUFFER_M = 20.0
BRIDGE_TOL_M = 30.0
BLOCK_M = 200.0
MIN_COVER = 0.05  # an AOI needs this share inside the run grid to be compared


# ---------------------------------------------------------------- small helpers

def _ratio(num, den):
    return None if not den else float(num) / float(den)


def _r(x, nd=4):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), nd)


def wilson_ci(k: int, n: int, z: float = 1.96):
    """95% Wilson interval for a proportion k/n. None if n == 0."""
    if not n:
        return None
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, c - h), 4), round(min(1.0, c + h), 4)]


def _read_vec(path: Path, crs) -> gpd.GeoDataFrame | None:
    if not path.exists():
        return None
    g = gpd.read_file(path)
    if g.crs is None:
        g = g.set_crs(4326)
    g = g[g.geometry.notna() & ~g.geometry.is_empty]
    return g.to_crs(crs)


# ---------------------------------------------------------------- event extent

def confusion_counts(pred: np.ndarray, ref: np.ndarray, valid: np.ndarray) -> dict:
    p, r, v = pred.astype(bool), ref.astype(bool), valid.astype(bool)
    return {
        "tp": int((p & r & v).sum()), "fp": int((p & ~r & v).sum()),
        "fn": int((~p & r & v).sum()), "tn": int((~p & ~r & v).sum()),
    }


def scores(tp, fp, fn, tn=0) -> dict:
    return {
        "iou": _ratio(tp, tp + fp + fn),
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "f1": _ratio(2 * tp, 2 * tp + fp + fn),
    }


def block_counts(pred, ref, valid, block_px: int) -> np.ndarray:
    """Per-block [tp, fp, fn, tn] for blocks that hold at least one valid pixel."""
    h, w = pred.shape
    rows, cols = np.nonzero(valid)
    if rows.size == 0:
        return np.zeros((0, 4), dtype=np.int64)
    nbx = (w + block_px - 1) // block_px
    bid = (rows // block_px) * nbx + (cols // block_px)
    p = pred[rows, cols].astype(bool)
    r = ref[rows, cols].astype(bool)
    n = int(bid.max()) + 1
    out = np.stack([
        np.bincount(bid, weights=(p & r), minlength=n),
        np.bincount(bid, weights=(p & ~r), minlength=n),
        np.bincount(bid, weights=(~p & r), minlength=n),
        np.bincount(bid, weights=(~p & ~r), minlength=n),
    ], axis=1).astype(np.int64)
    return out[out.sum(axis=1) > 0]


def bootstrap_ci(blocks: np.ndarray, n_boot: int = 1000, seed: int = 0) -> dict:
    """Block bootstrap 95% intervals for IoU, precision, recall and F1."""
    if len(blocks) < 2 or n_boot <= 0:
        return {k: None for k in ("iou", "precision", "recall", "f1")}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(blocks), size=(n_boot, len(blocks)))
    s = blocks[idx].sum(axis=1).astype(float)  # (n_boot, 4)
    tp, fp, fn = s[:, 0], s[:, 1], s[:, 2]
    with np.errstate(invalid="ignore", divide="ignore"):
        vals = {
            "iou": tp / (tp + fp + fn), "precision": tp / (tp + fp),
            "recall": tp / (tp + fn), "f1": 2 * tp / (2 * tp + fp + fn),
        }
    out = {}
    for k, v in vals.items():
        v = v[np.isfinite(v)]
        out[k] = None if v.size == 0 else [round(float(np.percentile(v, 2.5)), 4),
                                           round(float(np.percentile(v, 97.5)), 4)]
    return out


def extent_metrics(pred, ref, valid, block_px: int, n_boot: int = 1000, seed: int = 0) -> dict:
    c = confusion_counts(pred, ref, valid)
    s = scores(**c)
    blocks = block_counts(pred, ref, valid, block_px)
    return {
        "pixels": c, **{k: _r(v) for k, v in s.items()},
        "ci95": bootstrap_ci(blocks, n_boot, seed), "n_blocks": int(len(blocks)),
    }, blocks


def _pooled_extent(blocks_list, n_boot, seed):
    blocks = np.concatenate([b for b in blocks_list if len(b)]) if any(len(b) for b in blocks_list) else np.zeros((0, 4), np.int64)
    tot = blocks.sum(axis=0) if len(blocks) else np.zeros(4, np.int64)
    c = dict(zip(("tp", "fp", "fn", "tn"), (int(x) for x in tot)))
    s = scores(**c)
    return {"pixels": c, **{k: _r(v) for k, v in s.items()},
            "ci95": bootstrap_ci(blocks, n_boot, seed), "n_blocks": int(len(blocks))}


# ---------------------------------------------------------------- buildings

def building_metrics(ours: gpd.GeoDataFrame | None, ems: gpd.GeoDataFrame, area,
                     tol_m: float = BUILDING_TOL_M) -> dict:
    """ours and ems must share a metric CRS. area is the analysed polygon in that CRS."""
    if ours is None:
        return {"status": "missing damage/buildings.geojson"}
    ems = ems[ems.intersects(area)].copy()
    ours = ours[ours.intersects(area)].copy()
    ours["status"] = ours["status"].fillna("unobserved").astype(str)
    res = {"n_ems": int(len(ems)), "n_ours_in_area": int(len(ours)),
           "n_ours_flagged_in_area": int(ours["status"].isin(OUR_FLAGGED).sum()),
           "match_tolerance_m": tol_m}
    if ems.empty:
        res["status"] = "no EMS built-up records in this area"
        return res
    ems = ems.reset_index(drop=True)
    ems["_eid"] = np.arange(len(ems))
    if ours.empty:
        ems["our_status"] = "no_osm_match"
    else:
        o = ours[["status", "geometry"]].reset_index(drop=True)
        j = gpd.sjoin_nearest(ems[["_eid", "grade", "geometry"]], o, how="left",
                              max_distance=tol_m, distance_col="_d")
        # if several of ours are at the same distance, prefer the more severe status
        sev = {s: i for i, s in enumerate(OUR_STATUSES)}
        j["_sev"] = j["status"].map(sev).fillna(99)
        j = j.sort_values(["_eid", "_d", "_sev"]).drop_duplicates("_eid")
        ems = ems.merge(j[["_eid", "status"]], on="_eid", how="left")
        ems["our_status"] = ems["status"].fillna("no_osm_match")
    cols = list(OUR_STATUSES) + ["no_osm_match"]
    cm = (pd.crosstab(ems["grade"], ems["our_status"])
          .reindex(index=[g for g in EMS_GRADES], columns=cols, fill_value=0))
    res["confusion"] = {g: {c: int(cm.loc[g, c]) for c in cols} for g in cm.index if cm.loc[g].sum() > 0}

    def rate(grades, exclude_unobserved: bool):
        sub = ems[ems["grade"].isin(grades) & (ems["our_status"] != "no_osm_match")]
        if exclude_unobserved:
            sub = sub[sub["our_status"] != "unobserved"]
        k, n = int(sub["our_status"].isin(OUR_FLAGGED).sum()), int(len(sub))
        return {"flagged": k, "n": n, "rate": _r(_ratio(k, n)), "ci95": wilson_ci(k, n)}

    res["hit_rate_destroyed_damaged"] = rate(EMS_AFFECTED, False)
    res["hit_rate_destroyed_damaged_observed_only"] = rate(EMS_AFFECTED, True)
    res["hit_rate_destroyed"] = rate(("destroyed",), False)
    res["flag_rate_possibly_damaged"] = rate(("possibly_damaged",), False)
    fa = rate(("no_visible_damage",), False)
    res["false_alarm_rate_no_visible_damage"] = fa
    if fa["n"] == 0:
        res["false_alarm_note"] = ("EMS lists no buildings graded no_visible_damage that match an OSM "
                                   "building here, so the false alarm rate cannot be computed.")
    # our flagged buildings with no EMS record nearby: EMS mostly maps affected buildings,
    # so these are either false alarms or buildings EMS did not record.
    flagged = ours[ours["status"].isin(OUR_FLAGGED)]
    if len(flagged):
        near = gpd.sjoin_nearest(flagged[["geometry"]].reset_index(drop=True),
                                 ems[["geometry"]], how="left", max_distance=tol_m)
        near = near[~near.index.duplicated()]
        res["ours_flagged_without_ems_record"] = int(near["index_right"].isna().sum())
    else:
        res["ours_flagged_without_ems_record"] = 0
    res["n_ems_without_osm_match"] = int((ems["our_status"] == "no_osm_match").sum())
    return res


# ---------------------------------------------------------------- roads

def _lines_only(g: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    return g[g.geom_type.isin(["LineString", "MultiLineString"])]


def road_metrics(ours: gpd.GeoDataFrame | None, ems_lines: gpd.GeoDataFrame, area,
                 buffer_m: float = ROAD_BUFFER_M) -> dict:
    """Length of our roads inside buffers of EMS graded road lines, by EMS grade and our status.

    Where buffers of different grades overlap, the more severe EMS grade wins.
    """
    if ours is None:
        return {"status": "missing damage/roads.geojson"}
    ems_lines = _lines_only(ems_lines)
    ems_lines = ems_lines[ems_lines.intersects(area)]
    res = {"buffer_m": buffer_m,
           "ems_length_m": {g: round(float(ems_lines.loc[ems_lines.grade == g].intersection(area).length.sum()), 1)
                            for g in EMS_GRADES if (ems_lines.grade == g).any()}}
    if ems_lines.empty:
        res["status"] = "no EMS road lines in this area"
        return res
    ours = ours[ours.intersects(area)].copy()
    ours["status"] = ours["status"].fillna("unobserved").astype(str)
    taken = None
    L = {}
    for g in EMS_GRADES:
        sel = ems_lines[ems_lines.grade == g]
        if sel.empty:
            continue
        zone = unary_union(sel.buffer(buffer_m).values).intersection(area)
        if taken is not None:
            zone = zone.difference(taken)
        taken = zone if taken is None else taken.union(zone)
        row = {}
        for s in OUR_STATUSES:
            part = ours[ours.status == s]
            row[s] = round(float(part.intersection(zone).length.sum()), 1) if len(part) else 0.0
        L[g] = row
    res["length_m"] = L

    def agree(affected_grades):
        aff_aff = sum(L.get(g, {}).get(s, 0) for g in affected_grades for s in OUR_FLAGGED)
        aff_clear = sum(L.get(g, {}).get("clear", 0) for g in affected_grades)
        nvd_aff = sum(L.get("no_visible_damage", {}).get(s, 0) for s in OUR_FLAGGED)
        nvd_clear = L.get("no_visible_damage", {}).get("clear", 0)
        tot = aff_aff + aff_clear + nvd_aff + nvd_clear
        return {
            "agreement": _r(_ratio(aff_aff + nvd_clear, tot)),
            "recall_length": _r(_ratio(aff_aff, aff_aff + aff_clear)),
            "false_alarm_length": _r(_ratio(nvd_aff, nvd_aff + nvd_clear)),
            "compared_length_m": round(tot, 1),
        }

    res["destroyed_damaged_vs_no_visible_damage"] = agree(EMS_AFFECTED)
    res["including_possibly_damaged"] = agree(EMS_AFFECTED + ("possibly_damaged",))
    res["unobserved_length_m"] = round(sum(r.get("unobserved", 0) for r in L.values()), 1)
    return res


# ---------------------------------------------------------------- bridges

def bridge_metrics(our_bridges: gpd.GeoDataFrame | None, ems_bridges: gpd.GeoDataFrame, area,
                   tol_m: float = BRIDGE_TOL_M, to_wgs84=None) -> dict:
    ems = ems_bridges[ems_bridges.grade.isin(EMS_AFFECTED) & ems_bridges.intersects(area)].reset_index(drop=True)
    res = {"n_ems_destroyed_damaged": int(len(ems)), "tolerance_m": tol_m}
    if ems.empty:
        res.update(flagged=0, matched_osm=0, text="0/0")
        return res
    if our_bridges is None or our_bridges.empty:
        res.update(flagged=0, matched_osm=0, text=f"0/{len(ems)}",
                   status="no OSM bridges in this run")
        return res
    ob = our_bridges.reset_index(drop=True).copy()
    ob["status"] = ob["status"].fillna("unobserved").astype(str)
    if "osm_id" not in ob:
        ob["osm_id"] = ""
    details = []
    flagged = matched = 0
    sidx = ob.sindex
    for _, e in ems.iterrows():
        cand = ob.iloc[sidx.query(e.geometry.buffer(tol_m), predicate="intersects")]
        is_flag = bool(cand["status"].isin(OUR_FLAGGED).any()) if len(cand) else False
        matched += int(len(cand) > 0)
        flagged += int(is_flag)
        c = e.geometry.centroid
        if to_wgs84 is not None:
            c = to_wgs84(c)
        details.append({
            "ems_grade": e.grade, "lon": round(c.x, 6), "lat": round(c.y, 6),
            "osm_ids": [str(x) for x in cand["osm_id"].tolist()],
            "our_status": sorted(set(cand["status"])) if len(cand) else ["no_osm_bridge"],
            "flagged": is_flag,
        })
    res.update(flagged=flagged, matched_osm=matched, text=f"{flagged}/{len(ems)}", details=details)
    return res


# ---------------------------------------------------------------- cut-off check

def _node(pt, nd=1):
    return (round(pt[0], nd), round(pt[1], nd))


def build_graph(roads: gpd.GeoDataFrame, removed_zone=None, seg_m: float = 50.0,
                min_overlap_frac: float = 0.6):
    """Noded road graph, cut into pieces of at most seg_m metres.

    A piece is left out of the post-event graph when at least min_overlap_frac of its
    length lies inside removed_zone. Short pieces keep a road that only touches the
    zone at a junction (a crossing road) in the graph.
    """
    merged = unary_union(_lines_only(roads).geometry.values)
    pieces = list(getattr(merged, "geoms", [merged]))
    pre, post = nx.Graph(), nx.Graph()
    n_removed = 0
    for line in pieces:
        if line.is_empty or line.length == 0:
            continue
        n = max(1, int(math.ceil(line.length / seg_m)))
        step = line.length / n
        for i in range(n):
            p = substring(line, i * step, (i + 1) * step) if n > 1 else line
            if p.is_empty or p.length == 0 or p.geom_type != "LineString":
                continue
            coords = list(p.coords)
            a, b = _node(coords[0]), _node(coords[-1])
            pre.add_edge(a, b, weight=p.length)
            drop = False
            if removed_zone is not None and not removed_zone.is_empty and p.intersects(removed_zone):
                drop = p.intersection(removed_zone).length >= min_overlap_frac * p.length
            if drop:
                n_removed += 1
                post.add_nodes_from([a, b])
            else:
                post.add_edge(a, b, weight=p.length)
    return pre, post, n_removed


def _snap(nodes_xy: np.ndarray, nodes: list, pt, max_m):
    if not len(nodes):
        return None
    d = np.hypot(nodes_xy[:, 0] - pt.x, nodes_xy[:, 1] - pt.y)
    i = int(np.argmin(d))
    return nodes[i] if d[i] <= max_m else None


def cutoff_check(roads, places, ours_cutoff, health, removed_zone, aoi_union,
                 snap_m: float = 500.0) -> dict:
    """Recompute cut-off settlements with EMS destroyed roads removed and compare with ours.

    A settlement is cut off if before the event it could reach a health facility by road
    and afterwards it cannot. Without health facilities, the hub is the largest pre-event
    road component.
    """
    pre, post, n_removed = build_graph(roads, removed_zone)
    nodes = list(pre.nodes)
    xy = np.array(nodes, dtype=float) if nodes else np.zeros((0, 2))
    hubs = set()
    hub_kind = "health facilities"
    if health is not None and len(health):
        for g in health.geometry:
            n = _snap(xy, nodes, g.centroid, 1000.0)
            if n is not None:
                hubs.add(n)
    if not hubs and nodes:
        hub_kind = "largest pre-event road component"
        hubs = set(max(nx.connected_components(pre), key=len))
    pre_dist, pre_paths = nx.multi_source_dijkstra(pre, hubs) if hubs else ({}, {})
    post_reach = set()
    for comp in nx.connected_components(post):
        if comp & hubs:
            post_reach |= comp

    rows = []
    for _, p in places.iterrows():
        name = p.get("name") or str(p.get("osm_id", ""))
        n = _snap(xy, nodes, p.geometry.centroid, snap_m)
        ours = ours_cutoff.get(str(p.get("osm_id")), None)
        if n is None:
            rows.append({"name": name, "ref": None, "ours": ours, "note": "not on road network"})
            continue
        if n not in pre_dist:
            rows.append({"name": name, "ref": None, "ours": ours, "note": "no road link to a hub before the event"})
            continue
        ref = n not in post_reach
        path = pre_paths.get(n, [])
        through = bool(len(path) > 1 and aoi_union is not None and LineString(path).intersects(aoi_union))
        rows.append({"name": name, "ref": ref, "ours": ours, "testable": through})

    def summarise(rs):
        rs = [r for r in rs if r["ref"] is not None and r["ours"] is not None]
        both_cut = sum(1 for r in rs if r["ref"] and r["ours"])
        both_ok = sum(1 for r in rs if not r["ref"] and not r["ours"])
        return {
            "n": len(rs), "agree": both_cut + both_ok, "both_cut_off": both_cut, "both_connected": both_ok,
            "ref_only_cut_off": sorted(r["name"] for r in rs if r["ref"] and not r["ours"]),
            "ours_only_cut_off": sorted(r["name"] for r in rs if r["ours"] and not r["ref"]),
            "ref_cut_off": sum(1 for r in rs if r["ref"]), "ours_cut_off": sum(1 for r in rs if r["ours"]),
        }

    return {
        "hub": hub_kind, "n_hubs": len(hubs), "road_pieces_removed": n_removed,
        "all_settlements": summarise(rows),
        "settlements_whose_route_crosses_an_ems_aoi": summarise([r for r in rows if r.get("testable")]),
        "excluded": [{"name": r["name"], "note": r.get("note", "no Bato cutoff value")}
                     for r in rows if r["ref"] is None or r["ours"] is None],
        "note": ("EMS graded roads only inside its AOIs. Damage outside them is unknown to the "
                 "reference, so reference cut-offs are a lower bound. The subset whose pre-event "
                 "route to the nearest hub crosses an EMS AOI is the fairer comparison."),
    }


# ---------------------------------------------------------------- run-level driver

def _load_grid(run_dir: Path):
    gj = run_dir / "grid.json"
    if gj.exists():
        g = json.loads(gj.read_text())
        return g["crs"], Affine(*g["transform"][:6]), int(g["width"]), int(g["height"]), float(g.get("pixel_m", 20))
    with rasterio.open(run_dir / "rasters" / "flood_class.tif") as src:
        return src.crs.to_string(), src.transform, src.width, src.height, abs(src.transform.a)


def _read_raster(path: Path, shape):
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        a = src.read(1)
    if a.shape != shape:
        raise ValueError(f"{path.name} is {a.shape}, grid is {shape}")
    return a


def _burn(geoms, transform, shape):
    geoms = [g for g in geoms if g is not None and not g.is_empty]
    if not geoms:
        return np.zeros(shape, dtype=bool)
    return rfeatures.rasterize(((g, 1) for g in geoms), out_shape=shape, transform=transform,
                               fill=0, dtype="uint8").astype(bool)


def _select_stage(feats: gpd.GeoDataFrame, stage: str) -> gpd.GeoDataFrame:
    if stage == "first":
        return feats[feats["stage"] == "first"]
    if stage == "latest":
        keep = []
        for aoi, g in feats.groupby("aoi"):
            prods = sorted(g["product"].unique(), key=lambda p: (p != "GRA_PRODUCT", p))
            keep.append(g[g["product"] == prods[-1]])
        return pd.concat(keep) if keep else feats.iloc[0:0]
    raise ValueError("stage must be 'first' or 'latest'")


def compare_run(run_dir, ref_dir=None, stage: str = "first", n_boot: int = 1000, seed: int = 0) -> dict:
    run_dir = Path(run_dir)
    ref_dir = Path(ref_dir) if ref_dir else DEFAULT_OUT
    crs, transform, width, height, pixel_m = _load_grid(run_dir)
    shape = (height, width)
    block_px = max(1, int(round(BLOCK_M / pixel_m)))
    notes = []

    feats_all = gpd.read_file(ref_dir / "features.geojson")
    for col in ("image_dates", "grade", "layer", "aoi", "product", "stage"):
        feats_all[col] = feats_all[col].fillna("").astype(str)
    aois = gpd.read_file(ref_dir / "aois.geojson").to_crs(crs)
    feats = _select_stage(feats_all, stage).to_crs(crs)

    fc = _read_raster(run_dir / "rasters" / "flood_class.tif", shape)
    fp = _read_raster(run_dir / "rasters" / "flood_prob.tif", shape)
    ob = _read_raster(run_dir / "rasters" / "observed.tif", shape)
    if fc is not None:
        pred = np.isin(fc, (1, 2))
        pred_src = "flood_class in {1, 2}"
    elif fp is not None:
        pred = np.nan_to_num(fp) >= 0.5
        pred_src = "flood_prob >= 0.5"
    else:
        pred = None
        pred_src = None
        notes.append("No flood_class.tif or flood_prob.tif in the run; event extent not compared.")
    seen = np.ones(shape, dtype=bool)
    if ob is not None:
        seen &= ob > 0
    if fc is not None:
        seen &= fc != 255
    if ob is None and fc is None:
        notes.append("No observed.tif; the observed-only variant equals the full variant.")

    our_b = _read_vec(run_dir / "damage" / "buildings.geojson", crs)
    our_r = _read_vec(run_dir / "damage" / "roads.geojson", crs)
    our_br = _read_vec(run_dir / "damage" / "bridges.geojson", crs)
    if our_r is not None and "bridge" in our_r:
        rb = our_r[our_r["bridge"].fillna(False).astype(bool)]
        our_br = pd.concat([our_br, rb]) if our_br is not None else rb
        our_br = gpd.GeoDataFrame(our_br, geometry="geometry", crs=crs)

    x0, y0 = transform @ (0, 0)
    x1, y1 = transform @ (width, height)
    grid_box = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    def to_wgs84(pt):
        return gpd.GeoSeries([pt], crs=crs).to_crs(4326).iloc[0]

    aoi_results = []
    blocks_all, blocks_obs, blocks_prob = [], [], []
    pooled_b = []
    pooled_r = []
    pooled_bridges = [0, 0]
    analysed_union = []
    for _, a in aois.sort_values("aoi").iterrows():
        entry = {"aoi": a.aoi, "name": a["name"], "status": a.status,
                 "products_available": a.products or ""}
        if a.status != "delivered":
            entry["result"] = "not compared: EMS cancelled this AOI and delivered no product"
            aoi_results.append(entry)
            continue
        f = feats[feats.aoi == a.aoi]
        if f.empty:
            entry["result"] = f"not compared: no {stage} product for this AOI"
            aoi_results.append(entry)
            continue
        entry["product_used"] = sorted(f["product"].unique().tolist())
        entry["image_dates"] = sorted(set(",".join(f["image_dates"].unique()).split(",")) - {""})
        na = f[f.layer == "not_analysed"]
        area = a.geometry
        if len(na):
            area = area.difference(unary_union(na.geometry.values))
        inside = area.intersection(grid_box)
        cover = _ratio(inside.area, area.area) or 0.0
        entry["fraction_inside_run_grid"] = _r(cover)
        entry["not_analysed_area_km2"] = _r(a.geometry.intersection(unary_union(na.geometry.values)).area / 1e6 if len(na) else 0.0)
        if cover < MIN_COVER:
            entry["result"] = (f"not compared: only {cover:.1%} of this AOI lies inside the run grid "
                               f"(minimum {MIN_COVER:.0%})")
            aoi_results.append(entry)
            continue
        if cover < 0.999:
            notes.append(f"{a.aoi}: only {cover:.0%} of the analysed AOI lies inside the run grid.")
        analysed_union.append(inside)
        entry["result"] = "compared"

        # event extent
        if pred is not None:
            valid = _burn([inside], transform, shape)
            ev = f[f.layer == "observed_event"]
            ref = _burn(list(ev.geometry), transform, shape) & valid
            m_all, b_all = extent_metrics(pred, ref, valid, block_px, n_boot, seed)
            m_obs, b_obs = extent_metrics(pred, ref, valid & seen, block_px, n_boot, seed)
            ext = {"prediction": pred_src, "all_pixels": m_all, "observed_pixels_only": m_obs,
                   "ems_event_km2": _r(ref.sum() * pixel_m * pixel_m / 1e6),
                   "ours_flooded_km2": _r((pred & valid).sum() * pixel_m * pixel_m / 1e6),
                   "observed_fraction": _r(_ratio((valid & seen).sum(), valid.sum()))}
            if fp is not None:
                pv = np.nan_to_num(fp)
                ext["mean_flood_prob_inside_ems_event"] = _r(float(pv[ref].mean()) if ref.any() else None)
                ext["mean_flood_prob_outside_ems_event"] = _r(float(pv[valid & ~ref].mean()) if (valid & ~ref).any() else None)
                if fc is not None:
                    m_p, b_p = extent_metrics(pv >= 0.5, ref, valid, block_px, n_boot, seed)
                    ext["flood_prob_ge_0.5"] = m_p
                    blocks_prob.append(b_p)
            entry["extent"] = ext
            blocks_all.append(b_all)
            blocks_obs.append(b_obs)

        bu = f[f.layer == "built_up"]
        entry["buildings"] = building_metrics(our_b, bu, inside)
        if our_b is not None and len(bu):
            pooled_b.append((bu, inside))

        tr = f[f.layer.isin(["transportation"])]
        entry["roads"] = road_metrics(our_r, tr, inside)
        if our_r is not None:
            pooled_r.append(entry["roads"])

        br = f[f.layer == "bridge"]
        entry["bridges"] = bridge_metrics(our_br, br, inside, to_wgs84=to_wgs84)
        pooled_bridges[0] += entry["bridges"]["flagged"]
        pooled_bridges[1] += entry["bridges"]["n_ems_destroyed_damaged"]
        aoi_results.append(entry)

    pooled = {}
    if blocks_all:
        pooled["extent_all_pixels"] = _pooled_extent(blocks_all, n_boot, seed)
        pooled["extent_observed_pixels_only"] = _pooled_extent(blocks_obs, n_boot, seed)
        if blocks_prob:
            pooled["extent_flood_prob_ge_0.5"] = _pooled_extent(blocks_prob, n_boot, seed)
    if pooled_b:
        allb = gpd.GeoDataFrame(pd.concat([b for b, _ in pooled_b]), geometry="geometry", crs=crs)
        pooled["buildings"] = building_metrics(our_b, allb, unary_union([g for _, g in pooled_b]))
    if pooled_r:
        L = {}
        for r in pooled_r:
            for g, row in (r.get("length_m") or {}).items():
                for s, v in row.items():
                    L.setdefault(g, {}).setdefault(s, 0.0)
                    L[g][s] += v
        aa = sum(L.get(g, {}).get(s, 0) for g in EMS_AFFECTED for s in OUR_FLAGGED)
        ac = sum(L.get(g, {}).get("clear", 0) for g in EMS_AFFECTED)
        na_ = sum(L.get("no_visible_damage", {}).get(s, 0) for s in OUR_FLAGGED)
        nc = L.get("no_visible_damage", {}).get("clear", 0)
        pooled["roads"] = {"length_m": {g: {s: round(v, 1) for s, v in row.items()} for g, row in L.items()},
                           "agreement": _r(_ratio(aa + nc, aa + ac + na_ + nc)),
                           "recall_length": _r(_ratio(aa, aa + ac)),
                           "false_alarm_length": _r(_ratio(na_, na_ + nc))}
    pooled["bridges"] = {"flagged": pooled_bridges[0], "n_ems_destroyed_damaged": pooled_bridges[1],
                         "text": f"{pooled_bridges[0]}/{pooled_bridges[1]}"}

    # cut-off check
    cut = {"status": "not run"}
    pre_roads = _read_vec(run_dir / "osm" / "roads.geojson", crs)
    if pre_roads is None and our_r is not None:
        pre_roads = our_r
        notes.append("osm/roads.geojson missing; used damage/roads.geojson geometry as the pre-event network.")
    settl = _read_vec(run_dir / "access" / "settlements.geojson", crs)
    health = _read_vec(run_dir / "osm" / "health.geojson", crs)
    if pre_roads is not None and settl is not None and len(pre_roads):
        destroyed = feats[(feats.grade == "destroyed") & feats.layer.isin(["transportation", "bridge"])]
        zones = []
        for g in destroyed.geometry:
            if g.geom_type in ("Point", "MultiPoint"):
                zones.append(g.buffer(BRIDGE_TOL_M))
            elif g.geom_type in ("LineString", "MultiLineString"):
                zones.append(g.buffer(ROAD_BUFFER_M))
            else:
                zones.append(g.buffer(5))
        zone = unary_union(zones) if zones else None
        ours_cut = {}
        if "cutoff" in settl:
            for _, s in settl.iterrows():
                v = s.get("cutoff")
                ours_cut[str(s.get("osm_id"))] = None if v is None or (isinstance(v, float) and math.isnan(v)) else bool(v)
        aoi_u = unary_union(analysed_union) if analysed_union else None
        cut = cutoff_check(pre_roads, settl, ours_cut, health, zone, aoi_u)
        # Lifeline's cut-off means no ROAD route to care, so also check on the same drivable network:
        # with footpaths and steps in the graph, a path around a destroyed highway would count as access.
        from ..access.graph import WALK_ONLY
        if "highway" in pre_roads:
            drive = pre_roads[~pre_roads["highway"].isin(WALK_ONLY)]
            cut["drivable_roads_only"] = cutoff_check(drive, settl, ours_cut, health, zone, aoi_u)
            cut["drivable_roads_only"]["note"] = ("Same check on drivable roads only (footpaths, steps and tracks for walking "
                                                  "left out), matching Lifeline's definition of road access. Added 15 Oct 2026 "
                                                  "after a review found the first version routed over footpaths.")
    else:
        cut["status"] = "missing osm/roads.geojson or access/settlements.geojson"

    out = {
        "activation": "EMSR927",
        "credit": CREDIT,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_id": run_dir.name,
        "stage": stage,
        "reference": "EMS grading maps (photo-interpretation of VHR optical images). Used only to check results.",
        "settings": {"building_tolerance_m": BUILDING_TOL_M, "road_buffer_m": ROAD_BUFFER_M,
                     "bridge_tolerance_m": BRIDGE_TOL_M, "bootstrap_block_m": BLOCK_M,
                     "bootstrap_block_px": block_px, "n_boot": n_boot, "seed": seed},
        "aois": aoi_results,
        "pooled": pooled,
        "cutoff": cut,
        "notes": notes,
    }
    vdir = run_dir / "validation"
    vdir.mkdir(exist_ok=True)
    stem = output_stem(stage)
    (vdir / f"{stem}.json").write_text(json.dumps(out, indent=1, default=_json_default))
    (vdir / f"{stem}.md").write_text(to_markdown(out))
    return out


def output_stem(stage: str) -> str:
    """emsr927 for the first grading (the canonical file), emsr927_latest for the update."""
    return "emsr927" if stage == "first" else f"emsr927_{stage}"


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


def _fmt(x, ci=None):
    if x is None:
        return "n/a"
    s = f"{x:.2f}"
    if ci:
        s += f" ({ci[0]:.2f} to {ci[1]:.2f})"
    return s


def to_markdown(res: dict) -> str:
    lines = [
        f"# Check against Copernicus EMS {res['activation']} ({res['stage']} grading)",
        "",
        f"Run `{res['run_id']}`. Reference: {res['reference']} Credit: {res['credit']}.",
        "Ranges are 95% intervals (block bootstrap over 200 m blocks for the extent, Wilson for rates).",
        "",
        "| AOI | Status | Extent IoU | Extent F1 | F1, observed pixels | Building hit rate | Road agreement | Bridges flagged |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for a in res["aois"]:
        if a.get("result") != "compared":
            lines.append(f"| {a['aoi']} {a['name']} | {a.get('result', a['status'])} | | | | | | |")
            continue
        e = a.get("extent", {})
        ea, eo = e.get("all_pixels", {}), e.get("observed_pixels_only", {})
        b = a.get("buildings", {}).get("hit_rate_destroyed_damaged", {})
        r = a.get("roads", {}).get("destroyed_damaged_vs_no_visible_damage", {})
        lines.append(
            f"| {a['aoi']} {a['name']} | compared ({', '.join(a.get('product_used', []))}) | "
            f"{_fmt(ea.get('iou'), (ea.get('ci95') or {}).get('iou'))} | "
            f"{_fmt(ea.get('f1'), (ea.get('ci95') or {}).get('f1'))} | "
            f"{_fmt(eo.get('f1'), (eo.get('ci95') or {}).get('f1'))} | "
            f"{_fmt(b.get('rate'), b.get('ci95'))} (n={b.get('n', 0)}) | "
            f"{_fmt(r.get('agreement'))} | {a.get('bridges', {}).get('text', 'n/a')} |")
    p = res.get("pooled", {})
    if p:
        lines += ["", "## All compared AOIs together", ""]
        ea = p.get("extent_all_pixels")
        if ea:
            lines.append(f"- Event extent: IoU {_fmt(ea['iou'], ea['ci95']['iou'])}, precision "
                         f"{_fmt(ea['precision'])}, recall {_fmt(ea['recall'])}, F1 {_fmt(ea['f1'], ea['ci95']['f1'])}.")
        eo = p.get("extent_observed_pixels_only")
        if eo:
            lines.append(f"- Event extent, pixels our radar observed: IoU {_fmt(eo['iou'])}, F1 {_fmt(eo['f1'], eo['ci95']['f1'])}.")
        b = p.get("buildings")
        if b and "hit_rate_destroyed_damaged" in b:
            h = b["hit_rate_destroyed_damaged"]
            fa = b["false_alarm_rate_no_visible_damage"]
            lines.append(f"- Buildings: {h['flagged']} of {h['n']} EMS destroyed or damaged buildings flagged "
                         f"(hit rate {_fmt(h['rate'], h['ci95'])}). False alarm rate on EMS no visible damage: "
                         f"{_fmt(fa['rate'])} (n={fa['n']}). {b['n_ems_without_osm_match']} EMS records had no OSM "
                         f"building within {b['match_tolerance_m']:.0f} m.")
        r = p.get("roads")
        if r:
            lines.append(f"- Roads (length weighted): agreement {_fmt(r['agreement'])}, recall on EMS destroyed or "
                         f"damaged {_fmt(r['recall_length'])}, false alarm on EMS no visible damage {_fmt(r['false_alarm_length'])}.")
        br = p.get("bridges")
        if br:
            lines.append(f"- Bridges: we flagged {br['text']} EMS destroyed or damaged bridges.")
    c = res.get("cutoff", {})
    if "all_settlements" in c:
        s, t = c["all_settlements"], c["settlements_whose_route_crosses_an_ems_aoi"]
        lines += ["", "## Cut-off check", "",
                  f"- All settlements: agree on {s['agree']} of {s['n']} (reference cut off {s['ref_cut_off']}, ours {s['ours_cut_off']}).",
                  f"- Settlements whose route crosses an EMS AOI: agree on {t['agree']} of {t['n']}.",
                  f"- {c['note']}"]
    else:
        lines += ["", f"Cut-off check: {c.get('status')}"]
    if res.get("notes"):
        lines += ["", "## Notes", ""] + [f"- {n}" for n in res["notes"]]
    return "\n".join(lines) + "\n"
