"""Score the full flood extent (detected flood + model water + inferred river corridor) against EMSR927,
next to detected flood alone, and record how the two extra classes were chosen.

The extra classes were chosen on the development area (AOI03, Bidur) only, among the variants listed in
extent_dev.json, with the no-flood date (25 Jul 2026) capped at 3 km² of false alarm. AOI01, AOI02 and AOI05
are held out. A checking script only: the pipeline never reads EMS files.

    uv run python scripts/validate_extent.py   # writes runs/trishuli-2026/validation/extent.json
"""
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "data/reference/emsr927"
DEV = "AOI03"


def main():
    rd = ROOT / "runs/trishuli-2026/rasters"
    src = rasterio.open(rd / "flood_extent.tif")
    T, shp, crs = src.transform, src.shape, src.crs
    ext = src.read(1)
    maps = {"detected": np.isin(ext, (1, 2)), "detected_plus_model_water": np.isin(ext, (1, 2, 4)), "full_extent": np.isin(ext, (1, 2, 4, 5))}
    feats = gpd.read_file(REF / "features.geojson").to_crs(crs)
    first = feats[feats["stage"] == "first"]
    aois = gpd.read_file(REF / "aois.geojson").to_crs(crs)
    aois = aois[aois.status == "delivered"]
    res = {k: {"areas": {}} for k in maps}
    pooled = {k: [0, 0, 0] for k in maps}
    for _, a in aois.iterrows():
        g = a.geometry
        na = feats[(feats.aoi == a.aoi) & (feats.layer == "not_analysed")]
        if len(na):
            g = g.difference(unary_union(na.geometry.values))
        v = rasterize([(g, 1)], out_shape=shp, transform=T).astype(bool)
        ref = rasterize([(x, 1) for x in first[(first.aoi == a.aoi) & (first.layer == "observed_event")].geometry], out_shape=shp, transform=T).astype(bool) & v
        for k, p in maps.items():
            tp, fp, fn = int((p & ref & v).sum()), int((p & ~ref & v).sum()), int((~p & ref & v).sum())
            pr, rc = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
            res[k]["areas"][f"{a.aoi} {a['name']}"] = {"f1": round(2 * pr * rc / max(pr + rc, 1e-9), 3), "precision": round(pr, 3), "recall": round(rc, 3), "role": "development" if a.aoi == DEV else "held out"}
            if a.aoi != DEV:
                pooled[k] = [x + y for x, y in zip(pooled[k], (tp, fp, fn))]
    for k, (tp, fp, fn) in pooled.items():
        pr, rc = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
        res[k]["held_out_pooled"] = {"f1": round(2 * pr * rc / max(pr + rc, 1e-9), 3), "precision": round(pr, 3), "recall": round(rc, 3)}
    for run in ("trishuli-2026", "placebo-2026-07"):
        m = json.loads((ROOT / "runs" / run / "meta.json").read_text()).get("extent", {})
        res.setdefault("areas_km2", {})[run] = m
    res["note"] = __doc__.split("\n\n")[1].replace("\n", " ")
    (ROOT / "runs/trishuli-2026/validation/extent.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k].get("held_out_pooled") for k in maps}, indent=1)); print(json.dumps(res["areas_km2"], indent=1))
    for k in maps: print(k, {a: v["f1"] for a, v in res[k]["areas"].items()})


if __name__ == "__main__":
    main()
