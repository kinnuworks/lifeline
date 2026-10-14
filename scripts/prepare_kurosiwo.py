"""Convert partial Kuro Siwo GRD webdataset shards into small npz chips.

The shards in data/kurosiwo/raw/ are the first bytes of train_GRD shards from
huggingface.co/datasets/orion-ai-lab/Kuro-Siwo-Webdataset (HTTP range requests,
so only whole samples at the start of each shard are kept). Each output chip
holds the post-event VV and VH in dB and a label: 1 water (permanent or flood),
0 not water, -1 invalid. Events in South Asia are skipped (held out).

Usage: uv run python scripts/prepare_kurosiwo.py
"""
from __future__ import annotations

import collections
import io
import json
import tarfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1] / "data" / "kurosiwo"
RAW, OUT = ROOT / "raw", ROOT / "chips"
# Kuro Siwo activation ids for South Asian events (Nepal/Bihar 2019, Pakistan 2022).
HELD_OUT_ACTS = {1111007, 1111009}
NEEDED = {"flood_vv.npy", "flood_vh.npy", "mask.npy", "valid_mask.npy", "info.json"}


def to_db(a: np.ndarray) -> np.ndarray:
    a = a.astype(np.float32)
    out = np.full(a.shape, np.nan, np.float32)
    ok = np.isfinite(a) & (a > 0)
    out[ok] = 10 * np.log10(np.maximum(a[ok], 1e-5))
    return out


def save(key: str, parts: dict, stats: collections.Counter) -> None:
    if not NEEDED <= parts.keys():
        stats["incomplete"] += 1
        return
    info = json.loads(parts["info.json"])
    act = int(info["actid"])
    if act in HELD_OUT_ACTS:
        stats[f"held_out_{act}"] += 1
        return
    ld = lambda k: np.load(io.BytesIO(parts[k]), allow_pickle=False).squeeze()
    vv, vh = to_db(ld("flood_vv.npy")), to_db(ld("flood_vh.npy"))
    mask, valid = ld("mask.npy"), ld("valid_mask.npy")
    lab = np.where(mask >= 1, 1, 0).astype(np.int8)
    lab[(valid < 0.5) | ~np.isfinite(vv) | ~np.isfinite(vh)] = -1
    if (lab >= 0).mean() < 0.5:
        stats["mostly_invalid"] += 1
        return
    name = f"{act}_{int(info['aoiid']):02d}_{key}"
    np.savez_compressed(OUT / f"{name}.npz", vv=vv, vh=vh, label=lab)
    stats[f"act_{act}"] += 1
    stats["saved"] += 1


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    stats = collections.Counter()
    for shard in sorted(RAW.glob("*.tar")):
        cur_key, parts = None, {}
        try:
            with tarfile.open(shard, "r|") as t:
                for m in t:
                    key, _, field = m.name.partition(".")
                    if key != cur_key:
                        if cur_key is not None:
                            save(f"{shard.stem.split('.')[0][-1]}{cur_key}", parts, stats)
                        cur_key, parts = key, {}
                    f = t.extractfile(m)
                    if f is not None and field in NEEDED:
                        parts[field] = f.read()
        except (tarfile.ReadError, EOFError, OSError) as e:
            stats["truncated_shards"] += 1  # last sample is dropped
            print(shard.name, "ends early:", e)
    print(json.dumps(dict(stats), indent=1))


if __name__ == "__main__":
    main()
