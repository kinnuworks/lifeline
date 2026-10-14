"""What the terrain allows: slope, height above the river, and what radar can see.

Height above nearest drainage (HAND) says how far a pixel sits above the
stream it drains to; a flood cannot reach ground hundreds of metres up a
valley wall. Visibility comes from the radar's viewing geometry: slopes facing
the satellite more steeply than its incidence angle fold over (layover), and
slopes facing away more steeply than 90 degrees minus incidence are hidden
(shadow). Neither can be measured, so we mark them instead of guessing.
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from skimage.morphology import reconstruction

# Sentinel-1 IW: satellite heading for each pass direction (degrees from north)
HEADING = {"ascending": -13.0, "descending": 193.0}
INCIDENCE_DEG = 39.0  # middle of the IW swath; the swath spans about 30 to 46


def slope_aspect(dem: np.ndarray, pixel_m: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Slope in degrees and the east / north components of the gradient."""
    z = np.where(np.isfinite(dem), dem, np.nanmedian(dem))
    dzdy, dzdx = np.gradient(z, pixel_m)
    dzdy = -dzdy  # rows run north to south
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return slope.astype("float32"), dzdx, dzdy, z


def visibility(dem: np.ndarray, pixel_m: float, direction: str, incidence_deg: float = INCIDENCE_DEG) -> np.ndarray:
    """1 where the radar can measure the ground, 0 in layover or shadow."""
    _, dzdx, dzdy, _ = slope_aspect(dem, pixel_m)
    look_az = math.radians(HEADING[direction] + 90.0)  # right-looking
    to_sensor = look_az + math.pi
    th = math.radians(incidence_deg)
    s = np.array([math.sin(th) * math.sin(to_sensor), math.sin(th) * math.cos(to_sensor), math.cos(th)])
    n = np.stack([-dzdx, -dzdy, np.ones_like(dzdx)])
    n /= np.linalg.norm(n, axis=0)
    cos_local = s[0] * n[0] + s[1] * n[1] + s[2] * n[2]
    local_inc = np.degrees(np.arccos(np.clip(cos_local, -1, 1)))
    # slope component pointing toward the sensor (positive = facing it)
    toward = np.degrees(np.arctan(-(dzdx * math.sin(to_sensor) + dzdy * math.cos(to_sensor))))
    layover = toward > incidence_deg - 2.0
    shadow = local_inc > 85.0
    vis = ~(layover | shadow)
    # a pixel next to a fold is unreliable too
    vis = ndimage.binary_erosion(vis, iterations=1, border_value=1)
    return vis.astype("uint8")


def fill_depressions(dem: np.ndarray) -> np.ndarray:
    z = np.where(np.isfinite(dem), dem, np.nanmax(dem))
    seed = z.copy()
    seed[1:-1, 1:-1] = z.max()
    return reconstruction(seed, z, method="erosion")


# D8 neighbours: (row offset, col offset)
_D8 = [(-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1), (-1, -1)]


def flow_direction(filled: np.ndarray) -> np.ndarray:
    """Index (0..7) of the steepest downhill neighbour, -1 for flats and edges."""
    h, w = filled.shape
    best = np.zeros((h, w), dtype="float64")
    out = np.full((h, w), -1, dtype="int8")
    pad = np.pad(filled, 1, mode="edge")
    for k, (dr, dc) in enumerate(_D8):
        nb = pad[1 + dr : 1 + dr + h, 1 + dc : 1 + dc + w]
        drop = (filled - nb) / (math.sqrt(2) if dr and dc else 1.0)
        better = drop > best
        best[better] = drop[better]
        out[better] = k
    return out


def flow_accumulation(fdir: np.ndarray) -> np.ndarray:
    """Upstream cell count, by processing cells from high to low (topological)."""
    h, w = fdir.shape
    acc = np.ones(h * w, dtype="float64")
    idx = np.arange(h * w)
    r, c = np.divmod(idx, w)
    dr = np.array([d[0] for d in _D8] + [0])[fdir.ravel()]
    dc = np.array([d[1] for d in _D8] + [0])[fdir.ravel()]
    tr, tc = r + dr, c + dc
    valid = (fdir.ravel() >= 0) & (tr >= 0) & (tr < h) & (tc >= 0) & (tc < w)
    target = np.where(valid, tr * w + tc, -1)
    indeg = np.bincount(target[target >= 0], minlength=h * w)
    queue = list(np.flatnonzero(indeg == 0))
    head = 0
    while head < len(queue):
        i = queue[head]
        head += 1
        t = target[i]
        if t >= 0:
            acc[t] += acc[i]
            indeg[t] -= 1
            if indeg[t] == 0:
                queue.append(t)
    return acc.reshape(h, w)


def hand(dem: np.ndarray, pixel_m: float, channel_km2: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Height above nearest drainage, and the channel mask it was measured from.

    Each pixel follows its flow path downhill until it reaches a channel cell;
    HAND is its elevation minus that channel cell's elevation.
    """
    filled = fill_depressions(dem)
    fdir = flow_direction(filled)
    acc = flow_accumulation(fdir)
    channels = acc * (pixel_m**2) / 1e6 >= channel_km2
    h, w = dem.shape
    flat_ch = channels.ravel()
    dr = np.array([d[0] for d in _D8] + [0])[fdir.ravel()]
    dc = np.array([d[1] for d in _D8] + [0])[fdir.ravel()]
    idx = np.arange(h * w)
    r, c = np.divmod(idx, w)
    tr, tc = r + dr, c + dc
    valid = (fdir.ravel() >= 0) & (tr >= 0) & (tr < h) & (tc >= 0) & (tc < w)
    nxt = np.where(valid, tr * w + tc, idx)
    nxt[flat_ch] = np.flatnonzero(flat_ch)  # channel cells absorb
    # pointer jumping: after k rounds each cell points 2^k steps downstream,
    # stopping at the first channel cell because channels point to themselves
    for _ in range(26):
        jumped = nxt[nxt]
        if np.array_equal(jumped, nxt):
            break
        nxt = jumped
    drain_cell = nxt
    z = filled.ravel()
    out = (z - z[drain_cell]).reshape(h, w)
    out = np.where(np.isfinite(dem), np.maximum(out, 0), np.nan)
    return out.astype("float32"), channels
