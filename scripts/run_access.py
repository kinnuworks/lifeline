"""Run the access analysis (who is cut off, repair ranking) and optionally the flow path tool.

Usage:
  uv run python scripts/run_access.py runs/<id> [--draws 1000] [--seed 7] [--prior 0.15]
                                                [--flow LON LAT]
"""

import argparse
import json
import sys
from pathlib import Path

from lifeline.access.pipeline import run_access


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--draws", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--prior", type=float, default=0.15, help="closure probability for unobserved roads")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--flow", nargs=2, type=float, metavar=("LON", "LAT"), help="also trace a flow path")
    a = ap.parse_args(argv)
    if not (a.run_dir / "damage" / "roads.geojson").exists():
        sys.exit(f"missing {a.run_dir / 'damage' / 'roads.geojson'}")

    s = run_access(a.run_dir, n_draws=a.draws, seed=a.seed, unobserved_prior=a.prior, top=a.top)
    g = s["graph"]
    print(f"graph: {g['nodes']} nodes, {g['edges']} edges ({g['drivable_edges']} drivable), "
          f"{g['km_total']} km, {g['bridges']} bridges, {g['drivable_components']} drivable components")
    print(f"destinations: {s['destinations']} ({s['destination_basis']}) {s['destination_note']}")
    print(f"settlements: {s['settlements_total']} total, {s['settlements_with_road_before']} with road, "
          f"{s['settlements_connected_before']} connected before the flood")
    print(f"cut off p>=0.9: {s['cutoff_p90_plus']}  {', '.join(s['names_p90_plus'])}")
    print(f"cut off 0.5-0.9: {s['cutoff_p50_to_90']}  {', '.join(s['names_p50_to_90'])}")
    print(f"at risk 0.1-0.5: {s['at_risk_p10_to_50']}")
    print(f"unobserved roads: {s['unobserved_edges']} edges, {s['unobserved_km']} km, prior {s['unobserved_prior']}")
    chokes = json.loads((a.run_dir / "access" / "chokepoints.json").read_text())
    for c in chokes:
        ppl = c["people_reconnected"] if c["people_reconnected"] is not None else "n/a"
        print(f"  repair {c['rank']}: {c['kind']} {c['name']} (osm {c['osm_id']}) reconnects "
              f"{c['villages_reconnected']} settlements, people {ppl}: {', '.join(c['names'][:6])}")
    print(f"single-point bridges before the flood: {s['single_point_bridges']}")
    print(f"timings: {s['timings']}")

    if a.flow:
        from lifeline.flowpath.trace import run_flowpath
        r = run_flowpath(a.run_dir, a.flow[0], a.flow[1])
        print(f"flow path: {r['length_km']} km, drop {r['drop_m']} m, corridor median half-width "
              f"{r['median_halfwidth_m']} m ({r['valley_basis']})")
        for st in r["settlements"]:
            print(f"  {st['order']}. {st['name']} at {st['km']} km")


if __name__ == "__main__":
    main()
