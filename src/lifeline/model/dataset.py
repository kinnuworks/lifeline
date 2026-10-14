"""Sen1Floods11 hand-labelled chips for water segmentation.

Inputs are Sentinel-1 GRD backscatter in dB (VV, VH). The model sees three
channels: normalised VV, normalised VH and normalised VV minus VH (the dB
form of the VV/VH ratio). Labels: 1 water, 0 not water, -1 no label (ignored).
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
import torch
from torch.utils.data import Dataset

DATA_ROOT = Path(__file__).resolve().parents[3] / "data" / "sen1floods11"
IGNORE = -1

# Clip ranges and normalisation constants in dB. Means and standard deviations
# are rounded values measured on the Sen1Floods11 training chips after clipping.
CLIP = {"vv": (-35.0, 5.0), "vh": (-42.0, -5.0), "diff": (-5.0, 25.0)}
STATS = {"vv": (-10.8, 4.1), "vh": (-17.5, 4.8), "diff": (6.8, 3.4)}

# Events held out of training and validation entirely because they are in or
# next to the Himalayan region (see docs/model/DATA.md).
HELD_OUT_EVENTS = ("India", "Pakistan")


def normalise(vv_db: np.ndarray, vh_db: np.ndarray, channels: int = 3) -> np.ndarray:
    """Return a (C, H, W) float32 array. NaN inputs become 0 (the mean)."""
    vv = np.clip(vv_db.astype(np.float32), *CLIP["vv"])
    vh = np.clip(vh_db.astype(np.float32), *CLIP["vh"])
    out = [(vv - STATS["vv"][0]) / STATS["vv"][1], (vh - STATS["vh"][0]) / STATS["vh"][1]]
    if channels == 3:
        diff = np.clip(vv - vh, *CLIP["diff"])
        out.append((diff - STATS["diff"][0]) / STATS["diff"][1])
    x = np.stack(out).astype(np.float32)
    return np.nan_to_num(x, nan=0.0, posinf=0.0, neginf=0.0)


def read_split(name: str) -> list[str]:
    """Chip ids (e.g. 'Ghana_103272') listed in an official split CSV."""
    path = DATA_ROOT / "splits" / f"flood_{name}_data.csv"
    with open(path) as f:
        return [row[0].replace("_S1Hand.tif", "") for row in csv.reader(f) if row]


def event_of(chip_id: str) -> str:
    return chip_id.rsplit("_", 1)[0]


@dataclass
class Chip:
    chip_id: str
    vv: np.ndarray  # dB, float32, may contain NaN
    vh: np.ndarray
    label: np.ndarray  # int8, -1/0/1


def load_chip(chip_id: str) -> Chip:
    with rasterio.open(DATA_ROOT / "S1Hand" / f"{chip_id}_S1Hand.tif") as s:
        vv, vh = s.read().astype(np.float32)
    with rasterio.open(DATA_ROOT / "LabelHand" / f"{chip_id}_LabelHand.tif") as s:
        lab = s.read(1).astype(np.int16)
    lab = np.where((lab == 0) | (lab == 1), lab, IGNORE).astype(np.int8)
    lab[~np.isfinite(vv) | ~np.isfinite(vh)] = IGNORE
    return Chip(chip_id, vv, vh, lab)


def load_chips(ids: list[str]) -> list[Chip]:
    return [load_chip(i) for i in ids]


KURO_ROOT = DATA_ROOT.parent / "kurosiwo" / "chips"


def load_kurosiwo(root: Path = KURO_ROOT) -> list[Chip]:
    """Kuro Siwo post-event chips prepared by scripts/prepare_kurosiwo.py (224 x 224)."""
    out = []
    for f in sorted(root.glob("*.npz")):
        with np.load(f, allow_pickle=False) as z:
            out.append(Chip("kurosiwo_" + f.stem, z["vv"].astype(np.float32),
                            z["vh"].astype(np.float32), z["label"].astype(np.int8)))
    return out


def _pool2(vv: np.ndarray, vh: np.ndarray, lab: np.ndarray):
    """2x2 downsample: average backscatter in linear power, majority label.

    Simulates the 20 m grid the pipeline runs on (Sen1Floods11 is 10 m).
    """
    h, w = (vv.shape[0] // 2) * 2, (vv.shape[1] // 2) * 2

    def pool_db(a):
        lin = 10.0 ** (a[:h, :w] / 10.0)
        lin = lin.reshape(h // 2, 2, w // 2, 2)
        return (10.0 * np.log10(np.nanmean(lin, axis=(1, 3)) + 1e-10)).astype(np.float32)

    l = lab[:h, :w].reshape(h // 2, 2, w // 2, 2).astype(np.float32)
    valid = (l >= 0).sum(axis=(1, 3))
    water = (l == 1).sum(axis=(1, 3))
    out = np.full(valid.shape, IGNORE, np.int8)
    ok = valid >= 2
    out[ok] = (water[ok] * 2 > valid[ok]).astype(np.int8)
    return pool_db(vv), pool_db(vh), out


class S1WaterDataset(Dataset):
    """Training/eval dataset over in-memory chips.

    train=True: random 256 crop at 10 m, or (p=pool_p) the whole chip pooled to
    20 m (256 x 256), then random flips and 90 degree rotations.
    train=False: full 512 chip, no augmentation.
    """

    def __init__(self, chips: list[Chip], train: bool, crop: int = 256,
                 channels: int = 3, pool_p: float = 0.5, seed: int = 0):
        self.chips = chips
        self.train = train
        self.crop = crop
        self.channels = channels
        self.pool_p = pool_p
        self.rng = np.random.default_rng(seed)
        self.pooled = [_pool2(c.vv, c.vh, c.label) for c in chips] if train and pool_p > 0 else None

    def __len__(self) -> int:
        return len(self.chips)

    def __getitem__(self, i: int):
        c = self.chips[i]
        if not self.train:
            x = normalise(c.vv, c.vh, self.channels)
            return torch.from_numpy(x), torch.from_numpy(c.label.astype(np.int64))
        r = self.rng
        if self.pooled is not None and r.random() < self.pool_p:
            vv, vh, lab = self.pooled[i]
        else:
            vv, vh, lab = c.vv, c.vh, c.label
        h, w = lab.shape
        k = self.crop
        y0 = r.integers(0, h - k + 1) if h > k else 0
        x0 = r.integers(0, w - k + 1) if w > k else 0
        vv, vh, lab = vv[y0:y0 + k, x0:x0 + k], vh[y0:y0 + k, x0:x0 + k], lab[y0:y0 + k, x0:x0 + k]
        x = normalise(vv, vh, self.channels)
        y = lab.astype(np.int64)
        ph, pw = k - y.shape[0], k - y.shape[1]
        if ph > 0 or pw > 0:  # small chips: pad with zeros and ignored labels
            x = np.pad(x, ((0, 0), (0, max(ph, 0)), (0, max(pw, 0))))
            y = np.pad(y, ((0, max(ph, 0)), (0, max(pw, 0))), constant_values=IGNORE)
        rot = int(r.integers(0, 4))
        x, y = np.rot90(x, rot, axes=(1, 2)), np.rot90(y, rot)
        if r.random() < 0.5:
            x, y = x[:, :, ::-1], y[:, ::-1]
        # Mild per-chip gain jitter (+-1 dB) for calibration differences.
        if r.random() < 0.5:
            x = x.copy()
            x[0] += r.normal(0, 1.0) / STATS["vv"][1]
            x[1] += r.normal(0, 1.0) / STATS["vh"][1]
        return torch.from_numpy(np.ascontiguousarray(x)), torch.from_numpy(np.ascontiguousarray(y))
