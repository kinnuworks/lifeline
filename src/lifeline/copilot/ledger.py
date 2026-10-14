"""The fact ledger: runs/<id>/facts.json.

Every number the copilot or the situation report may say lives here. A fact is a
dict:

    {"id": "F001", "key": "access.villages.cut_off", "value": 12, "unit": "villages",
     "label_en": "...", "label_ne": "...", "source": "access/settlements.geojson",
     "decimals": 0, "kind": "count"}

Optional fields: "decimals" (display precision), "kind" (count, measure, percent,
date, text, list, rank), "value_ne" (Nepali form of a text or list value).

`build_facts(run_dir)` derives facts from whatever contract files exist and merges
them into facts.json, keeping the IDs of keys that were already there and any
facts other modules wrote.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from .nepali import MONTHS_EN, MONTHS_NE, group_lakh, to_dev

# unit -> (english singular, english plural, nepali noun, nepali classifier)
UNITS: dict[str, tuple[str, str, str, str]] = {
    "villages": ("village", "villages", "गाउँ", "वटा"),
    "people": ("person", "people", "", "जना"),
    "buildings": ("building", "buildings", "घर/भवन", "वटा"),
    "bridges": ("bridge", "bridges", "पुल", "वटा"),
    "facilities": ("health facility", "health facilities", "स्वास्थ्य संस्था", "वटा"),
    "hospitals": ("hospital", "hospitals", "अस्पताल", "वटा"),
    "chokepoints": ("repair point", "repair points", "मर्मत स्थान", "वटा"),
    "km": ("km", "km", "किलोमिटर", ""),
    "ha": ("ha", "ha", "हेक्टर", ""),
    "km2": ("km²", "km²", "वर्ग किलोमिटर", ""),
    "percent": ("%", "%", "प्रतिशत", ""),
    "minutes": ("minute", "minutes", "मिनेट", ""),
    "count": ("", "", "", "वटा"),
    "rank": ("", "", "", ""),
    "track": ("", "", "", ""),
}

STATUS_LABELS = {
    "destroyed_likely": ("likely destroyed", "ध्वस्त भएको हुन सक्ने"),
    "damaged_possible": ("possibly damaged", "क्षति भएको हुन सक्ने"),
    "clear": ("no damage seen", "क्षति नदेखिएको"),
    "unobserved": ("not seen by radar", "राडारले नदेखेको"),
}
STATUSES = list(STATUS_LABELS)


# ---------------------------------------------------------------- formatting

def fmt_number(x: float | int, decimals: int = 0, lang: str = "en") -> str:
    """Format a number for display. English: 1,234.5. Nepali: १,२३४.५ (lakh grouping)."""
    if isinstance(x, bool):
        x = int(x)
    if decimals <= 0:
        s = str(int(round(x)))
        frac = ""
    else:
        s = f"{x:.{decimals}f}"
        s, frac = s.split(".")
        frac = "." + frac
    if lang == "ne":
        return to_dev(group_lakh(s) + frac)
    return f"{int(s):,}" + frac


def fmt_date(iso: str, lang: str = "en") -> str:
    """'2026-08-28' -> '28 August 2026' / 'अगस्ट २८, २०२६'."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(iso))
    if not m:
        return str(iso)
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    if lang == "ne":
        return f"{MONTHS_NE[mo - 1]} {to_dev(str(d))}, {to_dev(str(y))}"
    return f"{d} {MONTHS_EN[mo - 1]} {y}"


def fact_decimals(fact: dict) -> int:
    if fact.get("decimals") is not None:
        return int(fact["decimals"])
    v = fact.get("value")
    if isinstance(v, float) and not float(v).is_integer():
        return 1
    return 0


def list_join(vals) -> str:
    """Join names for reading; a semicolon when a name has its own comma ("Gnon-Sangdong, Lower Dzongu"),
    so a list of 9 is never read as 10."""
    vals = [str(x) for x in vals]
    return ("; " if any("," in x for x in vals) else ", ").join(vals)


def render_value(fact: dict, lang: str = "en", with_unit: bool = True) -> str:
    """Render a fact's value in English or Nepali, with its unit."""
    v = fact.get("value")
    kind = fact.get("kind")
    unit = fact.get("unit") or ""
    if kind == "date":
        return fmt_date(v, lang)
    if isinstance(v, list):
        vals = fact.get("value_ne") if lang == "ne" and fact.get("value_ne") else v
        return list_join(vals)
    if isinstance(v, str) or v is None:
        if lang == "ne" and fact.get("value_ne"):
            return str(fact["value_ne"])
        return "" if v is None else str(v)
    num = fmt_number(v, fact_decimals(fact), lang)
    if not with_unit or unit not in UNITS:
        return num
    sing, plur, ne_noun, ne_cls = UNITS[unit]
    if lang == "ne":
        if unit == "percent":
            return f"{num} प्रतिशत"
        bits = [num]
        if ne_cls:
            bits.append(ne_cls)
        if ne_noun:
            bits.append(ne_noun)
        return " ".join(bits)
    if unit == "percent":
        return f"{num}%"
    if unit in ("km", "ha", "km2"):
        return f"{num} {sing}"
    if not sing:
        return num
    return f"{num} {sing if float(v) == 1 else plur}"


# ---------------------------------------------------------------- ledger

class Ledger:
    def __init__(self, facts: Iterable[dict] | None = None, run_dir: str | Path | None = None):
        self.facts: list[dict] = list(facts or [])
        self.run_dir = Path(run_dir) if run_dir else None
        self._reindex()

    def _reindex(self) -> None:
        self.by_id = {f["id"]: f for f in self.facts}
        self.by_key = {f["key"]: f for f in self.facts}

    @classmethod
    def load(cls, run_dir: str | Path) -> "Ledger":
        p = Path(run_dir) / "facts.json"
        facts: list[dict] = []
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            facts = data.get("facts", []) if isinstance(data, dict) else data
        return cls(facts, run_dir)

    def save(self, run_dir: str | Path | None = None) -> Path:
        d = Path(run_dir) if run_dir else self.run_dir
        assert d is not None, "no run_dir"
        p = d / "facts.json"
        p.write_text(json.dumps(self.facts, ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    def __len__(self) -> int:
        return len(self.facts)

    def __iter__(self):
        return iter(self.facts)

    def get(self, key: str) -> dict | None:
        return self.by_key.get(key)

    def get_id(self, fid: str) -> dict | None:
        return self.by_id.get(fid)

    def value(self, key: str, default: Any = None) -> Any:
        f = self.by_key.get(key)
        return default if f is None else f.get("value")

    def prefix(self, prefix: str) -> list[dict]:
        return [f for f in self.facts if f["key"].startswith(prefix)]

    def _next_id(self) -> str:
        n = 0
        for fid in self.by_id:
            m = re.match(r"F(\d+)$", fid)
            if m:
                n = max(n, int(m[1]))
        return f"F{n + 1:03d}"

    def add(self, key: str, value: Any, unit: str = "", label_en: str = "", label_ne: str = "",
            source: str = "", **extra: Any) -> dict:
        """Add or update a fact. Keeps the existing ID if the key is already present."""
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise ValueError(f"fact {key} is not finite")
        old = self.by_key.get(key)
        fact = {"id": old["id"] if old else self._next_id(), "key": key, "value": value, "unit": unit,
                "label_en": label_en, "label_ne": label_ne, "source": source}
        fact.update({k: v for k, v in extra.items() if v is not None})
        if old:
            self.facts[self.facts.index(old)] = fact
        else:
            self.facts.append(fact)
        self.by_key[key] = fact
        self.by_id[fact["id"]] = fact
        return fact

    def numeric_facts(self) -> list[dict]:
        return [f for f in self.facts if is_numeric_fact(f)]

    def find_settlement(self, name: str) -> list[dict]:
        """Settlement name facts matching a name in English or Nepali (case- and space-insensitive)."""
        q = _simplify(name)
        if not q:
            return []
        hits = []
        for f in self.facts:
            if not (f["key"].startswith("settlement.") and f["key"].endswith(".name")):
                continue
            names = [_simplify(str(f.get("value", ""))), _simplify(str(f.get("value_ne", "")))]
            if q in names:
                hits.insert(0, f)
            elif any(n and (q in n or n in q) and len(n) >= 3 for n in names):
                hits.append(f)
        return hits


def _simplify(s: str) -> str:
    return re.sub(r"[\s\-_.']", "", s or "").lower()


def is_numeric_fact(f: dict) -> bool:
    v = f.get("value")
    return isinstance(v, (int, float)) and not isinstance(v, bool) and f.get("kind") not in ("date", "text")


def load(run_dir: str | Path) -> Ledger:
    return Ledger.load(run_dir)


# ---------------------------------------------------------------- builder

def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _features(p: Path) -> list[dict] | None:
    data = _read_json(p) if p.exists() else None
    if not isinstance(data, dict):
        return None
    return [f for f in data.get("features", []) if isinstance(f, dict)]


def _line_length_m(geom: dict | None) -> float:
    if not geom:
        return 0.0
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    t = geom.get("type")
    lines = [geom["coordinates"]] if t == "LineString" else geom.get("coordinates", []) if t == "MultiLineString" else []
    total = 0.0
    for line in lines:
        if len(line) >= 2:
            lons, lats = zip(*[(c[0], c[1]) for c in line])
            total += abs(geod.line_length(lons, lats))
    return total


def _num(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _area_decimals(v: float) -> int:
    return 2 if v < 10 else 1 if v < 1000 else 0


def _iso_date(s: Any) -> str | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(s or ""))
    return f"{m[1]}-{m[2]}-{m[3]}" if m else None


def _facts_meta(L: Ledger, run: Path) -> None:
    meta = _read_json(run / "meta.json") if (run / "meta.json").exists() else None
    if not isinstance(meta, dict):
        return
    req = meta.get("request") if isinstance(meta.get("request"), dict) else {}
    src = "meta.json"
    ev = _iso_date(meta.get("event_date") or req.get("event_date"))
    if ev:
        L.add("meta.event_date", ev, "date", "Event date", "घटना भएको मिति", src, kind="date")
    name = meta.get("area_name") or meta.get("label") or req.get("area_name") or req.get("label") or req.get("name")
    if name:
        L.add("meta.area_name", str(name), "", "Area", "क्षेत्र", src, kind="text",
              value_ne=meta.get("area_name_ne") or req.get("area_name_ne"))
    osm = _iso_date(meta.get("osm_snapshot") or meta.get("osm_date") or req.get("osm_snapshot"))
    if osm:
        L.add("meta.osm_snapshot", osm, "date", "OpenStreetMap snapshot date", "OpenStreetMap तथ्याङ्कको मिति", src, kind="date")
    pairs = meta.get("pairs") or meta.get("image_pairs") or meta.get("radar_pairs") or []
    if isinstance(pairs, dict):
        pairs = list(pairs.values())
    for i, p in enumerate(pairs if isinstance(pairs, list) else [], 1):
        if not isinstance(p, dict):
            continue
        b = _iso_date(p.get("before") or p.get("date_before") or p.get("pre") or p.get("before_date"))
        a = _iso_date(p.get("after") or p.get("date_after") or p.get("post") or p.get("after_date"))
        if b:
            L.add(f"meta.radar.{i}.before", b, "date", f"Radar pair {i}: before image date",
                  f"राडार जोडी {i}: पहिलेको तस्बिर मिति", src, kind="date")
        if a:
            L.add(f"meta.radar.{i}.after", a, "date", f"Radar pair {i}: after image date",
                  f"राडार जोडी {i}: पछिको तस्बिर मिति", src, kind="date")
        tr = _num(p.get("track") or p.get("relative_orbit"))
        if tr is not None:
            L.add(f"meta.radar.{i}.track", int(tr), "track", f"Radar pair {i}: orbit track",
                  f"राडार जोडी {i}: कक्षपथ ट्र्याक", src, kind="rank")
    afters = sorted(f["value"] for f in L.prefix("meta.radar.") if f["key"].endswith(".after"))
    if afters:
        L.add("meta.latest_image", afters[-1], "date", "Latest radar image used", "प्रयोग गरिएको पछिल्लो राडार तस्बिर", src, kind="date")


def _facts_flood(L: Ledger, run: Path) -> None:
    p = run / "rasters" / "flood_class.tif"
    if not p.exists():
        return
    try:
        import numpy as np
        import rasterio
        with rasterio.open(p) as ds:
            arr = ds.read(1)
            a, e = ds.transform.a, ds.transform.e
            crs = ds.crs
        px_m2 = abs(a * e)
        if crs is None or not crs.is_projected:
            grid = _read_json(run / "grid.json") or {}
            pm = _num(grid.get("pixel_m")) or 20.0
            px_m2 = pm * pm
        counts = {int(k): int(v) for k, v in zip(*np.unique(arr, return_counts=True))}
    except Exception:
        return
    src = "rasters/flood_class.tif"

    def area(key: str, n: int, en: str, ne: str) -> None:
        ha = n * px_m2 / 10_000
        km2 = ha / 100
        L.add(f"flood.area_ha.{key}", round(ha, 0) if ha >= 10 else round(ha, 1), "ha", f"{en} (hectares)",
              f"{ne} (हेक्टर)", src, kind="measure", decimals=0 if ha >= 10 else 1)
        L.add(f"flood.area_km2.{key}", round(km2, _area_decimals(km2)), "km2", f"{en} (km²)",
              f"{ne} (वर्ग किलोमिटर)", src, kind="measure", decimals=_area_decimals(km2))

    water, debris, blind = counts.get(1, 0), counts.get(2, 0), counts.get(255, 0)
    total_px = sum(counts.values())
    area("total", water + debris, "Area flooded or buried by debris", "डुबान वा गेग्रानले पुरिएको क्षेत्र")
    area("water", water, "Open flood water", "खुला बाढीको पानी")
    area("debris", debris, "Debris or scour", "गेग्रान वा कटान")
    area("unobserved", blind, "Area the radar could not see", "राडारले देख्न नसकेको क्षेत्र")
    area("aoi", total_px, "Area analysed", "विश्लेषण गरिएको क्षेत्र")
    if total_px:
        pct = 100 * (total_px - blind) / total_px
        L.add("flood.observed_pct", round(pct), "percent", "Share of the area the radar could see",
              "राडारले देखेको क्षेत्रको प्रतिशत", src, kind="percent", decimals=0)


def _facts_damage(L: Ledger, run: Path) -> None:
    for layer, unit, en, ne in (("buildings", "buildings", "Buildings", "घर/भवन"),
                                ("bridges", "bridges", "Bridges", "पुल"),
                                ("roads", None, "Roads", "सडक")):
        feats = _features(run / "damage" / f"{layer}.geojson")
        if feats is None:
            continue
        src = f"damage/{layer}.geojson"
        if layer == "roads":
            km = Counter()
            for f in feats:
                pr = f.get("properties") or {}
                m = _num(pr.get("length_m"))
                km[pr.get("status", "unobserved")] += (m if m is not None else _line_length_m(f.get("geometry"))) / 1000
            for st in STATUSES:
                v = km.get(st, 0.0)
                L.add(f"damage.roads_km.{st}", round(v, 1), "km", f"Road length {STATUS_LABELS[st][0]}",
                      f"{STATUS_LABELS[st][1]} सडक लम्बाइ", src, kind="measure", decimals=1)
            L.add("damage.roads_km.total", round(sum(km.values()), 1), "km", "Road length mapped",
                  "नक्सामा भएको कुल सडक लम्बाइ", src, kind="measure", decimals=1)
            L.add("damage.roads_km.hit", round(km["destroyed_likely"] + km["damaged_possible"], 1), "km",
                  "Road length likely destroyed or possibly damaged", "ध्वस्त वा क्षति भएको हुन सक्ने सडक लम्बाइ",
                  src, kind="measure", decimals=1)
            under = sum(_num((f.get("properties") or {}).get("damaged_stretch_m")) or 0 for f in feats) / 1000
            if under > 0:
                L.add("damage.roads_km.under_debris", round(under, 1), "km",
                      "Length of road that lies under mapped debris or water (only the buried stretches, not whole roads)",
                      "नक्सामा देखिएको गेग्रान वा पानीमुनि परेको सडकको लम्बाइ (पुरिएको भाग मात्र)",
                      src, kind="measure", decimals=1)
            continue
        c = Counter((f.get("properties") or {}).get("status", "unobserved") for f in feats)
        for st in STATUSES:
            L.add(f"damage.{layer}.{st}", int(c.get(st, 0)), unit, f"{en} {STATUS_LABELS[st][0]}",
                  f"{STATUS_LABELS[st][1]} {ne}", src, kind="count")
        L.add(f"damage.{layer}.total", len(feats), unit, f"{en} mapped", f"नक्सामा भएका कुल {ne}", src, kind="count")
        L.add(f"damage.{layer}.hit", int(c["destroyed_likely"] + c["damaged_possible"]), unit,
              f"{en} likely destroyed or possibly damaged", f"ध्वस्त वा क्षति भएको हुन सक्ने {ne}", src, kind="count")
        if layer == "bridges":
            names = [str((f.get("properties") or {}).get("name") or "") for f in feats
                     if (f.get("properties") or {}).get("status") == "destroyed_likely"]
            names = sorted({n for n in names if n})
            if names:
                L.add("damage.bridges.destroyed_names", names, "", "Named bridges likely destroyed",
                      "ध्वस्त भएको हुन सक्ने नाम भएका पुल", src, kind="list")


def _facts_damage_by_place(L: Ledger, run: Path, radius_m: float = 2000.0, top: int = 10) -> None:
    """Likely-destroyed buildings grouped by the nearest named settlement within 2 km."""
    import math

    import numpy as np
    from scipy.spatial import cKDTree

    places = _features(run / "osm" / "places.geojson")
    blds = _features(run / "damage" / "buildings.geojson")
    if not places or not blds:
        return
    hit = [f for f in blds if (f.get("properties") or {}).get("status") == "destroyed_likely"
           and (f.get("geometry") or {}).get("type") == "Point"]
    if not hit:
        return
    lat0 = places[0]["geometry"]["coordinates"][1]
    kx = 111320.0 * math.cos(math.radians(lat0))

    def xy(fs):
        c = np.array([f["geometry"]["coordinates"][:2] for f in fs], dtype=float)
        return np.c_[c[:, 0] * kx, c[:, 1] * 110540.0]

    pts = [f for f in places if (f.get("geometry") or {}).get("type") == "Point"]
    d, idx = cKDTree(xy(pts)).query(xy(hit), distance_upper_bound=radius_m)
    counts = Counter(int(i) for i, dd in zip(idx, d) if np.isfinite(dd))
    rows = counts.most_common(top)
    if not rows:
        return
    src = "damage/buildings.geojson + osm/places.geojson"
    names, names_ne = [], []
    for rank, (i, n) in enumerate(rows, 1):
        pr = pts[i].get("properties") or {}
        nm, ne = str(pr.get("name") or ""), str(pr.get("name_ne") or pr.get("name") or "")
        names.append(nm)
        names_ne.append(ne)
        L.add(f"damage.near.{rank}.buildings_destroyed", n, "buildings",
              f"Buildings likely destroyed within 2 km of {nm}", f"{ne} को २ किमीभित्र ध्वस्त भएको हुन सक्ने घर/भवन", src, kind="count")
        L.add(f"damage.near.{rank}.name", nm, "", f"Place ranked {rank} by likely destroyed buildings nearby",
              f"नजिकै ध्वस्त घरका आधारमा {rank} औँ स्थान", src, kind="text", value_ne=ne)
    L.add("damage.places_hit_names", names, "", "Places with the most likely destroyed buildings within 2 km, most first",
          "२ किमीभित्र सबैभन्दा धेरै घर ध्वस्त भएका स्थान", src, kind="list", value_ne=names_ne)


def _facts_access(L: Ledger, run: Path) -> None:
    feats = _features(run / "access" / "settlements.geojson")
    if feats is None:
        return
    src = "access/settlements.geojson"
    rows = []
    for f in feats:
        pr = f.get("properties") or {}
        p = _num(pr.get("p_cutoff"))
        cut = pr.get("cutoff")
        cut = bool(cut) if cut is not None else (p is not None and p >= 0.5)
        rows.append((pr, p, cut))
    cut_rows = [r for r in rows if r[2]]
    L.add("access.villages.total", len(rows), "villages", "Settlements analysed", "विश्लेषण गरिएका कुल बस्ती", src, kind="count")
    L.add("access.villages.cut_off", len(cut_rows), "villages", "Settlements likely cut off by road",
          "सडक सम्पर्क टुटेको हुन सक्ने बस्ती", src, kind="count")
    status = [r[0].get("access_status") for r in rows]
    if any(status):
        L.add("access.villages.connected", sum(s == "connected" for s in status), "villages",
              "Settlements still reachable by road", "सडकबाट अझै पुग्न सकिने बस्ती", src, kind="count")
        L.add("access.villages.road_cut_walkable", sum(s == "road_cut_walkable" for s in status), "villages",
              "Settlements whose road is cut but which can walk to care within an hour",
              "सडक टुटे पनि घण्टाभित्रै हिँडेर स्वास्थ्य संस्था पुग्न सकिने बस्ती", src, kind="count")
        L.add("access.villages.no_road_before", sum(s == "no_road_before" for s in status), "villages",
              "Settlements with no mapped road to care even before the flood",
              "बाढीअघि पनि स्वास्थ्य संस्थासम्म नक्सामा सडक नभएका बस्ती", src, kind="count")
    else:
        L.add("access.villages.connected", len(rows) - len(cut_rows), "villages", "Settlements still reachable by road",
              "सडकबाट अझै पुग्न सकिने बस्ती", src, kind="count")
    pops = [_num(r[0].get("population")) for r in cut_rows]
    known = [p for p in pops if p is not None]
    if known:
        L.add("access.people.cut_off", int(sum(known)), "people",
              "People living in cut-off settlements (where OpenStreetMap has a population)",
              "सम्पर्क टुटेका बस्तीमा बस्ने मानिस (OpenStreetMap मा जनसंख्या भएका बस्ती मात्र)", src, kind="count")
        L.add("access.villages.cut_off_with_population", len(known), "villages",
              "Cut-off settlements that have a population figure", "जनसंख्या उल्लेख भएका सम्पर्कविहीन बस्ती", src, kind="count")

    def sort_key(r):
        return (-(r[1] or 0), -(_num(r[0].get("population")) or 0), str(r[0].get("name") or ""))

    top = sorted(cut_rows, key=sort_key)
    names = [str(r[0].get("name")) for r in top if r[0].get("name")]
    names_ne = [str(r[0].get("name_ne") or r[0].get("name")) for r in top if r[0].get("name")]
    if names:
        L.add("access.villages.cut_off_names", names, "", "Cut-off settlements, most certain first",
              "सम्पर्क टुटेका बस्ती (सबैभन्दा निश्चितदेखि)", src, kind="list", value_ne=names_ne)
    health = Counter(str(r[0].get("nearest_health")) for r in cut_rows if r[0].get("nearest_health"))
    if health:
        name, n = health.most_common(1)[0]
        L.add("access.health.nearest_for_cut_off", name, "", "Nearest health facility for most cut-off settlements",
              "धेरैजसो सम्पर्कविहीन बस्तीको नजिकको स्वास्थ्य संस्था", src, kind="text")
        L.add("access.health.nearest_for_cut_off_villages", n, "villages",
              "Cut-off settlements whose nearest health facility is that one",
              "त्यही स्वास्थ्य संस्था नजिक पर्ने सम्पर्कविहीन बस्ती", src, kind="count")

    for pr, p, cut in rows:
        oid = str(pr.get("osm_id") or pr.get("name") or "")
        nm = pr.get("name")
        if not oid or not nm:
            continue
        k = f"settlement.{oid}"
        L.add(f"{k}.name", str(nm), "", "Settlement name", "बस्तीको नाम", src, kind="text", value_ne=pr.get("name_ne"))
        L.add(f"{k}.status", "cut_off" if cut else "reachable", "", f"Road access for {nm}", f"{pr.get('name_ne') or nm} को सडक पहुँच",
              src, kind="text", value_ne="सडक सम्पर्क टुटेको हुन सक्छ" if cut else "सडकबाट पुग्न सकिन्छ")
        if p is not None:
            L.add(f"{k}.p_cutoff_pct", round(100 * p), "percent", f"Chance that {nm} is cut off",
                  f"{pr.get('name_ne') or nm} सम्पर्कविहीन हुने सम्भावना", src, kind="percent", decimals=0)
        for fld, en, ne in (("minutes_before", "Drive to nearest health facility before the flood (minutes)", "बाढीअघि नजिकको स्वास्थ्य संस्थासम्म गाडीमा (मिनेट)"),
                            ("minutes_after", "Drive to nearest health facility now (minutes)", "अहिले नजिकको स्वास्थ्य संस्थासम्म गाडीमा (मिनेट)"),
                            ("walk_minutes_after", "Walk to nearest health facility now (minutes)", "अहिले नजिकको स्वास्थ्य संस्थासम्म पैदल (मिनेट)")):
            v = _num(pr.get(fld))
            if v is not None:
                L.add(f"{k}.{fld}", int(round(v)), "minutes", f"{nm}: {en}", f"{pr.get('name_ne') or nm}: {ne}", src, kind="measure", decimals=0)
        if pr.get("nearest_health"):
            L.add(f"{k}.nearest_health", str(pr["nearest_health"]), "", f"{nm}: nearest health facility",
                  f"{pr.get('name_ne') or nm}: नजिकको स्वास्थ्य संस्था", src, kind="text")
        pop = _num(pr.get("population"))
        if pop is not None:
            L.add(f"{k}.population", int(pop), "people", f"{nm}: population (OpenStreetMap)",
                  f"{pr.get('name_ne') or nm}: जनसंख्या (OpenStreetMap)", src, kind="count")


def _facts_osm(L: Ledger, run: Path) -> None:
    hfeats = _features(run / "osm" / "health.geojson")
    if hfeats is not None:
        L.add("osm.health.total", len(hfeats), "facilities", "Health facilities mapped in the area",
              "क्षेत्रमा नक्सामा भएका स्वास्थ्य संस्था", "osm/health.geojson", kind="count")
        hosp = sum(1 for h in hfeats if (h.get("properties") or {}).get("kind") == "hospital")
        L.add("osm.health.hospitals", hosp, "hospitals", "Hospitals mapped in the area", "क्षेत्रमा नक्सामा भएका अस्पताल",
              "osm/health.geojson", kind="count")


def _facts_chokepoints(L: Ledger, run: Path) -> None:
    data = _read_json(run / "access" / "chokepoints.json") if (run / "access" / "chokepoints.json").exists() else None
    if not isinstance(data, list):
        return
    src = "access/chokepoints.json"
    data = sorted([c for c in data if isinstance(c, dict)], key=lambda c: c.get("rank", 999))
    L.add("chokepoint.count", len(data), "chokepoints", "Repair points ranked", "क्रम मिलाइएका मर्मत स्थान", src, kind="count")
    for i, c in enumerate(data, 1):
        r = int(c.get("rank") or i)
        k = f"chokepoint.{r}"
        kind = c.get("kind") or "road"
        nm = c.get("name") or ("Unnamed bridge" if kind == "bridge" else "Unnamed road section")
        nm_ne = c.get("name_ne") or (c.get("name") if c.get("name") else
                                     "नाम नभएको पुल" if kind == "bridge" else "नाम नभएको सडक खण्ड")
        L.add(f"{k}.rank", r, "rank", f"Repair priority rank {r}", f"मर्मत प्राथमिकता क्रम {r}", src, kind="rank")
        L.add(f"{k}.name", str(nm), "", f"Repair point {r}: name", f"मर्मत स्थान {r}: नाम", src, kind="text", value_ne=nm_ne)
        L.add(f"{k}.kind", kind, "", f"Repair point {r}: type", f"मर्मत स्थान {r}: प्रकार", src, kind="text",
              value_ne="पुल" if kind == "bridge" else "सडक")
        if c.get("combined_repair") and len(c.get("osm_ids") or []) > 1:
            L.add(f"{k}.scope", f"{nm} together with the other closed stretches on the same route", "",
                  f"Repair point {r}: one repair of several closed stretches on one route",
                  f"मर्मत स्थान {r}: एउटै बाटोका धेरै बन्द खण्डको मर्मत", src, kind="text",
                  value_ne=f"{nm_ne} र सोही बाटोका अन्य बन्द खण्ड")
        v = _num(c.get("villages_reconnected"))
        if v is not None:
            L.add(f"{k}.villages", int(v), "villages", f"Settlements reconnected by reopening repair point {r}",
                  f"मर्मत स्थान {r} खोल्दा फेरि जोडिने बस्ती", src, kind="count")
        pp = _num(c.get("people_reconnected"))
        if pp is not None and pp > 0:
            L.add(f"{k}.people", int(pp), "people", f"People reconnected by reopening repair point {r}",
                  f"मर्मत स्थान {r} खोल्दा फेरि जोडिने मानिस", src, kind="count")
        names = [str(n) for n in (c.get("names") or []) if n]
        if names:
            L.add(f"{k}.names", names, "", f"Settlements reconnected by repair point {r}",
                  f"मर्मत स्थान {r} खोल्दा जोडिने बस्ती", src, kind="list", value_ne=c.get("names_ne") or None)


def build_facts(run_dir: str | Path, write: bool = True) -> Ledger:
    """Derive facts from the contract files in run_dir and merge them into facts.json.

    Missing files are skipped. Facts written by other modules are kept, and keys
    that already exist keep their IDs.
    """
    run = Path(run_dir)
    L = Ledger.load(run)
    for step in (_facts_meta, _facts_flood, _facts_damage, _facts_damage_by_place, _facts_access, _facts_osm, _facts_chokepoints):
        step(L, run)
    if write:
        L.save(run)
    return L
