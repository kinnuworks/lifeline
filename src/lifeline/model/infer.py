"""Sliding-window inference for the Sentinel-1 water U-Net.

The pipeline calls predict_water() with post-event VV and VH backscatter in dB
on the run grid. Any array size works; edges are reflect-padded and tiles are
blended with a smooth weight so seams do not show.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import torch

from lifeline.model.dataset import normalise
from lifeline.model.unet import build_unet

WEIGHTS = Path(__file__).resolve().parent / "weights" / "lifeline_unet_s1.pt"


def default_device() -> str:
    return "mps" if torch.backends.mps.is_available() else "cpu"


@lru_cache(maxsize=4)
def load_model(weights: str = str(WEIGHTS), device: str | None = None):
    """Load the exported model once. Returns (model, config)."""
    device = device or default_device()
    if not Path(weights).exists():
        raise FileNotFoundError(f"model weights not found: {weights}")
    ck = torch.load(weights, map_location="cpu", weights_only=False)
    cfg = ck["config"]
    model = build_unet(cfg["in_channels"], pretrained=False)
    model.load_state_dict({k: v.float() for k, v in ck["state_dict"].items()})
    model.eval().to(device)
    return model, cfg


def _weight_window(tile: int, overlap: int) -> np.ndarray:
    """2-D blend weight: 1 in the centre, cosine ramp over the overlap."""
    w = np.ones(tile, np.float32)
    if overlap > 0:
        ramp = 0.5 - 0.5 * np.cos(np.linspace(0, np.pi, overlap + 2)[1:-1])
        w[:overlap] = ramp
        w[-overlap:] = ramp[::-1]
    return np.outer(w, w).astype(np.float32) + 1e-6


def _starts(n: int, tile: int, step: int) -> list[int]:
    if n <= tile:
        return [0]
    s = list(range(0, n - tile, step))
    s.append(n - tile)
    return s


def predict_water(vv_db: np.ndarray, vh_db: np.ndarray, tile: int = 512, overlap: int = 64,
                  *, weights: str | Path = WEIGHTS, device: str | None = None,
                  batch: int = 4, tta: bool = False) -> np.ndarray:
    """Water probability (float32, 0..1, same shape as the inputs).

    vv_db, vh_db: 2-D Sentinel-1 backscatter in dB (sigma0 or gamma0). NaN
    marks no data; output is NaN where either input is NaN or infinite.
    tile: window size (multiple of 32). overlap: pixels shared between windows.
    tta: average over the 4 flips (slower, slightly smoother).
    """
    vv_db = np.asarray(vv_db, np.float32)
    vh_db = np.asarray(vh_db, np.float32)
    if vv_db.shape != vh_db.shape or vv_db.ndim != 2:
        raise ValueError(f"vv and vh must be 2-D and the same shape, got {vv_db.shape} {vh_db.shape}")
    if tile % 32 or overlap < 0 or overlap * 2 >= tile:
        raise ValueError("tile must be a multiple of 32 and overlap < tile / 2")
    model, cfg = load_model(str(weights), device)
    dev = next(model.parameters()).device
    nodata = ~(np.isfinite(vv_db) & np.isfinite(vh_db))

    H, W = vv_db.shape
    x = normalise(vv_db, vh_db, cfg["in_channels"])
    # Pad to at least one tile in each direction.
    ph, pw = max(tile - H, 0), max(tile - W, 0)
    if ph or pw:
        mode = "reflect" if (H > 1 and W > 1 and ph < H and pw < W) else "edge"
        x = np.pad(x, ((0, 0), (0, ph), (0, pw)), mode=mode)
    _, Hp, Wp = x.shape
    step = tile - overlap
    win = _weight_window(tile, overlap)
    acc = np.zeros((Hp, Wp), np.float32)
    wsum = np.zeros((Hp, Wp), np.float32)
    coords = [(y, xx) for y in _starts(Hp, tile, step) for xx in _starts(Wp, tile, step)]

    with torch.no_grad():
        for i in range(0, len(coords), batch):
            cs = coords[i:i + batch]
            t = torch.from_numpy(np.stack([x[:, y:y + tile, xx:xx + tile] for y, xx in cs])).to(dev)
            if tta:
                p = torch.zeros(t.shape[0], 1, tile, tile, device=dev)
                for dims in ([], [2], [3], [2, 3]):
                    ti = t.flip(dims) if dims else t
                    o = torch.sigmoid(model(ti))
                    p += o.flip(dims) if dims else o
                p /= 4
            else:
                p = torch.sigmoid(model(t))
            p = p[:, 0].float().cpu().numpy()
            for (y, xx), pi in zip(cs, p):
                acc[y:y + tile, xx:xx + tile] += pi * win
                wsum[y:y + tile, xx:xx + tile] += win
    prob = (acc / wsum)[:H, :W].astype(np.float32)
    prob[nodata] = np.nan
    return prob


MODEL_NAME = "lifeline_unet_s1 (U-Net, ResNet34 encoder, Sentinel-1 VV+VH)"

# Terrain mask applied after inference (see docs/model/RESULTS.md).
MAX_SLOPE_DEG = 15.0
MAX_HAND_M = 25.0


def _summary(water: np.ndarray, cls: np.ndarray | None, px_km2: float) -> dict:
    out = {"water_km2": round(float(water.sum()) * px_km2, 3),
           "agree_with_radar_water": None, "model_only_km2": None,
           "model_water_on_debris_class_km2": None}
    if cls is not None:
        radar, observed = cls == 1, cls != 255
        n = int(radar.sum())
        out["agree_with_radar_water"] = round(float((water & radar).sum()) / n, 4) if n else None
        out["model_only_km2"] = round(float((water & ~radar & observed).sum()) * px_km2, 3)
        out["model_water_on_debris_class_km2"] = round(float((water & (cls == 2)).sum()) * px_km2, 3)
    return out


def run_on_run_dir(run_dir: Path | str, threshold: float = 0.5, tta: bool = False,
                   weights: str | Path = WEIGHTS, max_slope_deg: float = MAX_SLOPE_DEG,
                   max_hand_m: float = MAX_HAND_M) -> dict:
    """Pipeline entry point.

    Reads rasters/s1_post_vv_db.tif and s1_post_vh_db.tif (float32 dB, NaN =
    no data, run grid). Writes, with the input profile and dtype float32:
      rasters/model_prob_raw.tif  the model output as is
      rasters/model_prob.tif      the same, set to 0 where observed.tif == 0,
                                  slope.tif > max_slope_deg or hand.tif > max_hand_m
                                  (each mask is used only if its raster exists)
    Returns a summary for meta.json with raw and masked numbers. Inference runs
    on the 20 m grid directly; training included 20 m samples (10 m chips
    averaged 2 x 2), so no resampling is done.

    Raises RuntimeError if the weights or the input rasters are missing.
    """
    import rasterio

    run_dir = Path(run_dir)
    r = run_dir / "rasters"
    if not Path(weights).exists():
        raise RuntimeError(f"water model weights not found at {weights}; model_prob.tif not written")
    vv_p, vh_p = r / "s1_post_vv_db.tif", r / "s1_post_vh_db.tif"
    for p in (vv_p, vh_p):
        if not p.exists():
            raise RuntimeError(f"water model input missing: {p}")

    def read(p, as_float=True):
        with rasterio.open(p) as s:
            a, prof, res = s.read(1), s.profile.copy(), s.res
            if as_float:
                a = a.astype(np.float32)
                if s.nodata is not None and not np.isnan(s.nodata):
                    a[a == s.nodata] = np.nan
            return a, prof, res

    def optional(name, as_float=True):
        p = r / name
        if not p.exists():
            return None
        a = read(p, as_float)[0]
        return a if a.shape == vv.shape else None

    vv, prof, res = read(vv_p)
    vh, _, _ = read(vh_p)
    try:
        prob = predict_water(vv, vh, tile=512, overlap=64, weights=weights, tta=tta)
    except FileNotFoundError as e:
        raise RuntimeError(str(e)) from e

    masked = prob.copy()
    applied = []
    observed = optional("observed.tif", as_float=False)
    if observed is not None:
        masked[(observed == 0) & np.isfinite(masked)] = 0.0
        applied.append("observed==0")
    slope = optional("slope.tif")
    if slope is not None:
        masked[(slope > max_slope_deg) & np.isfinite(masked)] = 0.0
        applied.append(f"slope>{max_slope_deg:g}deg")
    hand = optional("hand.tif")
    if hand is not None:
        masked[(hand > max_hand_m) & np.isfinite(masked)] = 0.0
        applied.append(f"hand>{max_hand_m:g}m")

    prof.update(dtype="float32", count=1, nodata=float("nan"))
    for name, a in (("model_prob_raw.tif", prob), ("model_prob.tif", masked)):
        with rasterio.open(r / name, "w", **prof) as dst:
            dst.write(a, 1)

    cls = optional("flood_class.tif", as_float=False)
    px_km2 = abs(res[0] * res[1]) / 1e6
    raw = _summary(np.nan_to_num(prob, nan=0.0) >= threshold, cls, px_km2)
    msk = _summary(np.nan_to_num(masked, nan=0.0) >= threshold, cls, px_km2)
    out = {"model": MODEL_NAME, "weights": Path(weights).name, "threshold": threshold,
           "pixel_m": float(abs(res[0])), "terrain_mask": applied, **msk,
           "raw": raw, "masked": msk}
    if slope is not None:
        w = np.nan_to_num(prob, nan=0.0) >= threshold
        out["raw"]["water_km2_on_slope_gt_20deg"] = round(float((w & (slope > 20)).sum()) * px_km2, 3)
    return out
