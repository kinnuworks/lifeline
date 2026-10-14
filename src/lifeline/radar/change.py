"""Where the ground changed, measured against its own normal behaviour.

For each orbit track we know how every pixel looked on several dates before
the flood. A change is unusual only if the after-image falls outside that
pixel's own normal range, so we score it as a robust z: the after value minus
the pre-flood median, divided by the pre-flood spread.

Two kinds of change matter here:
- open water: radar return drops (smooth water reflects the pulse away) and
  the after value is dark in absolute terms;
- debris and scour: rubble, mud and stripped vegetation change both the
  co-polarised (VV) and cross-polarised (VH) returns, in either direction.

The decision threshold is set from ground that cannot have flooded (pixels far
above the river), so it controls the false alarm rate without looking at any
published damage map.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

MIN_SPREAD_DB = 0.8  # floor on the per-pixel spread so stable pixels are not over-sensitive


@dataclass
class OrbitEvidence:
    label: str
    direction: str
    z_vv: np.ndarray
    z_vh: np.ndarray
    after_vv: np.ndarray
    visible: np.ndarray  # bool


def robust_z(stack: list[np.ndarray], after: np.ndarray) -> np.ndarray:
    arr = np.stack(stack)
    med = np.nanmedian(arr, axis=0)
    mad = np.nanmedian(np.abs(arr - med), axis=0) * 1.4826
    spread = np.maximum(np.nan_to_num(mad, nan=MIN_SPREAD_DB), MIN_SPREAD_DB)
    return ((after - med) / spread).astype("float32")


def change_score(ev: OrbitEvidence) -> np.ndarray:
    """One number per pixel: how strongly the ground changed, in spreads."""
    zvv = np.nan_to_num(ev.z_vv)
    zvh = np.nan_to_num(ev.z_vh)
    return np.sqrt((zvv**2 + zvh**2) / 2.0).astype("float32")


def null_threshold(score: np.ndarray, null_mask: np.ndarray, quantile: float = 0.995) -> float:
    """Score exceeded by only (1 - quantile) of pixels that cannot have flooded."""
    vals = score[null_mask & np.isfinite(score)]
    if vals.size < 1000:
        return 3.5
    return float(np.clip(np.quantile(vals, quantile), 2.5, 8.0))


def water_threshold_db(after_vv: np.ndarray, candidate: np.ndarray) -> float:
    """Otsu split of after-image VV on low ground, bounded to sane values."""
    v = after_vv[candidate & np.isfinite(after_vv)]
    if v.size < 2000:
        return -18.0
    hist, edges = np.histogram(v, bins=256, range=(-30, 5))
    centres = (edges[:-1] + edges[1:]) / 2
    w = np.cumsum(hist)
    m = np.cumsum(hist * centres)
    total, mt = w[-1], m[-1]
    w1 = total - w
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mt * w / total - m) ** 2 / (w * w1)
    t = centres[np.nanargmax(np.where((w > 0) & (w1 > 0), between, np.nan))]
    return float(np.clip(t, -22.0, -14.0))


def fuse(
    evidence: list[OrbitEvidence],
    hand: np.ndarray,
    slope: np.ndarray,
    hand_limit_m: float = 60.0,
    null_hand_m: float = 150.0,
    combine: str = "max",
    null_quantile: float = 0.99,
    hand_scale_m: float = 20.0,
    grow_prob: float | None = 0.25,
    corridor_hand_m: float = 30.0,
    valley_fill_hand_m: float | None = 8.0,
    grow_km: float = 0.5,
    min_seed_px: int = 100,
    pixel_m: float = 20.0,
) -> dict[str, np.ndarray]:
    """Combine every orbit into flood probability, class and visibility count."""
    shape = hand.shape
    observed = np.zeros(shape, dtype="uint8")
    logit_sum = np.zeros(shape, dtype="float32")
    logit_max = np.full(shape, -np.inf, dtype="float32")
    water_votes = np.zeros(shape, dtype="float32")
    details = []
    null_mask = (hand > null_hand_m) & (slope < 30)
    for ev in evidence:
        vis = ev.visible & np.isfinite(ev.z_vv) & np.isfinite(ev.z_vh)
        score = change_score(ev)
        thr = null_threshold(score, null_mask & vis, null_quantile)
        # map the score to a calibrated-ish logit: 0 at the threshold, +2 per spread above it
        logit = np.where(vis, 2.0 * (score - thr), 0.0)
        logit_sum += logit.astype("float32")
        logit_max = np.where(vis, np.maximum(logit_max, logit), logit_max)
        observed += vis.astype("uint8")
        low = vis & (hand < hand_limit_m)
        wt = water_threshold_db(ev.after_vv, low)
        water = vis & (ev.after_vv < wt) & (np.nan_to_num(ev.z_vv) < -1.5)
        water_votes += water.astype("float32")
        details.append({"orbit": ev.label, "threshold_spreads": round(thr, 2), "water_db": round(wt, 1), "visible_share": round(float(vis.mean()), 3)})

    n = np.maximum(observed, 1)
    if combine == "max":
        # any one clear view of the change is enough; a geometry that cannot see it does not vote against it
        mean_logit = np.where(observed > 0, logit_max, 0.0)
    else:
        mean_logit = logit_sum / n
    # terrain prior: flooding is likely near the river and impossible far above it
    prior = np.clip(1.5 - hand / hand_scale_m, -6.0, 1.5)
    steep = np.where(slope > 40, -1.5, 0.0)
    total = mean_logit + prior + steep
    prob = 1.0 / (1.0 + np.exp(-total))
    prob = np.where(observed > 0, prob, np.nan).astype("float32")
    prob = np.where(hand > hand_limit_m * 2.5, np.where(observed > 0, 0.0, np.nan), prob).astype("float32")

    cls = np.zeros(shape, dtype="uint8")
    flooded = np.nan_to_num(prob) >= 0.5
    if min_seed_px > 1:
        # isolated specks of change (single fields, noise) are not a flood
        lab, n = ndimage.label(flooded, structure=np.ones((3, 3)))
        if n:
            sizes = ndimage.sum(flooded, lab, index=np.arange(1, n + 1))
            flooded = np.isin(lab, np.flatnonzero(sizes >= min_seed_px) + 1)
    if grow_prob is not None and flooded.any():
        # hysteresis: weaker change counts when it is connected, along low valley ground,
        # to change we are confident about, and no further than grow_km from it
        near = ndimage.distance_transform_edt(~flooded) * pixel_m <= grow_km * 1000.0
        weak = (np.nan_to_num(prob) >= grow_prob) & (hand < corridor_hand_m) & near
        lab, _ = ndimage.label(weak | flooded, structure=np.ones((3, 3)))
        keep = np.unique(lab[flooded & (lab > 0)])
        flooded = np.isin(lab, keep[keep > 0])
    inferred = np.zeros(shape, dtype=bool)
    if valley_fill_hand_m is not None:
        # ground the radar could not see, on the valley floor, enclosed by or touching mapped flooding
        blind_floor = (observed == 0) & (hand < valley_fill_hand_m)
        lab, _ = ndimage.label(blind_floor | flooded, structure=np.ones((3, 3)))
        keep = np.unique(lab[flooded & (lab > 0)])
        inferred = np.isin(lab, keep[keep > 0]) & blind_floor
    # water drains within days, so later passes of other tracks often miss it: like the flood
    # map itself, one clear view of new dark water is enough (combine="max"); otherwise a majority
    water = water_votes > 0 if combine == "max" else (water_votes / n) >= 0.5
    cls[flooded & water] = 1
    cls[flooded & ~water] = 2
    cls[observed == 0] = 255
    cls[inferred] = 3  # inferred from terrain, not seen by radar
    # make the probability agree with the final map, so every later step
    # (damage, cut-off) sees the same flood the map shows
    p = np.nan_to_num(prob)
    final = np.where(flooded, np.maximum(p, 0.5), np.minimum(p, 0.35)).astype("float32")
    final = np.where(inferred, 0.45, final)
    prob = np.where((observed > 0) | inferred, final, np.nan).astype("float32")
    return {"flood_prob": prob, "flood_class": cls, "observed": observed, "details": details}
