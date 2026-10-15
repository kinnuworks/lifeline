"""The full flood extent: detected flood plus two labelled, inferred classes.

flood_class.tif holds what the radar and Sentinel-2 detected (1 open water, 2 debris or scour).
flood_extent.tif adds, without changing those pixels:
  4  water the segmentation model finds within MODEL_NEAR_M of detected flood
  5  river corridor: ground at most CORRIDOR_HAND_M above the stream within CORRIDOR_NEAR_M of
     detected flood, inferred from terrain (a debris flood fills the channel next to what we see)
Both were chosen on the development area (EMS AOI03, Bidur) with the no-flood date capped at
3 km² of false alarm, the same rule as the radar settings; the other EMS areas stay held out.
The corridor applies only in confined mountain valleys (mean slope within 2 km of at least 10 degrees);
on plains nearly all ground is a few metres above a stream, so the rule would spread everywhere.
Damage and cut-off use detected ground only, so these classes never claim a building or a road.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

MODEL_NEAR_M = 300.0
CORRIDOR_HAND_M = 8.0
CORRIDOR_NEAR_M = 100.0
VALLEY_WINDOW_M = 2000.0   # the corridor rule is for confined mountain valleys: mean slope within 2 km
VALLEY_SLOPE_DEG = 10.0    # at least 10 degrees; on plains almost everything is a few metres above a stream
MODEL_WATER, CORRIDOR = 4, 5


def full_extent(flood_class: np.ndarray, model_prob: np.ndarray | None, hand: np.ndarray, pixel_m: float,
                slope: np.ndarray | None = None) -> np.ndarray:
    cls = flood_class.copy()
    det = (flood_class == 1) | (flood_class == 2)
    if not det.any():
        return cls
    dist = ndi.distance_transform_edt(~det) * pixel_m
    free = ~det & (flood_class != 255)
    if model_prob is not None:
        cls[free & (np.nan_to_num(model_prob) >= 0.5) & (dist <= MODEL_NEAR_M)] = MODEL_WATER
    free = free & (cls != MODEL_WATER)
    corridor = free & (np.nan_to_num(hand, nan=1e9) <= CORRIDOR_HAND_M) & (dist <= CORRIDOR_NEAR_M)
    if slope is not None:
        k = max(3, int(VALLEY_WINDOW_M / pixel_m) | 1)
        corridor &= ndi.uniform_filter(np.nan_to_num(slope).astype("float32"), size=k) >= VALLEY_SLOPE_DEG
    cls[corridor] = CORRIDOR
    return cls


def run_extent(run_dir: Path) -> dict:
    import rasterio

    rd = Path(run_dir) / "rasters"
    with rasterio.open(rd / "flood_class.tif") as src:
        fc, prof = src.read(1), src.profile
        px = abs(src.transform.a)
    hand = rasterio.open(rd / "hand.tif").read(1)
    mp = rasterio.open(rd / "model_prob.tif").read(1) if (rd / "model_prob.tif").exists() else None
    slope = rasterio.open(rd / "slope.tif").read(1) if (rd / "slope.tif").exists() else None
    ext = full_extent(fc, mp, hand, px, slope)
    prof.update(dtype="uint8", nodata=None, count=1, compress="deflate")
    with rasterio.open(rd / "flood_extent.tif", "w", **prof) as dst:
        dst.write(ext, 1)
    a = px * px / 1e6
    out = {"detected_km2": round(float(np.isin(fc, (1, 2)).sum() * a), 2),
           "model_water_km2": round(float((ext == MODEL_WATER).sum() * a), 2),
           "corridor_km2": round(float((ext == CORRIDOR).sum() * a), 2)}
    out["full_extent_km2"] = round(out["detected_km2"] + out["model_water_km2"] + out["corridor_km2"], 2)
    meta_p = Path(run_dir) / "meta.json"
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
        meta["extent"] = out
        meta_p.write_text(json.dumps(meta, indent=1, default=str))
    return out
