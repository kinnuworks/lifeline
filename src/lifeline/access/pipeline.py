"""Run the whole access analysis on a run folder and write the contract outputs.

Writes into runs/<id>/access/:
  settlements.geojson   per contract, plus a few extra columns (see SETTLEMENT_EXTRA)
  chokepoints.json      per contract, plus extra keys per entry
  single_points.json    bridges that alone could cut villages off on the pre-flood network
  access_summary.json   the dict from `summary()`, for the facts ledger
"""

from __future__ import annotations

import math

import json
import time
from pathlib import Path

import numpy as np

from .chokepoints import rank_chokepoints, single_points_of_failure, write_chokepoints
from .montecarlo import AccessResult, analyse

CONTRACT_PROPS = ["p_cutoff", "cutoff", "access_status", "nearest_health", "minutes_before", "minutes_after",
                  "walk_minutes_after", "reason"]
SETTLEMENT_EXTRA = ["settlement_name", "nearest_health_after", "snap_m", "route_uses_unobserved",
                    "destination_basis", "fixed_by_rank"]


def _reasons(res: AccessResult, chokepoints: list[dict]) -> list[str]:
    pl = res.places
    fixer = {}
    for c in chokepoints:
        for nm in c["names"]:
            fixer.setdefault(nm, c)
    dest_word = {"health": "a hospital or clinic", "town": "a town",
                 "area_edge": "the edge of the area along the main road"}[res.dest.basis]
    out = []
    for i, row in pl.iterrows():
        nm = row["settlement_name"]
        p = row["p_cutoff"]
        if not res.has_road[i]:
            r = "no mapped road within 1.5 km of the settlement in pre-flood OSM; no road access before the flood"
        elif not res.connected_before[i]:
            r = f"on a road, but the pre-flood OSM network has no road link from it to {dest_word}"
        elif p >= 0.9:
            r = f"cut off: no open road to {dest_word} in almost every simulated damage scenario"
        elif p >= 0.5:
            r = f"likely cut off: no open road to {dest_word} in most simulated damage scenarios"
        elif p >= 0.1:
            r = f"at risk: some simulated damage scenarios cut its road to {dest_word}"
        else:
            r = f"still connected by road to {dest_word} in nearly all simulated damage scenarios"
        if res.connected_before[i] and nm in fixer and p >= 0.1:
            c = fixer[nm]
            r += f"; reopening {c['name']} ({c['kind']}) would reconnect it"
        if res.connected_before[i] and row.get("route_uses_unobserved"):
            r += "; part of its usual route was not visible to the radar, so damage there is assumed, not seen"
        if res.dest.basis != "health":
            r += f" (destination used: {dest_word}, because {res.dest.note})"
        out.append(r)
    return out


def write_settlements(res: AccessResult, chokepoints: list[dict], path: Path) -> None:
    pl = res.places.copy()
    pl["reason"] = _reasons(res, chokepoints)
    rank_of = {}
    for c in chokepoints:
        for nm in c["names"]:
            rank_of.setdefault(nm, c["rank"])
    pl["fixed_by_rank"] = [rank_of.get(n) for n in pl["settlement_name"]]
    pl["p_cutoff"] = [None if not np.isfinite(x) else round(float(x), 3) for x in pl["p_cutoff"]]
    pl["cutoff"] = pl["cutoff"].astype(bool)
    keep = [c for c in pl.columns if c != "geometry" and c not in CONTRACT_PROPS + SETTLEMENT_EXTRA]
    pl = pl[keep + CONTRACT_PROPS + SETTLEMENT_EXTRA + ["geometry"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    pl.to_file(path, driver="GeoJSON")


def summary(res: AccessResult, chokepoints: list[dict], spof: list[dict], info: dict | None = None) -> dict:
    """Plain numbers for the facts ledger. Every key here is safe to cite."""
    pl = res.places
    p = pl["p_cutoff"].to_numpy(dtype=float)
    names = pl["settlement_name"].to_numpy()
    fin = np.isfinite(p)
    cut = pl["cutoff"].to_numpy(dtype=bool)
    hi = fin & (p >= 0.9) & cut
    mid = fin & (p >= 0.5) & (p < 0.9) & cut
    walkable = fin & (p >= 0.5) & ~cut
    risk = fin & (p >= 0.1) & (p < 0.5)
    mb = np.array([x if x is not None else np.nan for x in pl["minutes_before"]], dtype=float)
    ma = np.array([x if x is not None else np.nan for x in pl["minutes_after"]], dtype=float)
    both = np.isfinite(mb) & np.isfinite(ma)
    delay = ma[both] - mb[both]
    rg = res.graph
    return {
        "settlements_total": int(len(pl)),
        "settlements_with_road_before": int(res.has_road.sum()),
        "settlements_without_road_before": int((~res.has_road).sum()),
        "settlements_connected_before": int(res.connected_before.sum()),
        "cutoff_p90_plus": int(hi.sum()),
        "cutoff_p50_to_90": int(mid.sum()),
        "cutoff_total_p50_plus": int((hi | mid).sum()),
        "at_risk_p10_to_50": int(risk.sum()),
        "road_cut_but_walkable": int(walkable.sum()),
        "names_road_cut_but_walkable": [str(n) for n in names[walkable]],
        "names_p90_plus": [str(n) for n in names[hi]],
        "names_p50_to_90": [str(n) for n in names[mid]],
        "names_at_risk": [str(n) for n in names[risk]],
        "cutoff_people_p50_plus": _people(pl, hi | mid),
        "median_extra_minutes_still_connected": round(float(np.median(delay)), 1) if len(delay) else None,
        "max_extra_minutes_still_connected": round(float(np.max(delay)), 1) if len(delay) else None,
        "walk_reachable_but_road_cut": int(((hi | mid) & pl["walk_minutes_after"].notna().to_numpy()).sum()),
        "destination_basis": res.dest.basis,
        "destination_note": res.dest.note,
        "destinations": len(res.dest.names),
        "draws": res.n_draws,
        "seed": res.seed,
        "unobserved_prior": float(rg.stats.get("unobserved_prior", np.nan)),
        "unobserved_edges": int(rg.unobserved.sum()),
        "unobserved_km": round(float(rg.length_m[rg.unobserved].sum()) / 1000, 1),
        "graph": rg.stats,
        "top_repair": chokepoints[0] if chokepoints else None,
        "repairs_listed": len(chokepoints),
        "still_cut_after_top_repairs": (info or {}).get("still_cut_after_top_repairs"),
        "single_point_bridges": len(spof),
        "single_point_bridge_names": [s["name"] for s in spof],
        "population_tags": bool((info or {}).get("population_tags", False)),
        "timings": res.timings,
    }


def _people(pl, mask):
    if "population" not in pl.columns:
        return None
    vals = []
    for x in pl.loc[mask, "population"]:
        try:
            v = float(str(x).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            vals.append(v)
    return int(sum(vals)) if vals else None


def run_access(run_dir: str | Path, n_draws: int = 1000, seed: int = 7, unobserved_prior: float = 0.15,
               top: int = 10) -> dict:
    run_dir = Path(run_dir)
    out_dir = run_dir / "access"
    t0 = time.perf_counter()
    res = analyse(run_dir, n_draws=n_draws, seed=seed, unobserved_prior=unobserved_prior)
    t1 = time.perf_counter()
    chokes, info = rank_chokepoints(res, top=top)
    spof = single_points_of_failure(res)
    res.timings["chokepoints_s"] = round(time.perf_counter() - t1, 2)
    write_settlements(res, chokes, out_dir / "settlements.geojson")
    write_chokepoints(chokes, out_dir / "chokepoints.json")
    (out_dir / "single_points.json").write_text(json.dumps(spof, indent=2, ensure_ascii=False))
    res.timings["total_s"] = round(time.perf_counter() - t0, 2)
    s = summary(res, chokes, spof, info)
    (out_dir / "access_summary.json").write_text(json.dumps(s, indent=2, ensure_ascii=False, default=str))
    return s
