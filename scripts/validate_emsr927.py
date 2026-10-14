"""Check a Bato run against the Copernicus EMS EMSR927 grading maps.

    uv run python scripts/validate_emsr927.py fetch
    uv run python scripts/validate_emsr927.py compare runs/<run_id> [--stage first|latest]

The EMS layers are used only to check results. They live in data/reference/emsr927/
and the comparison writes only to runs/<run_id>/validation/.
"""

import argparse
import sys
from pathlib import Path

from lifeline.validate.compare import compare_run, output_stem
from lifeline.validate.fetch_ems import DEFAULT_OUT, fetch_all


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download and normalise the EMSR927 vector packages")
    f.add_argument("--out", type=Path, default=DEFAULT_OUT)
    f.add_argument("--offline", action="store_true", help="re-parse files already on disk")
    c = sub.add_parser("compare", help="compare one run with the EMS maps")
    c.add_argument("run_dir", type=Path)
    c.add_argument("--ref", type=Path, default=DEFAULT_OUT)
    c.add_argument("--stage", choices=["first", "latest", "both"], default="both",
                   help="first grading, latest monitoring update, or both (default)")
    c.add_argument("--n-boot", type=int, default=1000)
    a = ap.parse_args(argv)

    if a.cmd == "fetch":
        res = fetch_all(a.out, offline=a.offline)
        print(f"{res['features']} features written to {a.out}")
        for r in res["aois"]:
            print(f"  {r['aoi']} {r['name']}: {r['status']} {r['products']}")
        return 0

    if not (a.ref / "features.geojson").exists():
        print(f"No reference layers in {a.ref}. Run the fetch step first.", file=sys.stderr)
        return 1
    stages = ["first", "latest"] if a.stage == "both" else [a.stage]
    vdir = Path(a.run_dir) / "validation"
    for st in stages:
        compare_run(a.run_dir, ref_dir=a.ref, stage=st, n_boot=a.n_boot)
        print(f"[{st}] wrote {vdir / (output_stem(st) + '.json')}")
    print((vdir / f"{output_stem(stages[0])}.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
