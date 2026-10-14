"""Run the water segmentation model on a Bato run folder and compare it with
the pipeline's change-detection classes.

Usage: uv run python scripts/eval_himalaya.py runs/<run_id> [--tta]

Reads   runs/<id>/rasters/s1_post_vv_db.tif, s1_post_vh_db.tif (post-event dB, run grid)
        runs/<id>/rasters/flood_class.tif   (0 unchanged, 1 open water, 2 debris/scour, 255 not observed)
        runs/<id>/rasters/slope.tif         (optional, degrees)
Writes  runs/<id>/rasters/model_prob_raw.tif (float32 0..1, NaN where no radar data)
        runs/<id>/rasters/model_prob.tif    (same, set to 0 where unobserved, slope > 15 deg or HAND > 25 m)
        runs/<id>/model_agreement.json      (agreement summary, printed as well)

There are no ground-truth labels for the Himalayan scenes, so this is an
agreement check between two independent methods, not an accuracy score.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio

from lifeline.model.infer import WEIGHTS, run_on_run_dir


def agreement(water: np.ndarray, cls: np.ndarray, valid: np.ndarray) -> dict:
    obs = valid & (cls != 255)
    n = int(obs.sum())
    out = {"observed_pixels": n}
    if n == 0:
        return out
    ref = cls == 1
    tp = int((water & ref & obs).sum()); fp = int((water & ~ref & obs).sum())
    fn = int((~water & ref & obs).sum())
    out["model_water_pixels"] = int((water & obs).sum())
    out["pipeline_open_water_pixels"] = int((ref & obs).sum())
    out["iou_vs_open_water"] = tp / max(tp + fp + fn, 1)
    out["share_of_pipeline_open_water_model_calls_water"] = tp / max(tp + fn, 1)
    out["share_of_model_water_pipeline_calls_open_water"] = tp / max(tp + fp, 1)
    for k, name in ((0, "unchanged"), (2, "debris_scour")):
        m = obs & (cls == k)
        out[f"{name}_pixels"] = int(m.sum())
        out[f"share_of_{name}_model_calls_water"] = float(water[m].mean()) if m.any() else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--weights", default=str(WEIGHTS))
    ap.add_argument("--tta", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    r = run_dir / "rasters"
    t0 = time.time()
    try:
        # Writes model_prob_raw.tif and the terrain-masked model_prob.tif.
        summary = run_on_run_dir(run_dir, threshold=args.threshold, tta=args.tta, weights=args.weights)
    except RuntimeError as e:
        sys.exit(str(e))
    summary["seconds"] = round(time.time() - t0, 1)

    cls_p, slope_p = r / "flood_class.tif", r / "slope.tif"
    if cls_p.exists():
        with rasterio.open(cls_p) as s:
            cls = s.read(1)
        slope = None
        if slope_p.exists():
            with rasterio.open(slope_p) as s:
                slope = s.read(1)
        for name in ("model_prob_raw", "model_prob"):
            with rasterio.open(r / f"{name}.tif") as s:
                prob = s.read(1)
            if prob.shape != cls.shape:
                summary["warning"] = "flood_class.tif shape differs from radar"
                break
            valid = np.isfinite(prob)
            water = np.nan_to_num(prob, nan=0.0) >= args.threshold
            block = {"all": agreement(water, cls, valid)}
            if slope is not None and slope.shape == prob.shape:
                for lo, hi in ((0, 5), (5, 15), (15, 30), (30, 90)):
                    m = (slope >= lo) & (slope < hi)
                    block[f"slope_{lo}_{hi}_deg"] = agreement(water, cls, valid & m)
            summary[f"agreement_{name}"] = block
    (run_dir / "model_agreement.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
