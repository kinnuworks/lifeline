"""Download and normalise the Copernicus EMS Rapid Mapping products for EMSR927.

These layers are reference data for checking results only. They are written to
data/reference/emsr927/ and are read by lifeline.validate.compare and nothing else.

Output files (all EPSG:4326):
  activation.json     the raw public activation record from the EMS dashboard API
  raw/<product>/      the unzipped vector packages, as published
  features.geojson    every graded feature in a common schema (see NORMALISED_COLUMNS)
  aois.geojson        all six AOIs, including the cancelled ones, with a status column
  SOURCES.md          URLs, product ids, image dates and the required credit
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely import wkt

ACTIVATION = "EMSR927"
API_URL = (
    "https://rapidmapping.emergency.copernicus.eu/backend/dashboard-api/"
    f"public-activations/?code={ACTIVATION}"
)
PORTAL_URL = f"https://rapidmapping.emergency.copernicus.eu/{ACTIVATION}/"
CREDIT = "European Union, Copernicus Emergency Management Service data"

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "data" / "reference" / "emsr927"

GRADES = ("destroyed", "damaged", "possibly_damaged", "no_visible_damage", "not_analysed")
NORMALISED_COLUMNS = [
    "layer",        # observed_event | built_up | transportation | bridge | facility | not_analysed
    "aoi",          # AOI01 ... AOI06
    "grade",        # one of GRADES; empty for observed_event (an extent has no grade)
    "kind",         # EMS obj_type, or event type for observed_event
    "detail",       # EMS info / obj_desc
    "name",
    "product",      # GRA_PRODUCT or GRA_MONIT01 ...
    "product_id",   # EMS product id from the API
    "stage",        # "first" for the first grading, "monitoring" for updates
    "image_dates",  # post-event image acquisition times used by EMS, comma separated
    "source_file",
    "geometry",
]

_UA = {"User-Agent": "Mozilla/5.0 (Lifeline validation; contact via project repo)"}


def normalise_grade(value) -> str:
    s = str(value or "").strip().lower().replace("-", " ")
    s = re.sub(r"\s+", " ", s)
    if s.startswith("destroyed"):
        return "destroyed"
    if s.startswith("possibly"):
        return "possibly_damaged"
    if s.startswith("damaged"):
        return "damaged"
    if s.startswith("no visible"):
        return "no_visible_damage"
    return "not_analysed"


def _get(url: str, *, tries: int = 4, timeout: int = 180) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=_UA, timeout=timeout)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
        except requests.RequestException as e:  # network hiccup, retry
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"could not download {url}: {last}")


def fetch_activation(out: Path) -> dict:
    r = _get(API_URL, timeout=60)
    data = r.json()
    rec = data["results"][0] if "results" in data else data
    (out / "activation.json").write_text(json.dumps(rec, indent=1))
    return rec


def _layer_for(layer_key: str, row: pd.Series) -> str | None:
    info = str(row.get("info", "") or "")
    simplified = str(row.get("simplified", "") or "").lower()
    is_bridge = info.startswith("2141") or "bridge" in simplified
    if layer_key.startswith("observedEvent"):
        return "observed_event"
    if layer_key.startswith("builtUp"):
        return "built_up"
    if layer_key.startswith("transportation"):
        return "bridge" if is_bridge else "transportation"
    if layer_key.startswith("facilities"):
        return "facility"
    if layer_key.startswith("notAnalysed"):
        return "not_analysed"
    return None


def parse_product_dir(pdir: Path, *, aoi: str, product: str, product_id: int,
                      stage: str, image_dates: str) -> tuple[gpd.GeoDataFrame, dict]:
    """Read every graded shapefile in one unzipped product into the common schema."""
    frames = []
    notes: dict = {"layers": {}, "dropped_null_geometry": 0}
    for shp in sorted(pdir.rglob("*.shp")):
        m = re.search(r"_(?P<key>[A-Za-z]+[APL])_v\d+$", shp.stem)
        if not m:
            continue
        key = m.group("key")
        if key.startswith(("areaOfInterest", "imageFootprint")):
            continue
        g = gpd.read_file(shp)
        if g.crs is None:
            g = g.set_crs(4326)
        g = g.to_crs(4326)
        n0 = len(g)
        g = g[g.geometry.notna() & ~g.geometry.is_empty]
        notes["dropped_null_geometry"] += n0 - len(g)
        if g.empty:
            notes["layers"][key] = 0
            continue
        rows = []
        for _, row in g.iterrows():
            layer = _layer_for(key, row)
            if layer is None:
                continue
            if layer == "observed_event":
                grade = ""
                kind = str(row.get("event_type", "") or "")
                detail = str(row.get("obj_desc", "") or "")
            elif layer == "not_analysed":
                grade = "not_analysed"
                kind = str(row.get("obj_type", "") or "not analysed")
                detail = str(row.get("info", "") or "")
            else:
                grade = normalise_grade(row.get("damage_gra"))
                kind = str(row.get("obj_type", "") or "")
                detail = str(row.get("info", "") or "")
            rows.append({
                "layer": layer, "aoi": aoi, "grade": grade, "kind": kind,
                "detail": detail.replace("Â", "").replace("\xa0", " ").strip(),
                "name": str(row.get("name", "") or ""), "product": product,
                "product_id": int(product_id), "stage": stage,
                "image_dates": image_dates, "source_file": shp.name,
                "geometry": row.geometry,
            })
        notes["layers"][key] = len(rows)
        if rows:
            frames.append(gpd.GeoDataFrame(rows, geometry="geometry", crs=4326))
    if frames:
        gdf = pd.concat(frames, ignore_index=True)
        gdf = gpd.GeoDataFrame(gdf, geometry="geometry", crs=4326)
    else:
        gdf = gpd.GeoDataFrame(columns=NORMALISED_COLUMNS, geometry="geometry", crs=4326)
    return gdf[NORMALISED_COLUMNS], notes


def _aoi_polygon_from_zip(pdir: Path):
    shp = next(iter(sorted(pdir.rglob("*areaOfInterestA*.shp"))), None)
    if shp is None:
        return None
    g = gpd.read_file(shp)
    g = g.set_crs(4326) if g.crs is None else g.to_crs(4326)
    return g.geometry.union_all()


def fetch_all(out: Path = DEFAULT_OUT, *, offline: bool = False) -> dict:
    """Download every EMSR927 vector package and write the normalised layers.

    offline=True reuses activation.json and raw/ already on disk.
    """
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "raw"
    raw.mkdir(exist_ok=True)
    if offline and (out / "activation.json").exists():
        rec = json.loads((out / "activation.json").read_text())
    else:
        rec = fetch_activation(out)

    feats = []
    aoi_rows = []
    log = []
    for a in sorted(rec["aois"], key=lambda a: a["number"]):
        aoi = f"AOI{int(a['number']):02d}"
        prods = sorted(a.get("products", []), key=lambda p: (p.get("monitoringNumber") or 0))
        delivered = [p for p in prods if p.get("feasible") and p.get("downloadPath")]
        aoi_poly = wkt.loads(a["extent"]) if a.get("extent") else None
        for p in prods:
            entry = {
                "aoi": aoi, "aoi_name": a["name"], "product_id": p["id"],
                "type": p.get("type"), "monitoring_number": p.get("monitoringNumber") or 0,
                "feasible": bool(p.get("feasible")), "url": p.get("downloadPath") or "",
                "image_dates": [i.get("acquisitionTime") for i in p.get("images", []) if i.get("acquisitionTime")],
                "sensors": [i.get("sensorName") for i in p.get("images", []) if i.get("sensorName")],
                "status": "", "layers": {}, "dropped_null_geometry": 0,
            }
            if not entry["feasible"] or not entry["url"]:
                entry["status"] = "cancelled or not feasible, no product delivered"
                log.append(entry)
                continue
            url = entry["url"]
            zname = url.rsplit("/", 1)[-1]
            product = "GRA_PRODUCT" if not entry["monitoring_number"] else f"GRA_MONIT{entry['monitoring_number']:02d}"
            pdir = raw / zname.removesuffix(".zip")
            if not (offline and pdir.exists()):
                r = _get(url)
                pdir.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                    for member in zf.namelist():
                        # vector parts only; skip map PDFs/JPGs and anything with a path escape
                        if ".." in member or member.startswith("/"):
                            continue
                        if not re.search(r"\.(shp|shx|dbf|prj|cpg|json|xml|xlsx)$", member, re.I):
                            continue
                        target = pdir / Path(member).name
                        target.write_bytes(zf.read(member))
                entry["bytes"] = len(r.content)
            stage = "first" if not entry["monitoring_number"] else "monitoring"
            gdf, notes = parse_product_dir(
                pdir, aoi=aoi, product=product, product_id=p["id"], stage=stage,
                image_dates=",".join(entry["image_dates"]))
            entry.update(notes)
            entry["product"] = product
            entry["status"] = "delivered"
            feats.append(gdf)
            poly = _aoi_polygon_from_zip(pdir)
            if poly is not None:
                aoi_poly = poly
            log.append(entry)
        aoi_rows.append({
            "aoi": aoi, "name": a["name"],
            "status": "delivered" if delivered else "cancelled",
            "products": ",".join(
                ("GRA_PRODUCT" if not (p.get("monitoringNumber") or 0) else f"GRA_MONIT{p['monitoringNumber']:02d}")
                for p in delivered),
            "geometry": aoi_poly,
        })

    features = pd.concat(feats, ignore_index=True) if feats else pd.DataFrame(columns=NORMALISED_COLUMNS)
    features = gpd.GeoDataFrame(features, geometry="geometry", crs=4326)
    features.to_file(out / "features.geojson", driver="GeoJSON")
    aois = gpd.GeoDataFrame(aoi_rows, geometry="geometry", crs=4326)
    aois.to_file(out / "aois.geojson", driver="GeoJSON")
    (out / "download_log.json").write_text(json.dumps(log, indent=1, default=str))
    write_sources(out, rec, log, features)
    return {"features": len(features), "aois": aoi_rows, "log": log}


def summary_table(features: gpd.GeoDataFrame) -> pd.DataFrame:
    f = features.copy()
    f["grade"] = f["grade"].replace("", "(extent)")
    return (f.groupby(["aoi", "product", "layer", "grade"]).size()
            .rename("count").reset_index())


def write_sources(out: Path, rec: dict, log: list, features: gpd.GeoDataFrame) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Reference data: Copernicus EMS {ACTIVATION}",
        "",
        f"Credit: {CREDIT}.",
        "",
        "These files are used only to check Bato's results for the case study.",
        "Nothing in this folder is read by the mapping pipeline. A test enforces this.",
        "",
        f"- Activation: {rec.get('code')} {rec.get('name')}, event {rec.get('eventTime')}, "
        f"activated {rec.get('activationTime')}",
        f"- Reason given by EMS: {rec.get('reason')}",
        f"- Activation record: {API_URL}",
        f"- Portal: {PORTAL_URL}",
        f"- Downloaded: {now}",
        "",
        "## Products",
        "",
        "| AOI | Name | Product id | Product | Status | Sensors | Image times (UTC) | URL |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in log:
        lines.append(
            f"| {e['aoi']} | {e['aoi_name']} | {e['product_id']} | {e.get('product', e.get('type'))} | "
            f"{e['status']} | {', '.join(e['sensors']) or '-'} | {', '.join(e['image_dates']) or '-'} | "
            f"{e['url'] or '-'} |")
    lines += [
        "",
        "All products are grading (GRA) maps made by photo-interpretation of very high",
        "resolution optical images. EMS did not publish a radar flood delineation product",
        "for this activation. The observed event polygons are drawn only inside each AOI.",
        "",
        "Event types found in the observed event layers: "
        + (", ".join(sorted(set(features.loc[features.layer == "observed_event", "kind"] + " / "
                                + features.loc[features.layer == "observed_event", "detail"]))) or "none")
        + ". EMS classed every observed event polygon this way. Bato compares its flood_class 1",
        "and 2 (open water and debris / scour) against all of them, because both maps try to",
        "capture the ground the flood and debris flow reached.",
        "",
        "EMS built-up layers list mostly affected buildings. Where there are no",
        "`no_visible_damage` records, a false alarm rate on buildings cannot be computed.",
        "",
        "## Normalised schema (features.geojson)",
        "",
        "`layer, aoi, grade, kind, detail, name, product, product_id, stage, image_dates, source_file, geometry`",
        "",
        "grade is one of destroyed, damaged, possibly_damaged, no_visible_damage, not_analysed.",
        "For observed_event it is empty (an extent has no grade); the event type is in `kind`.",
        "Features with an empty geometry in the source were dropped and counted in download_log.json.",
        "",
        "## Feature counts",
        "",
        "| AOI | Product | Layer | Grade | Count |",
        "|---|---|---|---|---|",
    ]
    if len(features):
        for _, r in summary_table(features).iterrows():
            lines.append(f"| {r.aoi} | {r['product']} | {r.layer} | {r.grade} | {r['count']} |")
    (out / "SOURCES.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    res = fetch_all()
    print(f"{res['features']} features")
    for a in res["aois"]:
        print(a["aoi"], a["name"], a["status"], a["products"])
