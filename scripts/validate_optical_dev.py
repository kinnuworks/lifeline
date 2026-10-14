"""Choose the Sentinel-2 fusion settings on the development area only, then freeze them.

    uv run python scripts/validate_optical_dev.py runs/trishuli-2026

Starts from the radar-only map (rasters/flood_class_radar.tif), tries a small grid of
settings for lifeline.optical.indices.fuse_optical, and picks the one with the best
event-extent F1 on EMS area 3 (Bidur). Ties go to the setting that adds the least ground.
Held-out areas are scored and recorded for transparency but play no part in the choice.
Writes runs/<id>/validation/optical_fusion_dev.json.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.ops import unary_union

from lifeline.acquire import stac
from lifeline.optical.indices import fuse_optical
from lifeline.validate.compare import DEFAULT_OUT, _burn, _load_grid, _read_raster, _select_stage, confusion_counts, scores

DEV, HELD = "AOI03", ["AOI01", "AOI02", "AOI05"]


def references(run: Path, shape, tf, crs):
    feats = _select_stage(gpd.read_file(DEFAULT_OUT / "features.geojson").fillna(""), "first").to_crs(crs)
    aois = gpd.read_file(DEFAULT_OUT / "aois.geojson").to_crs(crs)
    out = {}
    for a in [DEV, *HELD]:
        f = feats[feats.aoi == a]
        area = aois[aois.aoi == a].geometry.iloc[0]
        na = f[f.layer == "not_analysed"]
        if len(na):
            area = area.difference(unary_union(na.geometry.values))
        valid = _burn([area], tf, shape)
        out[a] = (_burn(list(f[f.layer == "observed_event"].geometry), tf, shape) & valid, valid)
    return out


def f1(pred, ref, valid):
    c = confusion_counts(pred, ref, valid)
    return round(scores(c["tp"], c["fp"], c["fn"], c["tn"])["f1"], 3)


def main(run: Path) -> None:
    crs, tf, w, h, px = _load_grid(run)
    grid = stac.Grid.from_json(json.loads((run / "grid.json").read_text()))
    refs = references(run, (h, w), tf, crs)
    radar = run / "rasters" / "flood_class_radar.tif"
    if not radar.exists():
        sys.exit("run the pipeline with optical on first: rasters/flood_class_radar.tif is missing")
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "rasters").mkdir()
        for name in ("flood_class_radar", "flood_prob_radar", "optical_change", "hand"):
            shutil.copy(run / "rasters" / f"{name}.tif", t / "rasters" / f"{name}.tif")
        base = np.isin(_read_raster(radar, (h, w)), (1, 2))
        rows.append({"setting": "radar only", **{a: f1(base, *refs[a]) for a in refs}, "added_km2": 0.0})
        for hand_m in (10, 20, 30, 45, 60):
            for near_km in (0.5, 1.0, 2.0, 50.0):
                for min_px in (10, 50):
                    r = fuse_optical(t, grid, hand_m=hand_m, near_km=near_km, min_px=min_px)
                    pred = np.isin(_read_raster(t / "rasters" / "flood_class.tif", (h, w)), (1, 2))
                    rows.append({"hand_m": hand_m, "near_km": near_km, "min_px": min_px, "added_km2": r["optical_added_km2"],
                                 **{a: f1(pred, *refs[a]) for a in refs}})
    cand = [r for r in rows if "hand_m" in r]
    best = max(cand, key=lambda r: (r[DEV], -r["added_km2"]))
    out = {"dev_area": DEV, "held_out": HELD, "rule": "max F1 on the development area; ties to the least added ground",
           "radar_only": rows[0], "variants": cand, "chosen": best}
    (run / "validation").mkdir(exist_ok=True)
    (run / "validation" / "optical_fusion_dev.json").write_text(json.dumps(out, indent=1))
    print("radar only:", rows[0])
    print("chosen on", DEV, ":", best)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "runs/trishuli-2026"))
