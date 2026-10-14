"""Map overlays for the dashboard: PNG images reprojected to web lon/lat with their bounds."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.warp import Resampling, calculate_default_transform, reproject

# flood classes: water, debris or scour, not observed
CLASS_RGBA = {1: (30, 110, 220, 210), 2: (214, 120, 30, 215), 255: (120, 120, 130, 70)}


def _to_wgs84(path: Path, resampling=Resampling.nearest, max_px: int = 2400):
    with rasterio.open(path) as src:
        transform, w, h = calculate_default_transform(src.crs, "EPSG:4326", src.width, src.height, *src.bounds)
        scale = max(1.0, max(w, h) / max_px)
        w, h = int(w / scale), int(h / scale)
        transform = transform * transform.scale(scale, scale)
        dst = np.zeros((h, w), dtype="float32")
        reproject(rasterio.band(src, 1), dst, src_transform=src.transform, src_crs=src.crs, dst_transform=transform, dst_crs="EPSG:4326", resampling=resampling, dst_nodata=np.nan)
    west, north = transform.c, transform.f
    east, south = west + transform.a * w, north + transform.e * h
    return dst, [[west, north], [east, north], [east, south], [west, south]]


def _save(rgba: np.ndarray, path: Path):
    Image.fromarray(rgba.astype("uint8"), "RGBA").save(path, optimize=True)


def make_previews(run_dir: Path) -> list[dict]:
    rd = run_dir / "rasters"
    out_dir = run_dir / "web"
    out_dir.mkdir(exist_ok=True)
    overlays = []

    cls, bounds = _to_wgs84(rd / "flood_class.tif")
    rgba = np.zeros(cls.shape + (4,), dtype="uint8")
    for k, col in CLASS_RGBA.items():
        rgba[cls == k] = col
    _save(rgba, out_dir / "flood_class.png")
    overlays.append({"id": "flood_class", "label": "Flood and debris", "url": "flood_class.png", "bounds": bounds})

    for name, label in (("s1_pre_vv_db", "Radar before"), ("s1_post_vv_db", "Radar after")):
        p = rd / f"{name}.tif"
        if not p.exists():
            continue
        arr, b = _to_wgs84(p, Resampling.bilinear)
        g = np.clip((arr + 25) / 25, 0, 1)
        g = np.nan_to_num(g) ** 0.8 * 255
        a = np.where(np.isfinite(arr), 255, 0)
        oid = "before" if "pre" in name else "after"
        _save(np.dstack([g, g, g, a]), out_dir / f"{oid}.png")
        overlays.append({"id": oid, "label": label, "url": f"{oid}.png", "bounds": b})

    prob, b = _to_wgs84(rd / "flood_prob.tif", Resampling.bilinear)
    p = np.nan_to_num(prob)
    alpha = np.clip((p - 0.3) / 0.5, 0, 1) * 230
    rgba = np.dstack([np.full_like(p, 214), np.full_like(p, 110) - p * 40, np.full_like(p, 30), alpha])
    _save(rgba, out_dir / "flood_prob.png")
    overlays.append({"id": "flood_prob", "label": "Flood probability", "url": "flood_prob.png", "bounds": b})

    # a small static picture for the situation report
    with rasterio.open(rd / "s1_post_vv_db.tif") as src:
        after = src.read(1)
    with rasterio.open(rd / "flood_class.tif") as src:
        c = src.read(1)
    step = max(1, max(after.shape) // 900)
    a = np.nan_to_num(np.clip((after[::step, ::step] + 25) / 25, 0, 1)) * 255
    img = np.dstack([a, a, a]).astype("float32")
    cc = c[::step, ::step]
    img[cc == 2] = img[cc == 2] * 0.35 + np.array([214, 120, 30]) * 0.65
    img[cc == 1] = img[cc == 1] * 0.35 + np.array([30, 110, 220]) * 0.65
    Image.fromarray(img.astype("uint8"), "RGB").save(rd / "preview_change.png", optimize=True)

    (out_dir / "overlays.json").write_text(json.dumps(overlays, indent=1))
    return overlays
