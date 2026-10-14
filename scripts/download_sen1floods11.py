"""Download the Sen1Floods11 hand-labelled split (S1Hand, LabelHand, split CSVs).

Source: public Google Cloud bucket gs://sen1floods11 (Bonafilia et al., 2020).
Writes to data/sen1floods11/.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BUCKET = "sen1floods11"
API = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
BASE = f"https://storage.googleapis.com/{BUCKET}/"
ROOT = Path(__file__).resolve().parents[1] / "data" / "sen1floods11"
PREFIXES = [
    "v1.1/splits/flood_handlabeled/",
    "v1.1/data/flood_events/HandLabeled/S1Hand/",
    "v1.1/data/flood_events/HandLabeled/LabelHand/",
]


def list_objects(prefix: str) -> list[dict]:
    out, token = [], None
    while True:
        q = {"prefix": prefix, "maxResults": "1000"}
        if token:
            q["pageToken"] = token
        with urllib.request.urlopen(API + "?" + urllib.parse.urlencode(q)) as r:
            d = json.load(r)
        out += d.get("items", [])
        token = d.get("nextPageToken")
        if not token:
            return out


def fetch(obj: dict) -> int:
    name = obj["name"]
    rel = name.split("flood_handlabeled/")[-1] if "splits" in name else name.split("HandLabeled/")[-1]
    dest = ROOT / ("splits" if "splits" in name else "") / rel
    size = int(obj["size"])
    if dest.exists() and dest.stat().st_size == size:
        return size
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(BASE + urllib.parse.quote(name), tmp)
    tmp.rename(dest)
    return size


def main() -> None:
    objs = [o for p in PREFIXES for o in list_objects(p) if not o["name"].endswith("/")]
    total = sum(int(o["size"]) for o in objs)
    print(f"{len(objs)} files, {total/1e6:.1f} MB", flush=True)
    done = 0
    with cf.ThreadPoolExecutor(16) as ex:
        for i, s in enumerate(ex.map(fetch, objs)):
            done += s
            if i % 100 == 0:
                print(f"{i}/{len(objs)} {done/1e6:.0f} MB", flush=True)
    print("done", file=sys.stderr)


if __name__ == "__main__":
    main()
