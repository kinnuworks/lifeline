"""Choose how to combine radar orbits using one development area only.

Uses official map area AOI03 (Bidur) as the development area and reports the
other delivered areas as held-out. The official maps are read here only, for
checking; the chosen setting is then written into the pipeline by hand.

    uv run python scripts/validate_fusion_dev.py trishuli-2026
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np

from lifeline.acquire import stac
from lifeline.pipeline import read_scenes_parallel
from lifeline.radar import change, terrain
from lifeline.validate.compare import _burn, _load_grid, confusion_counts, scores

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data" / "reference" / "emsr927"
DEV = "AOI03"


def main(run_id: str):
    run = ROOT / "runs" / run_id
    meta = json.loads((run / "meta.json").read_text())
    grid = stac.Grid.from_json(json.loads((run / "grid.json").read_text()))
    crs, tr, w, h, _ = _load_grid(run)
    def evidence_for(event):
        pairs = stac.choose_pairs(tuple(meta["bbox"]), event, baseline_passes=3)[:3]
        scenes = [s for p in pairs for s in p.history + [p.before, p.after]]
        data = dict(zip([(s.orbit, s.date) for s in scenes], read_scenes_parallel(scenes, grid)))
        out = []
        for p in pairs:
            hist = [data[(s.orbit, s.date)] for s in p.history + [p.before]]
            aft = data[(p.orbit, p.after.date)]
            out.append(change.OrbitEvidence(p.label(), p.direction, change.robust_z([x["vv"] for x in hist], aft["vv"]), change.robust_z([x["vh"] for x in hist], aft["vh"]), aft["vv"], terrain.visibility(dem, grid.pixel_m, p.direction).astype(bool)))
        return out, [p.label() for p in pairs]

    dem = stac.read_raster(run / "rasters" / "dem.tif")
    slope = stac.read_raster(run / "rasters" / "slope.tif")
    hand = stac.read_raster(run / "rasters" / "hand.tif")
    ev, _ = evidence_for(dt.date.fromisoformat(meta["event_date"]))
    placebo_date = dt.date(2026, 7, 25)  # monsoon season, no reported flood in this corridor
    ev_null, null_pairs = evidence_for(placebo_date)
    print("placebo pairs:", null_pairs)
    px_km2 = grid.pixel_m**2 / 1e6

    feats = gpd.read_file(REF / "features.geojson").to_crs(crs)
    aois = gpd.read_file(REF / "aois.geojson").to_crs(crs)
    first = feats[feats["stage"] == "first"]
    event_layers = [l for l in first["layer"].unique() if "event" in l or "extent" in l]
    print("event layers:", event_layers)
    areas = {}
    for _, a in aois.iterrows():
        m = _burn([a.geometry], tr, (h, w))
        if m.sum() < 1000:
            continue
        ref = _burn(list(first[(first.aoi == a.aoi) & first.layer.isin(event_layers)].geometry), tr, (h, w))
        areas[a.aoi] = (m, ref)

    variants = []
    for gp in (0.25, 0.4):
        for gk in (0.5, 1.0, 2.0):
            for ms in (25, 100):
                variants.append(dict(combine="max", null_quantile=0.99, grow_prob=gp, corridor_hand_m=30.0, grow_km=gk, min_seed_px=ms))
    variants.append(dict(combine="max", null_quantile=0.99, grow_prob=None, min_seed_px=25))
    rows = []
    for v in variants:
        fused = change.fuse(ev, hand, slope, **v)
        pred = np.isin(fused["flood_class"], (1, 2))
        null = change.fuse(ev_null, hand, slope, **v)
        row = dict(v) | {"event_km2": round(float(pred.sum() * px_km2), 1), "placebo_km2": round(float(np.isin(null["flood_class"], (1, 2)).sum() * px_km2), 2)}
        for aoi, (m, ref) in areas.items():
            c = confusion_counts(pred, ref, m)
            sc = scores(c["tp"], c["fp"], c["fn"])
            row[aoi] = round(sc["f1"] or 0, 3)
        rows.append(row)
        print(row, flush=True)
    MAX_PLACEBO_KM2 = 3.0
    ok = [r for r in rows if r["placebo_km2"] <= MAX_PLACEBO_KM2] or rows
    best = max(ok, key=lambda r: r.get(DEV, 0))
    held = [a for a in areas if a != DEV]
    print(f"\nchosen on {DEV} only: {best}")
    print("held-out F1:", {a: best[a] for a in held})
    (run / "validation" / "fusion_dev.json").write_text(json.dumps({"dev_area": DEV, "held_out": held, "placebo_date": placebo_date.isoformat(), "max_placebo_km2": MAX_PLACEBO_KM2, "variants": rows, "chosen": best}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
