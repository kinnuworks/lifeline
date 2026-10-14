"""HTTP API for live runs: the dashboard's "run any valley" panel and the copilot.

    uv run uvicorn lifeline.api.server:app --port 8000

POST /runs {"bbox": [w, s, e, n], "date": "YYYY-MM-DD", "label": "..."} starts a run
GET  /runs/{id}             stage-by-stage progress and results (meta.json)
GET  /runs/{id}/files/{path} any file inside the run folder
POST /ask {"run_id", "question", "lang"} copilot answer with cited facts
GET  /health
"""
from __future__ import annotations

import datetime as dt
import json
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from ..pipeline import RUNS, run

app = FastAPI(title="Lifeline API", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])

MAX_AREA_DEG2 = 0.45  # about 60 x 70 km in Nepal; larger areas take too long for a live run
_lock = threading.Lock()
_active: dict[str, threading.Thread] = {}
_exported: dict[str, bool] = {}   # run id -> website data written (False while it is being written)
FIRST_S1_DATE = dt.date(2014, 10, 3)


def _close_orphans() -> None:
    """A run still marked running when the engine starts was cut off by a restart; say so instead of spinning forever."""
    try:
        for m in RUNS.glob("live-*/meta.json"):
            meta = json.loads(m.read_text())
            if meta.get("status") == "running":
                meta["status"] = "failed"
                meta["error"] = "The engine restarted during this run. Please run it again."
                m.write_text(json.dumps(meta, indent=1))
    except Exception:
        pass


_close_orphans()


class RunRequest(BaseModel):
    bbox: list[float] = Field(min_length=4, max_length=4)
    date: dt.date
    label: str = ""
    name: str = ""

    @field_validator("bbox")
    @classmethod
    def sane(cls, b):
        w, s, e, n = b
        if not (-180 <= w < e <= 180 and -80 <= s < n <= 80):
            raise ValueError("bbox must be west, south, east, north in degrees")
        if (e - w) * (n - s) > MAX_AREA_DEG2:
            raise ValueError("That area is too large for a live run. Draw a box under about 60 km on each side.")
        return b

    @field_validator("date")
    @classmethod
    def in_range(cls, d):
        if d < FIRST_S1_DATE:
            raise ValueError("Sentinel-1 radar images start in October 2014. Pick a later flood date.")
        if d > dt.date.today() - dt.timedelta(days=2):
            raise ValueError("The radar needs a pass after the flood. Pick a date at least two days ago.")
        return d


class AskRequest(BaseModel):
    run_id: str
    question: str = Field(min_length=1, max_length=600)
    lang: str = "en"


def _run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", run_id):
        raise HTTPException(400, "bad run id")
    d = RUNS / run_id
    if not d.exists():
        raise HTTPException(404, "no such run")
    return d


@app.get("/health")
def health():
    return {"ok": True, "active_runs": [k for k, t in _active.items() if t.is_alive()]}


@app.post("/runs")
def start_run(req: RunRequest):
    with _lock:
        rid = f"live-{req.date.isoformat()}-{abs(hash(tuple(round(v, 3) for v in req.bbox))) % 10**6:06d}"
        if rid in _active and _active[rid].is_alive():   # the same box and date: follow the run already going
            return {"id": rid, "run_id": rid, "status_url": f"/runs/{rid}"}
        busy = [k for k, t in _active.items() if t.is_alive()]
        if len(busy) >= 2:
            raise HTTPException(429, "Two runs are already going. Try again in a few minutes.")
        _exported[rid] = False
        t = threading.Thread(target=_run_and_export, args=(req.bbox, req.date, rid, req.label or req.name), daemon=True)
        _active[rid] = t
        t.start()
    return {"id": rid, "run_id": rid, "status_url": f"/runs/{rid}"}


def _little_change_note(meta: dict) -> str | None:
    """When a run finds almost no flood, say when the radar actually looked, so a zero reads as a measurement."""
    rad = meta.get("radar") or {}
    found = sum(float(rad.get(k) or 0) for k in ("water_km2", "debris_km2", "optical_added_km2"))
    if not rad or found >= 0.1:
        return None
    after = []
    for o in rad.get("orbits", []):
        m = re.match(r"track (\d+) \w+ \d{4}-\d{2}-\d{2} -> (\d{4}-\d{2}-\d{2})", str(o.get("orbit", "")))
        if m:
            after.append((m.group(2), m.group(1)))
    when = ", ".join(f"{dt.date.fromisoformat(d):%-d %b %Y} (track {t})" for d, t in sorted(after))
    note = "Little or no flood change found."
    if when:
        note += f" The first radar passes after the flood date were on {when}; water that drained before then does not show."
    if float(rad.get("unobserved_km2") or 0) >= 0.5:
        note += f" {rad['unobserved_km2']} km² of steep ground was hidden from the radar and is marked not observed, not clear."
    return note


def _run_and_export(bbox, date, rid, label):
    d = run(bbox, date, rid, label, optical_budget_s=180)
    try:
        meta = json.loads((d / "meta.json").read_text())
        note = _little_change_note(meta)
        if note:
            meta.setdefault("warnings", []).append(note)
            (d / "meta.json").write_text(json.dumps(meta, indent=1))
    except Exception:
        pass
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("export_web", RUNS.parent / "scripts" / "export_web.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.main(rid, out=d / "webdata", update_index=False)
    except Exception as e:  # the run itself is still usable through /runs/{id}/files
        meta = json.loads((d / "meta.json").read_text())
        meta.setdefault("warnings", []).append(f"web export failed: {e}")
        (d / "meta.json").write_text(json.dumps(meta, indent=1))
    finally:
        _exported[rid] = True


# stage names the dashboard shows, mapped from the pipeline's internal stages
WEB_STAGES = [
    ("find_images", "Finding satellite images", ["images"]),
    ("radar_change", "Mapping radar and optical change", ["terrain", "radar", "optical"]),
    ("osm", "Reading OpenStreetMap", ["osm"]),
    ("damage", "Checking buildings, roads and bridges", ["damage"]),
    ("cutoff", "Finding who is cut off", ["access"]),
    ("report", "Checking water with the model, writing the report", ["model", "facts", "report", "previews"]),
]


def _web_status(meta: dict, rid: str) -> dict:
    st = meta.get("stages", {})
    stages = []
    for key, label, parts in WEB_STAGES:
        states = [st.get(p, {}).get("state") for p in parts]
        secs = round(sum(st.get(p, {}).get("seconds", 0) or 0 for p in parts), 1)
        if any(s == "running" for s in states):
            status = "running"
        elif all(s in ("done", "skipped", "failed") for s in states if s) and any(states):
            status = "done" if all(s for s in states) or meta.get("status") != "running" else "running"
        else:
            status = "pending"
        stages.append({"key": key, "label": label, "status": status, "seconds": secs})
    status = meta.get("status", "running")
    if status == "done" and not _exported.get(rid, True):
        status = "finishing"   # the maps are done; the website files are still being written
    out = {"status": status, "stages": stages, "runtime_seconds": meta.get("runtime_seconds"),
           "error": _plain_error(meta.get("error")), "warnings": meta.get("warnings", [])}
    if status == "done":
        out["data_url"] = f"/runs/{rid}/files/webdata/"
    return out


def _plain_error(err: str | None) -> str | None:
    """Turn a stack-level error into something a duty officer can act on."""
    if not err:
        return None
    e = str(err)
    low = e.lower()
    if "no sentinel-1" in low or "no radar" in low:
        return e
    if any(k in low for k in ("timeout", "timed out", "connection", "temporarily", "503", "502", "504")):
        return "A data service (satellite archive or OpenStreetMap) did not answer in time. Try again in a minute."
    if "memory" in low:
        return "The area is too large for this machine. Try a smaller box."
    return f"The run stopped: {e[:200]}"


@app.get("/runs")
def list_runs():
    out = []
    for d in sorted(RUNS.iterdir()):
        m = d / "meta.json"
        if m.exists():
            meta = json.loads(m.read_text())
            out.append({k: meta.get(k) for k in ("run_id", "label", "event_date", "bbox", "status", "runtime_seconds")})
    return out


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    meta = json.loads((_run_dir(run_id) / "meta.json").read_text())
    return meta | _web_status(meta, run_id)


class FlowRequest(BaseModel):
    run_id: str
    lon: float
    lat: float


@app.post("/flowpath")
def flowpath(req: FlowRequest):
    from ..flowpath.trace import run_flowpath

    from ..flowpath.trace import to_geojson

    d = _run_dir(req.run_id)
    # a click traces a new path for this viewer only; the run's own flowpath.geojson is left as it is
    fc = to_geojson(run_flowpath(d, req.lon, req.lat, write=False))
    sp = d / "access" / "settlements.geojson"
    st = {f["properties"].get("name"): f["properties"] for f in json.loads(sp.read_text())["features"]} if sp.exists() else {}
    feats = []
    for f in fc["features"]:
        p = f["properties"]
        if p.get("kind") == "settlement":
            s_ = st.get(p.get("name"), {})
            f = {"type": "Feature", "geometry": f["geometry"],
                 "properties": {"kind": "village", "name": p.get("name"), "name_ne": p.get("name_ne") or s_.get("name_ne", ""),
                                "km_downstream": p.get("km"), "order": p.get("order"), "p_cutoff": s_.get("p_cutoff")}}
        feats.append(f)
    return {"type": "FeatureCollection", "features": feats}


@app.get("/runs/{run_id}/files/{path:path}")
def get_file(run_id: str, path: str):
    base = _run_dir(run_id).resolve()
    target = (base / path).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(404, "no such file")
    return FileResponse(target)


@app.post("/ask")
def ask(req: AskRequest):
    from ..copilot.agent import answer

    return answer(req.question, _run_dir(req.run_id), req.lang)
