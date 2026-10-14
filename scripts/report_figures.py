"""Figures for docs/REPORT: flood map, cut-off map and the cross-event check.

    uv run python scripts/report_figures.py trishuli-2026
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio
from matplotlib.colors import LightSource, ListedColormap
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "img"
INK, MUTED = "#1D1D1F", "#6E6E73"
plt.rcParams.update({"font.family": "Helvetica", "font.size": 8.5, "axes.edgecolor": "#C8C8CC", "axes.linewidth": 0.6,
                     "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED})


def read(run: Path, name: str):
    with rasterio.open(run / "rasters" / f"{name}.tif") as s:
        return s.read(1).astype("float32"), s.transform, s.crs


def lonlat_to_px(transform, crs, lon, lat):
    x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
    c, r = ~transform * (x, y)
    return c, r


def flood_figure(run: Path):
    pre, tf, crs = read(run, "s1_pre_vv_db")
    post, _, _ = read(run, "s1_post_vv_db")
    cls, _, _ = read(run, "flood_class")
    # the Trishuli valley around Bidur and Betrawati
    c0, r0 = lonlat_to_px(tf, crs, 85.12, 27.98)
    c1, r1 = lonlat_to_px(tf, crs, 85.22, 27.88)
    sl = (slice(int(r0), int(r1)), slice(int(c0), int(c1)))

    def grey(a):
        return np.clip((a[sl] + 25) / 25, 0, 1) ** 0.8

    fig, ax = plt.subplots(1, 3, figsize=(7.2, 2.7), dpi=220)
    ax[0].imshow(grey(pre), cmap="gray", vmin=0, vmax=1)
    ax[0].set_title("Radar before (16 Aug)", fontsize=8.5, color=INK, loc="left")
    ax[1].imshow(grey(post), cmap="gray", vmin=0, vmax=1)
    ax[1].set_title("Radar after (28 Aug)", fontsize=8.5, color=INK, loc="left")
    ax[2].imshow(grey(post), cmap="gray", vmin=0, vmax=1)
    c = cls[sl]
    over = np.zeros(c.shape + (4,))
    over[c == 2] = (0.84, 0.47, 0.12, 0.85)
    over[c == 1] = (0.12, 0.43, 0.86, 0.85)
    over[c == 3] = (0.84, 0.47, 0.12, 0.45)
    over[c == 255] = (0.47, 0.47, 0.51, 0.35)
    ax[2].imshow(over)
    ax[2].set_title("Mapped: debris (orange), new water (blue), not seen (grey)", fontsize=8.5, color=INK, loc="left")
    for a in ax:
        a.set_xticks([]), a.set_yticks([])
    fig.tight_layout(pad=0.4)
    fig.savefig(OUT / "fig_flood.png", bbox_inches="tight")
    plt.close(fig)


def cutoff_figure(run: Path):
    dem, tf, crs = read(run, "dem")
    step = 4
    d = dem[::step, ::step]
    hs = LightSource(azdeg=315, altdeg=40).hillshade(np.nan_to_num(d, nan=np.nanmean(d)), vert_exag=0.05, dx=20 * step, dy=20 * step)
    h, w = dem.shape
    to = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=220)
    left, top = tf.c, tf.f
    ext = (left, left + w * tf.a, top + h * tf.e, top)
    ax.imshow(hs, cmap="gray", extent=ext, vmin=0.1, vmax=1.1, alpha=0.9)
    roads = json.loads((run / "damage" / "roads.geojson").read_text())["features"]
    for f in roads:
        st = f["properties"].get("status")
        if st not in ("destroyed_likely",) or f["geometry"]["type"] != "LineString":
            continue
        xs, ys = to.transform(*np.array(f["geometry"]["coordinates"]).T)
        ax.plot(xs, ys, color="#D6782A", lw=1.1, solid_capstyle="round")
    st = json.loads((run / "access" / "settlements.geojson").read_text())["features"]
    col = {"cut_off": "#C0392B", "road_cut_walkable": "#E6A23C", "connected": "#2E7D4F", "no_road_before": "#9A9AA2"}
    for k in ("no_road_before", "connected", "road_cut_walkable", "cut_off"):
        pts = [f for f in st if f["properties"].get("access_status") == k]
        if not pts:
            continue
        xs, ys = to.transform(*np.array([p["geometry"]["coordinates"] for p in pts]).T)
        ax.scatter(xs, ys, s=9 if k != "cut_off" else 13, c=col[k], edgecolors="white", linewidths=0.35, zorder=3,
                   label={"cut_off": "cut off from care", "road_cut_walkable": "road cut, care within an hour on foot",
                          "connected": "still connected", "no_road_before": "no mapped road before"}[k] + f" ({len(pts)})")
    ch = json.loads((run / "access" / "chokepoints.json").read_text())
    ch = ch.get("ranked", ch) if isinstance(ch, dict) else ch
    if ch:
        top = ch[0]
        x, y = to.transform(top["lon"], top["lat"])
        ax.scatter([x], [y], marker="*", s=120, c=INK, edgecolors="white", linewidths=0.5, zorder=4, label="repair that reconnects the most villages")
    ax.plot([], [], color="#D6782A", lw=1.2, label="road likely destroyed")
    ax.set_xticks([]), ax.set_yticks([])
    ax.set_xlim(ext[0], ext[1]), ax.set_ylim(ext[2], ext[3])
    ax.legend(loc="lower left", fontsize=6.6, frameon=True, framealpha=0.92, edgecolor="#DDD")
    fig.tight_layout(pad=0.3)
    fig.savefig(OUT / "fig_cutoff.png", bbox_inches="tight")
    plt.close(fig)


def events_figure():
    rows = []
    for rid, label in (("trishuli-2026", "Trishuli 2026\n(case study)"), ("placebo-2026-07", "Same area,\nno flood (25 Jul)"),
                       ("melamchi-2021", "Melamchi 2021"), ("teesta-2023", "Teesta 2023"),
                       ("beas-mandi-2023", "Beas, Mandi 2023"), ("chamoli-2021", "Chamoli 2021")):
        m = ROOT / "runs" / rid / "meta.json"
        if not m.exists():
            continue
        meta = json.loads(m.read_text())
        fl = meta.get("flood", {})
        km2 = fl.get("area_km2_total") or fl.get("area_km2") or fl.get("total_km2")
        if km2 is None:
            with rasterio.open(ROOT / "runs" / rid / "rasters" / "flood_class.tif") as s:
                c = s.read(1)
                km2 = float(np.isin(c, (1, 2, 3)).sum() * abs(s.transform.a * s.transform.e) / 1e6)
        dm = json.loads((ROOT / "runs" / rid / "damage" / "summary.json").read_text())
        b = dm.get("buildings", {}).get("destroyed_likely", 0)
        rows.append((label, km2, b))
    fig, ax = plt.subplots(1, 2, figsize=(7.2, 2.6), dpi=220)
    labels = [r[0] for r in rows]
    colors = ["#D6782A" if "no flood" not in l else "#9A9AA2" for l in labels]
    ax[0].barh(labels, [r[1] for r in rows], color=colors)
    ax[0].set_title("Ground mapped as flooded or debris, km²", fontsize=8.5, loc="left", color=INK)
    ax[1].barh(labels, [r[2] for r in rows], color=colors)
    ax[1].set_title("Buildings likely destroyed", fontsize=8.5, loc="left", color=INK)
    ax[1].set_yticklabels([])
    for a, k in ((ax[0], 1), (ax[1], 2)):
        a.invert_yaxis()
        a.spines[["top", "right"]].set_visible(False)
        for i, r in enumerate(rows):
            a.text(r[k], i, f" {r[k]:,.1f}" if k == 1 else f" {r[k]:,}", va="center", fontsize=7, color=INK)
    fig.tight_layout(pad=0.4)
    fig.savefig(OUT / "fig_events.png", bbox_inches="tight")
    plt.close(fig)
    return rows


if __name__ == "__main__":
    run = ROOT / "runs" / (sys.argv[1] if len(sys.argv) > 1 else "trishuli-2026")
    OUT.mkdir(parents=True, exist_ok=True)
    flood_figure(run)
    cutoff_figure(run)
    print(events_figure())
