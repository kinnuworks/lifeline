"""Check the U-Net's water map on the case-study scene against the official EMSR927 extent.

The model never saw this scene in training. EMS maps the event extent (landslide and debris) inside
four areas; the model is trained to see open water only, so precision (how much of what it marks is
inside the official extent) is the fair test and recall is low by design. A checking script only:
the pipeline never reads EMS files.

    uv run python scripts/validate_model_ems.py   # writes runs/trishuli-2026/validation/model_vs_ems.json
"""
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize

RUN = Path("runs/trishuli-2026")
REF = Path("data/reference/emsr927")


def main():
    R = RUN / "rasters"
    src = rasterio.open(R / "model_prob.tif")
    T, shp, crs = src.transform, src.shape, src.crs
    layers = {
        "unet_masked": np.nan_to_num(src.read(1)) >= 0.5,
        "unet_raw": np.nan_to_num(rasterio.open(R / "model_prob_raw.tif").read(1)) >= 0.5,
    }
    fc = rasterio.open(R / "flood_class.tif").read(1)
    layers["radar_water_rule"] = fc == 1
    layers["lifeline_flood_map"] = (fc == 1) | (fc == 2)
    obs = rasterio.open(R / "observed.tif").read(1) > 0
    feats = gpd.read_file(REF / "features.geojson").to_crs(crs)
    aois = gpd.read_file(REF / "aois.geojson").to_crs(crs)
    aois = aois[aois.status == "delivered"]
    ev = rasterize([(g, 1) for g in feats[feats.layer == "observed_event"].geometry], out_shape=shp, transform=T).astype(bool)
    out = {"note": __doc__.split("\n\n")[1].replace("\n", " "), "areas": {}, "pooled": {}}
    pooled = {k: [0, 0, 0] for k in layers}
    for _, row in aois.iterrows():
        v = rasterize([(row.geometry, 1)], out_shape=shp, transform=T).astype(bool) & obs
        res = {"ems_extent_px": int((ev & v).sum())}
        for k, p in layers.items():
            tp, fp, fn = int((p & ev & v).sum()), int((p & ~ev & v).sum()), int((~p & ev & v).sum())
            pooled[k] = [a + b for a, b in zip(pooled[k], (tp, fp, fn))]
            res[k] = {"precision": round(tp / max(tp + fp, 1), 3), "recall": round(tp / max(tp + fn, 1), 3), "marked_px": tp + fp}
        out["areas"][f"{row.aoi} {row['name']}"] = res
    for k, (tp, fp, fn) in pooled.items():
        out["pooled"][k] = {"precision": round(tp / max(tp + fp, 1), 3), "recall": round(tp / max(tp + fn, 1), 3)}
    dst = RUN / "validation" / "model_vs_ems.json"
    dst.write_text(json.dumps(out, indent=1))
    print(json.dumps(out["pooled"], indent=1))


if __name__ == "__main__":
    main()
