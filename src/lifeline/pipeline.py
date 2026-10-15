"""One command from an area and a date to maps, damage, cut-off villages and a report.

    uv run lifeline run --bbox 84.93 27.78 85.45 28.30 --date 2026-08-26

Stages write into runs/<run_id>/ following docs/CONTRACTS.md, and each stage
records its timing so the dashboard can show where the minutes went.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from .acquire import stac
from .acquire.osm import fetch_osm
from .radar import change, terrain

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "runs"
CACHE = ROOT / "data" / "cache"
STAGES = ["images", "terrain", "radar", "optical", "osm", "damage", "access", "model", "facts", "report", "previews"]


class Run:
    def __init__(self, run_id: str, bbox, event: dt.date, label: str = ""):
        self.id = run_id
        self.bbox = tuple(float(v) for v in bbox)
        self.event = event
        self.dir = RUNS / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "rasters").mkdir(exist_ok=True)
        self.meta = {
            "run_id": run_id,
            "label": label or run_id,
            "bbox": list(self.bbox),
            "event_date": event.isoformat(),
            "started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "stages": {},
            "warnings": [],
            "status": "running",
        }
        self.save()

    def save(self):
        (self.dir / "meta.json").write_text(json.dumps(self.meta, indent=1, default=str))

    def stage(self, name: str, state: str, **info):
        st = self.meta["stages"].setdefault(name, {})
        st["state"] = state
        st.update(info)
        self.save()

    def warn(self, text: str):
        self.meta["warnings"].append(text)
        self.save()


def log(msg: str):
    print(f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _cache_key(*parts) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:16]


def _scene_cache(scene: stac.S1Scene, grid: stac.Grid) -> Path:
    key = _cache_key(scene.date, scene.orbit, grid.crs, grid.transform, grid.width, grid.height, "ovr")
    return CACHE / f"s1_{key}.npz"


def read_scenes_parallel(scenes: list[stac.S1Scene], grid: stac.Grid, workers: int = 16) -> list[dict]:
    """Read every band of every slice of every pass at once; cache whole passes."""
    CACHE.mkdir(parents=True, exist_ok=True)
    out: list[dict | None] = [None] * len(scenes)
    jobs = []
    for i, sc in enumerate(scenes):
        path = _scene_cache(sc, grid)
        if path.exists():
            z = np.load(path)
            out[i] = {"vv": z["vv"], "vh": z["vh"]}
            continue
        for pol in ("vv", "vh"):
            for it in sc.items:
                jobs.append((i, pol, it.assets[pol].href))

    def one(job):
        i, pol, href = job
        return i, pol, stac.read_to_grid(href, grid, nodata=0)

    parts: dict[tuple[int, str], np.ndarray] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, pol, arr in ex.map(one, jobs):
            acc = parts.get((i, pol))
            if acc is None:
                parts[(i, pol)] = arr
            else:
                fill = ~np.isfinite(acc) | (acc <= 0)
                acc[fill] = arr[fill]
    for i, sc in enumerate(scenes):
        if out[i] is not None:
            continue
        d = {}
        for pol in ("vv", "vh"):
            lin = parts[(i, pol)]
            with np.errstate(divide="ignore", invalid="ignore"):
                d[pol] = (10.0 * np.log10(np.where(lin > 0, lin, np.nan))).astype("float32")
        np.savez_compressed(_scene_cache(sc, grid), **d)
        out[i] = d
    return out


def run(bbox, event: dt.date, run_id: str | None = None, label: str = "", max_orbits: int = 3, baseline_passes: int = 3, with_model: bool = True, with_optical: bool = True, optical_budget_s: float | None = None) -> Path:
    t_all = time.time()
    run_id = run_id or f"run-{event.isoformat()}-{_cache_key(bbox)[:6]}"
    R = Run(run_id, bbox, event, label)
    grid = stac.Grid.for_bbox(R.bbox)
    stac.save_grid(R.dir, grid)
    (R.dir / "aoi.geojson").write_text(json.dumps({"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [[[bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]]]]}}))
    log(f"Run {run_id}: {grid.width} x {grid.height} pixels at {grid.pixel_m:.0f} m ({grid.crs})")
    try:
        # OpenStreetMap downloads in the background while the radar is read
        # ---- images: find the radar pairs first, so a date with no pass fails in seconds
        t = time.time()
        R.stage("images", "running")
        pairs = stac.choose_pairs(R.bbox, event, baseline_passes=baseline_passes)[:max_orbits]
        if not pairs:
            raise RuntimeError("No Sentinel-1 pass on the same orbit track covers this area both before and within "
                               "two weeks after this date. If the flood was in the last two weeks, the next pass may not "
                               "be in the archive yet; otherwise try a slightly larger box.")
        with ThreadPoolExecutor(max_workers=1) as bg:
            osm_future = bg.submit(fetch_osm, R.bbox, event, R.dir / "osm", log)
            scenes = []
            for p in pairs:
                scenes += p.history + [p.before, p.after]
            log(f"Found {len(pairs)} radar tracks: " + "; ".join(p.label() for p in pairs) + f" ({len(scenes)} passes to read)")
            dem = stac.read_dem(R.bbox, grid)
            data = read_scenes_parallel(scenes, grid)
            by_scene = {(s.orbit, s.date): d for s, d in zip(scenes, data)}
            R.meta["radar_pairs"] = [
                {"orbit": p.orbit, "direction": p.direction, "before": p.before.date.isoformat(), "after": p.after.date.isoformat(), "baseline": [s.date.isoformat() for s in p.history], "platform": p.after.platform}
                for p in pairs
            ]
            R.stage("images", "done", seconds=round(time.time() - t, 1), passes=len(scenes))
            log(f"Read {len(scenes)} radar passes and the elevation model ({time.time() - t:.0f} s)")

            # ---- terrain
            t = time.time()
            R.stage("terrain", "running")
            slope, *_ = terrain.slope_aspect(dem, grid.pixel_m)
            hand, channels = terrain.hand(dem, grid.pixel_m)
            for name, arr in (("dem", dem.astype("float32")), ("slope", slope), ("hand", hand)):
                stac.write_raster(R.dir / "rasters" / f"{name}.tif", arr, grid)
            R.stage("terrain", "done", seconds=round(time.time() - t, 1))
            log(f"Terrain: slope and height above river ({time.time() - t:.0f} s)")

            # ---- radar change
            t = time.time()
            R.stage("radar", "running")
            evidence = []
            for p in pairs:
                hist = [by_scene[(s.orbit, s.date)] for s in p.history + [p.before]]
                aft = by_scene[(p.orbit, p.after.date)]
                vis = terrain.visibility(dem, grid.pixel_m, p.direction).astype(bool)
                evidence.append(
                    change.OrbitEvidence(
                        label=p.label(),
                        direction=p.direction,
                        z_vv=change.robust_z([h["vv"] for h in hist], aft["vv"]),
                        z_vh=change.robust_z([h["vh"] for h in hist], aft["vh"]),
                        after_vv=aft["vv"],
                        visible=vis,
                    )
                )
            fused = change.fuse(evidence, hand, slope)
            stac.write_raster(R.dir / "rasters" / "flood_prob.tif", fused["flood_prob"], grid)
            stac.write_raster(R.dir / "rasters" / "flood_class.tif", fused["flood_class"], grid, nodata=None)
            for stale in ("flood_class_radar.tif", "flood_prob_radar.tif"):   # left by an earlier optical fusion
                (R.dir / "rasters" / stale).unlink(missing_ok=True)
            stac.write_raster(R.dir / "rasters" / "observed.tif", fused["observed"], grid)
            first = by_scene[(pairs[0].orbit, pairs[0].after.date)]
            stac.write_raster(R.dir / "rasters" / "s1_post_vv_db.tif", first["vv"], grid)
            stac.write_raster(R.dir / "rasters" / "s1_post_vh_db.tif", first["vh"], grid)
            before0 = by_scene[(pairs[0].orbit, pairs[0].before.date)]
            stac.write_raster(R.dir / "rasters" / "s1_pre_vv_db.tif", before0["vv"], grid)
            cls = fused["flood_class"]
            px_km2 = (grid.pixel_m**2) / 1e6
            areas = {"water_km2": round(float((cls == 1).sum() * px_km2), 2), "debris_km2": round(float((cls == 2).sum() * px_km2), 2), "unobserved_km2": round(float((cls == 255).sum() * px_km2), 2)}
            R.meta["radar"] = {"orbits": fused["details"], **areas}
            R.stage("radar", "done", seconds=round(time.time() - t, 1), **areas)
            log(f"Flood map: {areas['debris_km2']} km² debris or scour, {areas['water_km2']} km² new water, {areas['unobserved_km2']} km² hidden from radar ({time.time() - t:.0f} s)")

            # ---- optical (when the sky allows)
            t = time.time()
            R.stage("optical", "running")
            try:
                if not with_optical:
                    raise RuntimeError("switched off for this run")
                from .optical.indices import optical_change

                if optical_budget_s:
                    import threading
                    from concurrent.futures import ThreadPoolExecutor as _TPE, TimeoutError as _TO

                    stop = threading.Event()
                    ex = _TPE(max_workers=1)
                    fut = ex.submit(optical_change, R.bbox, event, grid, R.dir, cancel=stop)
                    try:
                        opt = fut.result(timeout=optical_budget_s)
                    except _TO:
                        stop.set()
                        ex.shutdown(wait=False)
                        raise RuntimeError(f"Sentinel-2 took over {optical_budget_s:.0f} s to read; skipped to keep the live run short")
                    ex.shutdown(wait=False)
                else:
                    opt = optical_change(R.bbox, event, grid, R.dir)
                from .optical.indices import fuse_optical

                added = fuse_optical(R.dir, grid)
                opt = {**opt, **added}
                R.meta["radar"]["optical_added_km2"] = added.get("optical_added_km2", 0.0)
                log(f"Optical: {added.get('optical_added_km2', 0.0)} km² of stripped valley ground added next to radar damage")
                R.stage("optical", "done", seconds=round(time.time() - t, 1), **opt)
            except Exception as e:  # optical is a confirmation layer; the run never fails on it
                R.warn(f"Optical check skipped: {e}")
                R.stage("optical", "skipped", seconds=round(time.time() - t, 1))

            # ---- OpenStreetMap
            t = time.time()
            try:
                osm_stats = osm_future.result(timeout=900 if optical_budget_s is None else 420)   # live runs wait less
            except Exception as e:  # keep the flood map; damage and cut-off need OpenStreetMap
                R.warn(f"OpenStreetMap could not be read ({str(e)[:120]}); damage and cut-off are empty for this run")
                osm_stats = _empty_osm(R.dir)
            R.meta["osm"] = osm_stats
            R.stage("osm", "done", seconds=round(osm_stats.get("roads_s", 0) + osm_stats.get("points_s", 0) + osm_stats.get("buildings_s", 0), 1))

        # ---- damage
        t = time.time()
        R.stage("damage", "running")
        from .damage.exposure import assess

        dmg = assess(R.dir, grid)
        R.meta["damage"] = dmg
        R.stage("damage", "done", seconds=round(time.time() - t, 1))
        log(f"Damage: {dmg.get('buildings', {})} buildings; bridges {dmg.get('bridges', {})} ({time.time() - t:.0f} s)")

        # ---- access
        t = time.time()
        R.stage("access", "running")
        from .access.pipeline import run_access

        try:
            acc = run_access(R.dir)
        except Exception as e:  # e.g. no drivable road in the box: report it, keep the rest of the run
            R.warn(f"Cut-off analysis skipped: {str(e)[:160]}")
            acc = {"settlements_total": 0, "cutoff_total_p50_plus": 0, "note": "cut-off analysis skipped"}
        R.meta["access"] = {k: acc[k] for k in list(acc)[:40] if not isinstance(acc[k], (list, dict)) or k in ("top_repair",)}
        R.stage("access", "done", seconds=round(time.time() - t, 1))
        log(f"Cut-off analysis done ({time.time() - t:.0f} s)")

        # ---- model (water check)
        if with_model:
            t = time.time()
            R.stage("model", "running")
            try:
                from .model.infer import run_on_run_dir

                m = run_on_run_dir(R.dir)
                R.stage("model", "done", seconds=round(time.time() - t, 1), **(m or {}))
            except Exception as e:
                R.warn(f"Segmentation model skipped: {e}")
                R.stage("model", "skipped", seconds=round(time.time() - t, 1))
        try:   # full extent: detected flood plus model water and the inferred river corridor
            from .radar.extent import run_extent

            R.save()
            R.meta["extent"] = run_extent(R.dir)
        except Exception as e:
            R.warn(f"Full flood extent skipped: {e}")

        # ---- facts, report, previews
        for name, fn in (("facts", _facts), ("report", _report), ("previews", _previews)):
            t = time.time()
            R.stage(name, "running")
            try:
                fn(R.dir)
                R.stage(name, "done", seconds=round(time.time() - t, 1))
            except Exception as e:
                R.warn(f"{name} failed: {e}")
                R.stage(name, "failed", seconds=round(time.time() - t, 1), error=str(e)[:300])

        R.meta["status"] = "done"
    except Exception as e:
        R.meta["status"] = "failed"
        R.meta["error"] = str(e)
        R.meta["traceback"] = traceback.format_exc()[-2000:]
        log(f"Run failed: {e}")
    R.meta["runtime_seconds"] = round(time.time() - t_all, 1)
    R.meta["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    R.save()
    log(f"Finished in {R.meta['runtime_seconds']} s -> {R.dir}")
    return R.dir


def _empty_osm(run_dir: Path) -> dict:
    d = run_dir / "osm"
    d.mkdir(exist_ok=True)
    for f in ("roads", "bridges", "places", "health", "buildings"):
        p = d / f"{f}.geojson"
        if not p.exists():
            p.write_text('{"type": "FeatureCollection", "features": []}')
    return {"roads": 0, "bridges": 0, "places": 0, "health": 0, "buildings": 0, "error": "unavailable"}


def finish(run_id: str, with_model: bool = True) -> Path:
    """Re-run the stages after the radar map on an existing run (damage onward)."""
    d = RUNS / run_id
    meta = json.loads((d / "meta.json").read_text())
    grid = stac.Grid.from_json(json.loads((d / "grid.json").read_text()))
    t_all = time.time()
    from .damage.exposure import assess
    from .access.pipeline import run_access

    from .acquire.names import normalise_file

    for f in ("places", "health"):
        normalise_file(d / "osm" / f"{f}.geojson")
    def _access():
        acc = run_access(d)
        meta["access"] = {k: acc[k] for k in list(acc)[:40] if not isinstance(acc[k], (list, dict)) or k in ("top_repair",)}

    steps = [("damage", lambda: meta.__setitem__("damage", assess(d, grid))), ("access", _access)]
    if with_model:
        from .model.infer import run_on_run_dir

        steps.append(("model", lambda: run_on_run_dir(d)))

    def _extent():
        from .radar.extent import run_extent
        (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
        meta["extent"] = run_extent(d)
    steps.append(("extent", _extent))
    steps += [("facts", lambda: _facts(d)), ("report", lambda: _report(d)), ("previews", lambda: _previews(d))]
    meta["warnings"] = [w for w in meta.get("warnings", []) if "Optical" in w]
    for name, fn in steps:
        t = time.time()
        try:
            res = fn()
            meta["stages"][name] = {"state": "done", "seconds": round(time.time() - t, 1)} | (res if isinstance(res, dict) and name == "model" else {})
            log(f"{name} done ({time.time() - t:.0f} s)")
        except Exception as e:
            meta["stages"][name] = {"state": "failed", "seconds": round(time.time() - t, 1), "error": str(e)[:300]}
            meta["warnings"].append(f"{name} failed: {e}")
            log(f"{name} failed: {e}")
        (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    meta["status"] = "done"
    meta.pop("error", None)
    meta.pop("traceback", None)
    meta["runtime_seconds"] = round(meta.get("runtime_seconds", 0) + time.time() - t_all, 1)
    meta["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    return d


def _facts(run_dir: Path):
    from .copilot.ledger import build_facts

    (run_dir / "facts.json").unlink(missing_ok=True)   # rebuilt from the maps, so no stale figure survives
    build_facts(run_dir)


def _report(run_dir: Path):
    from .report.sitrep import build_sitrep

    build_sitrep(run_dir)


def _previews(run_dir: Path):
    from .previews import make_previews

    make_previews(run_dir)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="lifeline", description="Flood damage and cut-off settlements from free satellite data.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the full pipeline for an area and a flood date")
    r.add_argument("--bbox", nargs=4, type=float, required=True, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    r.add_argument("--date", required=True, help="flood date, YYYY-MM-DD")
    r.add_argument("--id", default=None)
    r.add_argument("--label", default="")
    r.add_argument("--orbits", type=int, default=3)
    r.add_argument("--no-model", action="store_true")
    r.add_argument("--no-optical", action="store_true")
    r.add_argument("--baseline", type=int, default=3, help="earlier passes per orbit used as the normal baseline")
    f = sub.add_parser("finish", help="re-run damage, cut-off, model, facts, report and previews on an existing run")
    f.add_argument("run_id")
    f.add_argument("--no-model", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "finish":
        finish(a.run_id, with_model=not a.no_model)
    if a.cmd == "run":
        w, s_, e, n = a.bbox
        if not (w < e and s_ < n):
            ap.error("--bbox must be WEST SOUTH EAST NORTH")
        if (e - w) * (n - s_) > 1.0:
            ap.error("that box is over 1 square degree (about 100 x 110 km); split it into smaller runs")
        run(a.bbox, dt.date.fromisoformat(a.date), a.id, a.label, max_orbits=a.orbits, baseline_passes=a.baseline, with_model=not a.no_model, with_optical=not a.no_optical)


if __name__ == "__main__":
    main()
