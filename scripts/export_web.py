"""Copy a finished run into the website's data folder, in the shapes the site reads.

    uv run python scripts/export_web.py trishuli-2026

Writes web/data/<run_id>/ (summary.json, overlays + PNGs, GeoJSON layers,
chokepoints, validation, copilot samples, situation reports) and updates
web/data/index.json. Values that a run did not produce are left out; the site
hides anything missing instead of showing a placeholder.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(p: Path, default=None):
    return json.loads(p.read_text()) if p.exists() else default


def slim_geojson(src: Path, dst: Path, keep: tuple[str, ...], where=None, round_to: int = 5) -> int:
    fc = load(src)
    if not fc:
        return 0
    feats = []
    for f in fc["features"]:
        if where and not where(f["properties"]):
            continue
        props = {k: f["properties"].get(k) for k in keep if k in f["properties"]}

        def rnd(c):
            return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(c[0], round_to), round(c[1], round_to)]

        geom = dict(f["geometry"])
        geom["coordinates"] = rnd(geom["coordinates"])
        feats.append({"type": "Feature", "geometry": geom, "properties": props})
    dst.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    return len(feats)


def _num(x):
    if isinstance(x, dict):
        for k in ("value", "rate", "estimate", "agreement"):
            if isinstance(x.get(k), (int, float)):
                return x[k]
        return None
    return x if isinstance(x, (int, float)) else None


def web_validation(v: dict) -> dict:
    """Reshape the checker's output into the table the website shows."""
    aois = []
    for a in v.get("aois", []):
        row = {"id": a.get("aoi"), "name": a.get("name")}
        if a.get("result") != "compared":
            row |= {"status": "cancelled" if "cancel" in str(a.get("status", "")) + str(a.get("result", "")) else "not compared", "note": a.get("result")}
            aois.append(row)
            continue
        ext = a.get("extent", {}).get("all_pixels", {})
        b = a.get("buildings", {})
        r = a.get("roads", {})
        br = a.get("bridges", {})
        row |= {
            "status": "compared",
            "flood_iou": ext.get("iou"),
            "flood_f1": ext.get("f1"),
            "flood_precision": ext.get("precision"),
            "flood_recall": ext.get("recall"),
            "buildings_hit_rate": _num(b.get("hit_rate_destroyed_damaged")),
            "roads_agree_rate": _num((r.get("destroyed_damaged_vs_no_visible_damage") or {}).get("agreement")),
            "bridges_flagged": br.get("flagged"),
            "bridges_total": br.get("n_ems_destroyed_damaged"),
            "role": "development area" if a.get("aoi") == "AOI03" else "held out",
        }
        aois.append(row)
    pooled = v.get("pooled", {})
    ext = pooled.get("extent_all_pixels", {})
    return {
        "reference": f"Copernicus EMS rapid mapping, activation {v.get('activation', '')} ({v.get('stage', 'first')} grading maps)",
        "reference_note": "Official maps are read only by the checker, after the run. They are never an input. Settings were chosen on area 3 (Bidur) only; the other delivered areas are held out.",
        "aois": aois,
        "pooled": {
            "flood_iou": ext.get("iou"),
            "flood_f1": ext.get("f1"),
            "flood_precision": ext.get("precision"),
            "flood_recall": ext.get("recall"),
            "buildings_hit_rate": _num(pooled.get("buildings", {}).get("hit_rate_destroyed_damaged")),
            "roads_agree_rate": _num(pooled.get("roads", {}).get("agreement")),
            "roads_recall": _num(pooled.get("roads", {}).get("recall_length")),
            "bridges_flagged": pooled.get("bridges", {}).get("flagged"),
            "bridges_total": pooled.get("bridges", {}).get("n_ems_destroyed_damaged"),
        },
        "notes": v.get("notes", []),
        "credit": "European Union, Copernicus Emergency Management Service data",
    }


def _areas_from_map(run: Path, radar: dict) -> dict:
    """Areas from the final flood map (radar plus optical fusion), falling back to the radar stage."""
    p = run / "rasters" / "flood_class.tif"
    try:
        import numpy as np
        import rasterio

        with rasterio.open(p) as src:
            c = src.read(1)
            px = abs(src.transform.a * src.transform.e) / 1e6
        return {"area_debris_km2": round(float((c == 2).sum() * px), 2), "area_water_km2": round(float((c == 1).sum() * px), 2),
                "area_unobserved_km2": round(float((c == 255).sum() * px), 2),
                "area_optical_added_km2": radar.get("optical_added_km2")}
    except Exception:
        return {"area_debris_km2": radar.get("debris_km2"), "area_water_km2": radar.get("water_km2"),
                "area_unobserved_km2": radar.get("unobserved_km2")}


def main(run_id: str, out: Path | None = None, update_index: bool = True):
    run = ROOT / "runs" / run_id
    out = out or ROOT / "web" / "data" / run_id
    out.mkdir(parents=True, exist_ok=True)
    meta = load(run / "meta.json", {})
    dmg = load(run / "damage" / "summary.json", {})
    acc = load(run / "access" / "access_summary.json", {})
    # results of the after-the-fact check against the official maps (never an input)
    val = next((load(f) for f in sorted((run / "validation").glob("*.json")) if "latest" not in f.name), None) if (run / "validation").exists() else None
    red = load(run / "copilot_redteam.json")
    bbox = meta.get("bbox", [0, 0, 0, 0])

    # map layers; radar backdrops become JPEG to keep the page light
    from PIL import Image

    overlays = load(run / "web" / "overlays.json", [])
    for ov in overlays:
        src = run / "web" / ov["url"]
        if not src.exists():
            continue
        if ov["id"] in ("before", "after"):
            im = Image.open(src).convert("RGBA")
            bg = Image.new("RGB", im.size, (20, 20, 22))
            bg.paste(im, mask=im.split()[3])
            name = src.stem + ".jpg"
            bg.save(out / name, quality=78, optimize=True, progressive=True)
            ov["url"] = name
        else:
            shutil.copy(src, out / src.name)
    (out / "overlays.json").write_text(json.dumps(overlays, indent=1))
    n_roads = slim_geojson(run / "damage" / "roads.geojson", out / "roads.geojson", ("name", "highway", "status", "hit_prob", "bridge", "length_m"), where=lambda p: p.get("status") in ("destroyed_likely", "damaged_possible", "unobserved") or p.get("highway") in ("trunk", "primary", "secondary"))
    slim_geojson(run / "damage" / "bridges.geojson", out / "bridges.geojson", ("name", "status", "hit_prob", "highway"))
    slim_geojson(run / "damage" / "buildings.geojson", out / "buildings_hit.geojson", ("status", "hit_prob"), where=lambda p: p.get("status") in ("destroyed_likely", "damaged_possible"))
    slim_geojson(run / "access" / "settlements.geojson", out / "settlements.geojson", ("name", "name_ne", "place", "population", "p_cutoff", "cutoff", "nearest_health", "minutes_before", "minutes_after", "walk_minutes_after", "reason"))
    slim_geojson(run / "osm" / "health.geojson", out / "health.geojson", ("name", "kind"))
    fp = load(run / "access" / "flowpath.geojson")
    if fp:
        st = {f["properties"].get("name"): f["properties"] for f in (load(run / "access" / "settlements.geojson") or {"features": []})["features"]}
        feats = []
        for f in fp["features"]:
            p = f["properties"]
            if p.get("kind") == "settlement":
                s_ = st.get(p.get("name"), {})
                f = {"type": "Feature", "geometry": f["geometry"], "properties": {"kind": "village", "name": p.get("name"), "name_ne": p.get("name_ne") or s_.get("name_ne", ""), "km_downstream": p.get("km"), "order": p.get("order"), "p_cutoff": s_.get("p_cutoff")}}
            feats.append(f)
        (out / "flowpath.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    for name in ("chokepoints.json",):
        if (run / "access" / name).exists():
            shutil.copy(run / "access" / name, out / name)
    for name in ("flowpath.geojson", "validation.json", "copilot_samples.json", "optical.json"):
        if not (out / name).exists():   # optional for this run: an explicit empty file keeps the browser console clean
            (out / name).write_text("null")
    for pdf in ("sitrep_en.pdf", "sitrep_ne.pdf"):
        if (run / pdf).exists():
            shutil.copy(run / pdf, out / pdf)
    if val:
        (out / "validation.json").write_text(json.dumps(web_validation(val), indent=1))
    facts = load(run / "facts.json")
    if facts:
        items = facts if isinstance(facts, list) else facts.get("facts", [])
        slim = [{k: f.get(k) for k in ("id", "key", "value", "unit", "label_en", "label_ne")} for f in items]
        (out / "facts.json").write_text(json.dumps(slim, ensure_ascii=False, separators=(",", ":")))
    samples = load(run / "copilot_samples.json")
    if samples:
        (out / "copilot_samples.json").write_text(json.dumps(samples, indent=1, ensure_ascii=False))
    model = load(ROOT / "runs" / "model" / "metrics.json")
    if model:
        (out.parent / "model_metrics.json").write_text(json.dumps(model, indent=1))

    # the scoreboard
    radar = meta.get("radar", {})
    osm = meta.get("osm", {})
    b = dmg.get("bridges", {})
    bld = dmg.get("buildings", {})
    roads_km = dmg.get("roads", {})
    summary = {
        "run_id": run_id,
        "title": meta.get("label", run_id),
        "event_date": meta.get("event_date"),
        "generated_at": meta.get("finished_at"),
        # a cached rerun is not a fair time; only a measured cold run is shown
        "runtime_seconds": meta.get("cold_runtime_seconds"),
        "radar_passes": meta.get("stages", {}).get("images", {}).get("passes"),
        "radar_tracks": len(meta.get("radar_pairs", [])),
        "buildings_checked": bld.get("checked") or osm.get("buildings"),
        "bridges_assessed": osm.get("bridges"),
        "roads_km_checked": osm.get("roads_km"),
        "villages_routed": acc.get("settlements_with_road_before") or acc.get("settlements_total") or osm.get("places"),
        "health_facilities": osm.get("health"),
        "villages_cut_off": acc.get("cutoff_total_p50_plus"),
        "villages_at_risk": acc.get("at_risk_p10_to_50"),
        **_areas_from_map(run, radar),
        "osm_snapshot": osm.get("snapshot"),
        "radar_track_labels": [f"{p.get('orbit')} {p.get('direction')}" for p in meta.get("radar_pairs", [])],
        "roads_destroyed_km": round(roads_km.get("destroyed_likely", 0.0), 1) if roads_km else None,
        "roads_under_debris_km": dmg.get("roads_under_damage_km"),
        "bridges_destroyed": b.get("destroyed_likely"),
        "buildings_hit": (bld.get("destroyed_likely", 0) + bld.get("damaged_possible", 0)) if bld else None,
        "copilot_caught": red.get("fabrications_caught") if red else None,
        "copilot_attempts": red.get("attempts") if red else None,
        "bounds": bbox,
        "center": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
        "radar_pairs": meta.get("radar_pairs", []),
        "stages": meta.get("stages", {}),
        "warnings": meta.get("warnings", []),
    }
    if val:
        wv = web_validation(val)["pooled"]
        summary["check_buildings_hit_rate"] = wv["buildings_hit_rate"]
        summary["check_bridges_flagged"] = wv["bridges_flagged"]
        summary["check_bridges_total"] = wv["bridges_total"]
        summary["check_flood_f1"] = wv["flood_f1"]
        summary["check_flood_precision"] = wv["flood_precision"]
        summary["check_roads_recall"] = wv["roads_recall"]
    summary = {k: v for k, v in summary.items() if v is not None}
    (out / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))

    if not update_index:
        print(f"exported {run_id} -> {out}")
        return summary
    idx_path = out.parent / "index.json"
    idx = load(idx_path, {"default": run_id, "runs": []})
    if isinstance(idx, list):
        idx = {"default": run_id, "runs": idx}
    idx["runs"] = [r for r in idx["runs"] if r.get("run_id") != run_id] + [{"run_id": run_id, "title": summary.get("title"), "area_name": summary.get("title"), "event_date": summary.get("event_date"), "path": f"{run_id}/", "mock": False}]
    idx_path.write_text(json.dumps(idx, indent=1, ensure_ascii=False))
    print(f"exported {run_id}: {n_roads} roads, summary keys {len(summary)} -> {out}")
    return summary


if __name__ == "__main__":
    main(sys.argv[1])
