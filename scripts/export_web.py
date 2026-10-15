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


def _extent_check() -> dict:
    """Full-extent scores (detected + model water + inferred river corridor) from validation/extent.json, by AOI id."""
    p = ROOT / "runs" / "trishuli-2026" / "validation" / "extent.json"
    if not p.exists():
        return {}
    e = json.loads(p.read_text()).get("full_extent", {})
    out = {k.split()[0]: a.get("f1") for k, a in e.get("areas", {}).items()}
    out["held_out_pooled"] = e.get("held_out_pooled", {}).get("f1")
    return out


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
            "role": "development area (settings tuned here)" if a.get("aoi") == "AOI03" else "held out",
            "flood_f1_full": _extent_check().get(a.get("aoi")),
        }
        aois.append(row)
    pooled = v.get("pooled", {})
    ext = pooled.get("extent_all_pixels", {})
    # held-out areas only: the fairer estimate for a new valley (area 3 was used to choose settings)
    tp = fp = fn = bf = bn = brf = brn = 0
    for a in v.get("aois", []):
        if a.get("result") != "compared" or a.get("aoi") == "AOI03":
            continue
        px = a.get("extent", {}).get("all_pixels", {}).get("pixels", {})
        tp += px.get("tp", 0); fp += px.get("fp", 0); fn += px.get("fn", 0)
        h = a.get("buildings", {}).get("hit_rate_destroyed_damaged", {})
        if isinstance(h, dict):
            bf += h.get("flagged", 0) or 0; bn += h.get("n", 0) or 0
        brf += a.get("bridges", {}).get("flagged", 0) or 0
        brn += a.get("bridges", {}).get("n_ems_destroyed_damaged", 0) or 0
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    held = {"flood_f1": round(2 * prec * rec / (prec + rec), 3) if prec and rec else None,
            "flood_precision": round(prec, 3) if prec else None, "flood_recall": round(rec, 3) if rec else None,
            "buildings_hit_rate": round(bf / bn, 3) if bn else None, "buildings_flagged": bf, "buildings_total": bn,
            "bridges_flagged": brf, "bridges_total": brn, "areas": "AOI01, AOI02, AOI05"}
    return {
        "reference": f"Copernicus EMS rapid mapping, activation {v.get('activation', '')} ({v.get('stage', 'first')} grading maps)",
        "reference_note": "Official maps are read only by the checker, after the run; the pipeline never reads them while it runs. Settings were tuned on area 3 (Bidur) only; the other delivered areas are held out.",
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
        "held_out": held,
        "held_out_full_extent_f1": _extent_check().get("held_out_pooled"),
        "notes": v.get("notes", []),
        "credit": "European Union, Copernicus Emergency Management Service data",
    }


def _kml(features, name_of, style) -> str:
    """Minimal KML that Google Earth and phone map apps open."""
    from xml.sax.saxutils import escape

    marks = []
    for f in features:
        g = f.get("geometry") or {}
        nm = escape(str(name_of(f.get("properties") or {})))
        if g.get("type") == "Point":
            x, y = g["coordinates"][:2]
            geom = f"<Point><coordinates>{x:.6f},{y:.6f}</coordinates></Point>"
        elif g.get("type") in ("LineString", "MultiLineString"):
            lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
            geom = "<MultiGeometry>" + "".join("<LineString><coordinates>" + " ".join(f"{x:.6f},{y:.6f}" for x, y, *_ in ln) + "</coordinates></LineString>" for ln in lines) + "</MultiGeometry>"
        else:
            continue
        marks.append(f"<Placemark><name>{nm}</name><styleUrl>#s</styleUrl>{geom}</Placemark>")
    return ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
            f'<Style id="s">{style}</Style>' + "".join(marks) + "</Document></kml>")


def _field_kit(run: Path, out: Path) -> None:
    """Copy-paste alert text and files a field team can open on a phone or in Google Earth."""
    try:
        from lifeline.report.alert import build_alert

        a = build_alert(run)
        (out / "alert.json").write_text(json.dumps(a, ensure_ascii=False))
    except Exception as e:  # the site works without it
        print("alert skipped:", e)
    st = load(run / "access" / "settlements.geojson")
    if st:
        cut = [f for f in st["features"] if (f["properties"].get("p_cutoff") or 0) >= 0.5]
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": f["geometry"], "properties": {k: f["properties"].get(k) for k in ("name", "name_ne", "p_cutoff", "nearest_health", "minutes_before", "walk_minutes_after")}} for f in cut]}
        (out / "cutoff_villages.geojson").write_text(json.dumps(fc, ensure_ascii=False))
        (out / "cutoff_villages.kml").write_text(_kml(cut, lambda p: f"{p.get('name')} (cut off, p={p.get('p_cutoff', 0):.2f})",
                                                       "<IconStyle><color>ff2b39c0</color><scale>1.1</scale></IconStyle>"))
    rd = load(run / "damage" / "roads.geojson")
    if rd:
        bad = [f for f in rd["features"] if f["properties"].get("status") in ("destroyed_likely", "damaged_possible")]
        fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": f["geometry"], "properties": {k: f["properties"].get(k) for k in ("name", "highway", "status", "hit_prob")}} for f in bad]}
        (out / "damaged_roads.geojson").write_text(json.dumps(fc, ensure_ascii=False))
        (out / "damaged_roads.kml").write_text(_kml(bad, lambda p: f"{p.get('name') or p.get('highway') or 'road'} ({p.get('status')})",
                                                     "<LineStyle><color>ff2b39c0</color><width>4</width></LineStyle>"))
    ch = run / "access" / "chokepoints.json"
    if ch.exists():
        c = json.loads(ch.read_text())
        c = c.get("ranked", c) if isinstance(c, dict) else c
        feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [x["lon"], x["lat"]]},
                  "properties": {"rank": x["rank"], "name": x["name"], "villages_reconnected": x["villages_reconnected"]}} for x in c]
        (out / "repairs.kml").write_text(_kml(feats, lambda p: f"#{p['rank']} {p['name']}: reconnects {p['villages_reconnected']}",
                                              "<IconStyle><color>ff2b79c8</color><scale>1.3</scale></IconStyle>"))


def _areas_from_map(run: Path, radar: dict) -> dict:
    """Areas from the final flood map (radar plus optical fusion), falling back to the radar stage."""
    p = run / "rasters" / "flood_class.tif"
    try:
        import numpy as np
        import rasterio

        with rasterio.open(p) as src:
            c = src.read(1)
            px = abs(src.transform.a * src.transform.e) / 1e6
        ext = load(run / "meta.json", {}).get("extent") or {}
        return {"area_debris_km2": round(float((c == 2).sum() * px), 2), "area_water_km2": round(float((c == 1).sum() * px), 2),
                "area_unobserved_km2": round(float((c == 255).sum() * px), 2),
                "area_optical_added_km2": radar.get("optical_added_km2"),
                "area_model_water_km2": ext.get("model_water_km2"), "area_corridor_km2": ext.get("corridor_km2")}
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
    _field_kit(run, out)
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
        "villages_routed": acc.get("settlements_total") or osm.get("places"),
        "villages_with_road_before": acc.get("settlements_with_road_before"),
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
        "buildings_destroyed": bld.get("destroyed_likely") if bld else None,
        "copilot_caught": red.get("fabrications_caught") if red else None,
        "copilot_attempts": red.get("attempts") if red else None,
        "bounds": bbox,
        "center": [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2],
        "radar_pairs": meta.get("radar_pairs", []),
        "stages": meta.get("stages", {}),
        "warnings": meta.get("warnings", []),
    }
    if val:
        full = web_validation(val)
        wv, ho = full["pooled"], full["held_out"]
        summary["check_buildings_hit_rate"] = ho["buildings_hit_rate"]
        summary["check_bridges_flagged"] = ho["bridges_flagged"]
        summary["check_bridges_total"] = ho["bridges_total"]
        summary["check_heldout_f1"] = ho["flood_f1"]
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
