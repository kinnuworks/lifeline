"""True-colour Sentinel-2 before/after of one valley stretch, for the website.

    uv run python scripts/optical_preview.py trishuli-2026 85.12 27.88 85.22 27.98

Uses the same pre/post windows and cloud mask as the optical stage, at 10 m.
Writes web/data/<id>/optical_before.jpg, optical_after.jpg and optical.json.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from lifeline.acquire import stac

ROOT = Path(__file__).resolve().parents[1]


def rgb(items, grid):
    c = stac.read_s2_composite(items, grid, bands=("B04", "B03", "B02"))
    a = np.dstack([c["B04"], c["B03"], c["B02"]])
    if np.nanmedian(a) > 0.09:                          # processing baseline 04.00+ adds 1000 to every band
        a = a - 0.1
    lo, hi = np.nanpercentile(a, 1), np.nanpercentile(a, 99)
    a = np.clip((a - lo) / (hi - lo), 0, 1) ** (1 / 1.25)
    return (np.nan_to_num(a, nan=0.92) * 255).astype("uint8")


def main(run_id, w, s, e, n):
    meta = json.loads((ROOT / "runs" / run_id / "meta.json").read_text())
    event = dt.date.fromisoformat(meta["event_date"])
    bbox = (w, s, e, n)
    grid = stac.Grid.for_bbox(bbox, pixel_m=10)
    pre = stac.find_s2(bbox, event - dt.timedelta(days=40), event - dt.timedelta(days=1), 40)
    post = stac.find_s2(bbox, event + dt.timedelta(days=1), event + dt.timedelta(days=40), 40)
    out = ROOT / "web" / "data" / run_id
    for name, items in (("before", pre), ("after", post)):
        Image.fromarray(rgb(items, grid)).save(out / f"optical_{name}.jpg", quality=84, optimize=True)
    info = {"bbox": list(bbox), "before_dates": sorted({i.properties["datetime"][:10] for i in pre[:6]}),
            "after_dates": sorted({i.properties["datetime"][:10] for i in post[:6]})}
    (out / "optical.json").write_text(json.dumps(info))
    print(info)


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], *map(float, a[1:5]))
