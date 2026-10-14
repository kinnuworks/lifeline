"""Sentinel-2 as a second opinion, used only where the sky was clear on both dates.

Water raises the green-to-near-infrared contrast (NDWI); debris and scour strip
vegetation (NDVI falls) and expose wet sediment. Monsoon cloud often hides the
first weeks after a flood, so this layer confirms the radar map where it can
and is skipped where it cannot.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

from ..acquire import stac


def _nd(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b) / (a + b)


def optical_change(bbox, event: dt.date, grid: stac.Grid, run_dir: Path, window_days: int = 40, max_cloud: float = 40.0,
                   cancel=None) -> dict:
    cached = run_dir / "rasters" / "optical_change.tif"
    note = run_dir / "rasters" / "optical_change.json"
    if cached.exists() and note.exists():
        import json

        return json.loads(note.read_text()) | {"reused": True}
    pre = stac.find_s2(bbox, event - dt.timedelta(days=window_days), event - dt.timedelta(days=1), max_cloud)
    post = stac.find_s2(bbox, event + dt.timedelta(days=1), event + dt.timedelta(days=window_days), max_cloud)
    if not pre or not post:
        raise RuntimeError(f"no clear Sentinel-2 scene within {window_days} days on both sides of the event")
    a = stac.read_s2_composite(pre, grid)
    b = stac.read_s2_composite(post, grid)
    ndvi_pre, ndvi_post = _nd(a["B08"], a["B04"]), _nd(b["B08"], b["B04"])
    ndwi_post = _nd(b["B03"], b["B08"])
    clear = np.isfinite(ndvi_pre) & np.isfinite(ndvi_post)
    veg_loss = clear & ((ndvi_pre - ndvi_post) > 0.25) & (ndvi_post < 0.35)
    water = clear & (ndwi_post > 0.05) & (_nd(a["B03"], a["B08"]) < 0.0)
    if cancel is not None and cancel.is_set():   # the run moved on without optical; write nothing
        return {"cancelled": True}
    code = np.zeros(clear.shape, dtype="uint8")  # 0 no data, 1 clear unchanged, 2 vegetation stripped, 3 new water
    code[clear] = 1
    code[veg_loss] = 2
    code[water] = 3
    stac.write_raster(run_dir / "rasters" / "optical_change.tif", code, grid)
    info = {
        "pre_dates": sorted({it.properties["datetime"][:10] for it in pre[:6]}),
        "post_dates": sorted({it.properties["datetime"][:10] for it in post[:6]}),
        "clear_share": round(float(clear.mean()), 3),
    }
    import json

    note.write_text(json.dumps(info))
    return info


# Chosen on the development area only (EMS area 3, Bidur), frozen before scoring the others:
# scripts/validate_optical_dev.py, record in runs/trishuli-2026/validation/optical_fusion_dev.json
OPT_HAND_M = 45.0     # optical change counts only on valley ground
OPT_NEAR_KM = 0.5     # and only next to damage the radar already found
OPT_MIN_PX = 10       # patches smaller than this are dropped
OPT_PROB = 0.6        # optical-only ground is "possibly damaged", below radar's "likely"


def fuse_optical(run_dir: Path, grid: stac.Grid, hand_m: float = OPT_HAND_M, near_km: float = OPT_NEAR_KM,
                 min_px: int = OPT_MIN_PX) -> dict:
    """Add Sentinel-2 vegetation loss and new water to the radar flood map.

    In steep gorges a debris flow can strip a slope without changing its radar brightness
    much, while the optical image shows bare ground where forest was. That evidence is
    accepted only on valley ground (HAND under 45 m) within 0.5 km of radar-detected damage,
    so a landslide high on a hillside or a harvested field far from the river is not mapped
    as flood. The radar-only map is kept as flood_class_radar.tif.
    """
    from scipy import ndimage as ndi

    rd = run_dir / "rasters"
    code_p = rd / "optical_change.tif"
    if not code_p.exists():
        return {"optical_added_km2": 0.0, "note": "no optical change layer"}
    radar_p = rd / "flood_class_radar.tif"
    if not radar_p.exists():
        import shutil

        shutil.copy(rd / "flood_class.tif", radar_p)
        shutil.copy(rd / "flood_prob.tif", rd / "flood_prob_radar.tif")
    cls = stac.read_raster(radar_p).astype("uint8")
    prob = stac.read_raster(rd / "flood_prob_radar.tif").astype("float32")
    code = stac.read_raster(code_p)
    hand = stac.read_raster(rd / "hand.tif")
    radar = np.isin(cls, (1, 2))
    cand = np.isin(code, (2, 3)) & (np.nan_to_num(hand, nan=1e9) <= hand_m) & (cls != 255)
    if radar.any():
        cand &= ndi.distance_transform_edt(~radar) * grid.pixel_m <= near_km * 1000
    else:
        cand[:] = False
    lab, _ = ndi.label(cand & ~radar)
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_px
    keep[0] = False
    add = keep[lab]
    cls[add] = 2
    prob[add] = np.maximum(np.nan_to_num(prob[add]), OPT_PROB)
    stac.write_raster(rd / "flood_class.tif", cls, grid, nodata=None)
    stac.write_raster(rd / "flood_prob.tif", prob, grid)
    km2 = round(float(add.sum() * grid.pixel_m ** 2 / 1e6), 2)
    return {"optical_added_km2": km2, "hand_m": hand_m, "near_km": near_km, "min_px": min_px}
