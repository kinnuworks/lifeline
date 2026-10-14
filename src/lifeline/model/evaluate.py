"""Evaluate the exported model and an Otsu-on-VV baseline on Sen1Floods11.

Usage: uv run python -m lifeline.model.evaluate [--weights PATH] [--tta]
Writes runs/model/metrics.json.

Metrics are pixel-aggregated over every labelled pixel in a set (IoU, F1,
precision, recall of the water class). Pixels labelled -1 are ignored.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from skimage.filters import threshold_otsu

from lifeline.model.dataset import HELD_OUT_EVENTS, _pool2, event_of, load_chips, read_split
from lifeline.model.infer import WEIGHTS, predict_water

PROJECT = Path(__file__).resolve().parents[3]
OUT = PROJECT / "runs" / "model" / "metrics.json"


def scores(tp: int, fp: int, fn: int, tn: int) -> dict:
    p = tp / max(tp + fp, 1)
    r = tp / max(tp + fn, 1)
    return {"iou": tp / max(tp + fp + fn, 1), "f1": 2 * p * r / max(p + r, 1e-12),
            "precision": p, "recall": r, "accuracy": (tp + tn) / max(tp + fp + fn + tn, 1),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def otsu_vv(vv_db: np.ndarray) -> np.ndarray:
    """Per-chip Otsu threshold on VV dB; water is below the threshold."""
    v = vv_db[np.isfinite(vv_db)]
    if v.size < 10 or v.min() == v.max():
        return np.zeros_like(vv_db, bool)
    return vv_db < threshold_otsu(np.clip(v, -40, 10))


class Counter:
    def __init__(self):
        self.c = np.zeros(4, np.int64)
        self.chip_iou = []

    def add(self, pred: np.ndarray, lab: np.ndarray) -> None:
        v = lab >= 0
        gt, pr = lab == 1, pred & v
        tp = int((pr & gt).sum()); fp = int((pr & ~gt).sum())
        fn = int((~pr & gt & v).sum()); tn = int((~pr & ~gt & v).sum())
        self.c += [tp, fp, fn, tn]
        if tp + fp + fn > 0:
            self.chip_iou.append(tp / (tp + fp + fn))

    def result(self) -> dict:
        out = scores(*map(int, self.c))
        out["mean_chip_iou"] = float(np.mean(self.chip_iou)) if self.chip_iou else None
        return out


GLOBAL_T = None  # Otsu threshold on VV pooled over all training chips (set in main)


def global_otsu_threshold() -> float:
    from lifeline.model.dataset import load_chips
    ids = [i for i in read_split("train") if event_of(i) not in HELD_OUT_EVENTS]
    v = np.concatenate([c.vv[(c.label >= 0) & np.isfinite(c.vv)] for c in load_chips(ids)])
    return float(threshold_otsu(np.clip(v, -40, 10)))


def run_set(chips, weights, tta: bool, pooled: bool = False) -> dict:
    model_c, otsu_c, glob_c = Counter(), Counter(), Counter()
    for c in chips:
        vv, vh, lab = (_pool2(c.vv, c.vh, c.label) if pooled else (c.vv, c.vh, c.label))
        prob = predict_water(vv, vh, tile=512 if not pooled else 256, overlap=0 if not pooled else 0,
                             weights=weights, tta=tta)
        model_c.add(np.nan_to_num(prob, nan=0.0) >= 0.5, lab)
        otsu_c.add(otsu_vv(vv), lab)
        glob_c.add(np.nan_to_num(vv, nan=0.0) < GLOBAL_T, lab)
    return {"chips": len(chips), "model": model_c.result(), "otsu_vv": otsu_c.result(),
            "otsu_vv_global": glob_c.result()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=str(WEIGHTS))
    ap.add_argument("--tta", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    t0 = time.time()

    test, valid, train = read_split("test"), read_split("valid"), read_split("train")
    bolivia = read_split("bolivia")
    every = train + valid + test
    sets = {
        "test_official": test,
        "test_excluding_held_out_events": [i for i in test if event_of(i) not in HELD_OUT_EVENTS],
        "bolivia": bolivia,
    }
    for ev in HELD_OUT_EVENTS:
        sets[f"held_out_{ev.lower()}_all_splits"] = [i for i in every if event_of(i) == ev]
    sets["held_out_south_asia_all_splits"] = [i for i in every if event_of(i) in HELD_OUT_EVENTS]

    global GLOBAL_T
    GLOBAL_T = global_otsu_threshold()
    print(f"global Otsu VV threshold from training chips: {GLOBAL_T:.2f} dB", flush=True)
    results = {}
    cache = {}
    for name, ids in sets.items():
        chips = [cache.setdefault(i, load_chips([i])[0]) for i in ids]
        results[name] = run_set(chips, args.weights, args.tta)
        if name in ("test_official", "bolivia"):
            results[name + "_at_20m"] = run_set(chips, args.weights, args.tta, pooled=True)
        m, o, g = results[name]["model"], results[name]["otsu_vv"], results[name]["otsu_vv_global"]
        print(f"{name:40s} n={len(ids):3d} model IoU {m['iou']:.3f} F1 {m['f1']:.3f} | "
              f"Otsu/chip IoU {o['iou']:.3f} | Otsu global IoU {g['iou']:.3f} F1 {g['f1']:.3f}", flush=True)

    import torch
    ck = torch.load(args.weights, map_location="cpu", weights_only=False)
    payload = {"weights": str(Path(args.weights).relative_to(PROJECT)) if str(args.weights).startswith(str(PROJECT)) else args.weights,
               "config": ck["config"], "tta": args.tta, "threshold": 0.5, "otsu_global_vv_threshold_db": GLOBAL_T,
               "held_out_events": list(HELD_OUT_EVENTS), "sets": results,
               "seconds": round(time.time() - t0)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2, default=float))
    print("wrote", args.out)


if __name__ == "__main__":
    main()
