"""OpenStreetMap exactly as it stood before the flood, from the ohsome API.

The brief names the ohsome API and a pre-event snapshot. Its plain geometry
endpoint needs a key, so line features come from the full-history endpoint
(keyless): every version of every way, from which we keep the version that was
valid at the snapshot time. Point features use the keyless bounding-box
endpoint. Large areas are split into tiles and fetched in parallel.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from shapely.geometry import shape, mapping

from .names import normalise_file

API = "https://api.ohsome.org/v1"
LATEST_SNAPSHOT = dt.date(2026, 7, 27)  # newest data the API holds, per the brief
UA = {"User-Agent": "lifeline-flood-mapper/0.1 (student hackathon project)"}

ROAD_FILTER = (
    "type:way and highway in (motorway, trunk, primary, secondary, tertiary, unclassified, residential, "
    "service, track, motorway_link, trunk_link, primary_link, secondary_link, tertiary_link, living_street, "
    "path, footway, steps, bridleway)"
)
PLACE_FILTER = "place in (city, town, village, hamlet, isolated_dwelling, locality, suburb, neighbourhood)"
HEALTH_FILTER = "amenity in (hospital, clinic, doctors) or healthcare in (hospital, clinic, centre, doctor)"
BUILDING_FILTER = "building=* and geometry:polygon"


def snapshot_for(event: dt.date) -> dt.date:
    """The day before the event, capped at the newest data available."""
    return min(event - dt.timedelta(days=1), LATEST_SNAPSHOT)


def _tiles(bbox, n: int) -> list[tuple[float, float, float, float]]:
    w, s, e, nn = bbox
    dx, dy = (e - w) / n, (nn - s) / n
    return [(w + i * dx, s + j * dy, w + (i + 1) * dx, s + (j + 1) * dy) for i in range(n) for j in range(n)]


def _post(endpoint: str, data: dict, attempts: int = 5) -> dict:
    for k in range(attempts):
        try:
            r = requests.post(f"{API}/{endpoint}", data=data, headers=UA, timeout=300)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(3 * (k + 1))
                continue
            raise RuntimeError(f"ohsome {endpoint} {r.status_code}: {r.text[:300]}")
        except requests.RequestException:
            time.sleep(3 * (k + 1))
    raise RuntimeError(f"ohsome {endpoint} failed after {attempts} attempts")


def _bbox_str(b) -> str:
    return ",".join(f"{v:.6f}" for v in b)


def _history_lines(bbox, when: dt.date, filt: str, tiles: int) -> list[dict]:
    """Ways valid at `when`, from the full-history endpoint."""
    t_from = (when - dt.timedelta(days=1)).isoformat()
    t_to = when.isoformat()
    stamp = f"{t_to}T00:00:00Z"

    def one(tb):
        js = _post(
            "elementsFullHistory/geometry",
            {"bboxes": _bbox_str(tb), "time": f"{t_from},{t_to}", "filter": filt, "properties": "tags,metadata", "clipGeometry": "false"},
        )
        keep = []
        for f in js.get("features", []):
            p = f["properties"]
            if p.get("@validFrom", "") <= stamp <= p.get("@validTo", "9999"):
                keep.append(f)
        return keep

    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=6) as ex:
        for feats in ex.map(one, _tiles(bbox, tiles)):
            for f in feats:
                oid = f["properties"].get("@osmId")
                if oid in seen:
                    continue
                seen.add(oid)
                out.append(f)
    return out


def _bbox_points(bbox, when: dt.date, filt: str, tiles: int) -> list[dict]:
    """Features as points (bounding-box centres), from the keyless bbox endpoint."""

    def one(tb):
        js = _post("elements/bbox", {"bboxes": _bbox_str(tb), "time": when.isoformat(), "filter": filt, "properties": "tags", "clipGeometry": "false"})
        return js.get("features", [])

    out, seen = [], set()
    with ThreadPoolExecutor(max_workers=6) as ex:
        for feats in ex.map(one, _tiles(bbox, tiles)):
            for f in feats:
                oid = f["properties"].get("@osmId")
                if oid in seen:
                    continue
                seen.add(oid)
                c = shape(f["geometry"]).centroid
                out.append({"type": "Feature", "geometry": mapping(c), "properties": f["properties"]})
    return out


def _length_m(geom) -> float:
    # equirectangular is plenty for road segments of a few km
    coords = list(geom.coords) if geom.geom_type == "LineString" else [c for g in geom.geoms for c in g.coords]
    total = 0.0
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        kx = 111_320 * math.cos(math.radians((y0 + y1) / 2))
        total += math.hypot((x1 - x0) * kx, (y1 - y0) * 110_540)
    return total


def _fc(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def fetch_osm(bbox, event: dt.date, out_dir: Path, log=print) -> dict:
    """Write roads, bridges, buildings, places and health facilities as GeoJSON."""
    out_dir.mkdir(parents=True, exist_ok=True)
    when = snapshot_for(event)
    area_deg2 = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    tiles = max(1, min(6, math.ceil(math.sqrt(area_deg2 / 0.02))))
    stats = {"snapshot": when.isoformat(), "tiles": tiles * tiles}

    t = time.time()
    raw_roads = _history_lines(bbox, when, ROAD_FILTER, tiles)
    roads, bridges = [], []
    for f in raw_roads:
        g = shape(f["geometry"])
        if g.geom_type not in ("LineString", "MultiLineString"):
            continue
        tags = f["properties"]
        is_bridge = tags.get("bridge") not in (None, "no")
        props = {
            "osm_id": tags.get("@osmId"),
            "highway": tags.get("highway"),
            "name": tags.get("name") or tags.get("name:en") or "",
            "name_ne": tags.get("name:ne", ""),
            "bridge": bool(is_bridge),
            "length_m": round(_length_m(g), 1),
            "surface": tags.get("surface", ""),
        }
        feat = {"type": "Feature", "geometry": f["geometry"], "properties": props}
        roads.append(feat)
        if is_bridge:
            bridges.append(feat)
    (out_dir / "roads.geojson").write_text(json.dumps(_fc(roads)))
    (out_dir / "bridges.geojson").write_text(json.dumps(_fc(bridges)))
    stats.update(roads=len(roads), bridges=len(bridges), roads_km=round(sum(r["properties"]["length_m"] for r in roads) / 1000, 1), roads_s=round(time.time() - t, 1))
    log(f"  OpenStreetMap roads: {len(roads)} ways, {stats['roads_km']} km, {len(bridges)} bridges ({stats['roads_s']} s)")

    t = time.time()
    places = []
    for f in _bbox_points(bbox, when, PLACE_FILTER, max(1, tiles // 2)):
        tags = f["properties"]
        name = tags.get("name") or tags.get("name:en")
        if not name:
            continue
        pop = tags.get("population")
        try:
            pop = int(str(pop).replace(",", "")) if pop else None
        except ValueError:
            pop = None
        places.append({"type": "Feature", "geometry": f["geometry"], "properties": {"osm_id": tags.get("@osmId"), "name": name, "name_ne": tags.get("name:ne", ""), "place": tags.get("place"), "population": pop}})
    (out_dir / "places.geojson").write_text(json.dumps(_fc(places)))
    normalise_file(out_dir / "places.geojson")

    health = []
    for f in _bbox_points(bbox, when, HEALTH_FILTER, max(1, tiles // 2)):
        tags = f["properties"]
        kind = tags.get("amenity") or tags.get("healthcare") or "clinic"
        kind = {"doctors": "clinic", "doctor": "clinic", "centre": "health_post"}.get(kind, kind)
        health.append({"type": "Feature", "geometry": f["geometry"], "properties": {"osm_id": tags.get("@osmId"), "name": tags.get("name") or tags.get("name:en") or f"Unnamed {kind}", "kind": kind}})
    (out_dir / "health.geojson").write_text(json.dumps(_fc(health)))
    normalise_file(out_dir / "health.geojson")
    stats.update(places=len(places), health=len(health), points_s=round(time.time() - t, 1))
    log(f"  OpenStreetMap places: {len(places)} named settlements, {len(health)} health facilities ({stats['points_s']} s)")

    t = time.time()
    buildings = [
        {"type": "Feature", "geometry": f["geometry"], "properties": {"osm_id": f["properties"].get("@osmId")}}
        for f in _bbox_points(bbox, when, BUILDING_FILTER, tiles)
    ]
    (out_dir / "buildings.geojson").write_text(json.dumps(_fc(buildings)))
    stats.update(buildings=len(buildings), buildings_s=round(time.time() - t, 1))
    log(f"  OpenStreetMap buildings: {len(buildings)} ({stats['buildings_s']} s)")
    return stats
