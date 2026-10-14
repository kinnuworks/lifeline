"""Access analysis on the synthetic network from the brief: B and C cut off, A and D not."""

import json
import shutil
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest

from lifeline.access.graph import build_graph
from lifeline.access.montecarlo import simulate
from lifeline.access.pipeline import run_access

FIX = Path(__file__).parent / "fixtures" / "access_demo"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    d = tmp_path_factory.mktemp("run") / "access_demo"
    shutil.copytree(FIX, d)
    summ = run_access(d, n_draws=1000, seed=3)
    sett = gpd.read_file(d / "access" / "settlements.geojson").set_index("name")
    chokes = json.loads((d / "access" / "chokepoints.json").read_text())
    spof = json.loads((d / "access" / "single_points.json").read_text())
    return d, summ, sett, chokes, spof


def test_graph_noding():
    roads = gpd.read_file(FIX / "damage" / "roads.geojson")
    g = build_graph(roads)
    # road 2 crosses road 1 without a shared vertex: it must be split into two edges at the crossing
    assert (g.osm_id.astype(str) == "2").sum() == 2
    # the bridge stays a single edge of its own
    assert g.bridge.sum() == 1
    # the H-D road starts about 10 m from the junction and is snapped onto it: one drivable component
    assert g.stats["drivable_components"] == 1
    assert g.stats["endpoint_gap_joins"] >= 1
    # footpath is walk only
    assert not g.drivable[g.highway == "path"].any()


def test_brief_illustration(run):
    _, summ, s, _, _ = run
    assert s.loc["B", "cutoff"] and s.loc["C", "cutoff"]
    assert not s.loc["A", "cutoff"] and not s.loc["D", "cutoff"]
    assert s.loc["B", "p_cutoff"] > 0.85
    assert 0.75 < s.loc["C", "p_cutoff"] < 0.95
    assert s.loc["A", "p_cutoff"] < 0.1
    # D's road is partly unobserved: prior 0.15 plus a 0.05 hit on the observed half
    assert 0.12 < s.loc["D", "p_cutoff"] < 0.26
    assert summ["names_p90_plus"] == ["B"] and summ["names_p50_to_90"] == ["C"]
    assert summ["unobserved_edges"] == 1


def test_contract_columns_and_times(run):
    d, _, s, _, _ = run
    for col in ["p_cutoff", "cutoff", "nearest_health", "minutes_before", "minutes_after",
                "walk_minutes_after", "reason"]:
        assert col in s.columns
    assert s.loc["A", "nearest_health"] == "District Hospital"
    assert s.loc["A", "minutes_after"] == pytest.approx(s.loc["A", "minutes_before"])
    assert np.isnan(s.loc["B", "minutes_after"]) and np.isnan(s.loc["C", "minutes_after"])
    # C can still walk out on the footpath; B is behind a destroyed bridge with no path
    assert s.loc["C", "walk_minutes_after"] > 60
    assert np.isnan(s.loc["B", "walk_minutes_after"])
    # E has no road within 1.5 km
    assert "no road access before the flood" in s.loc["E", "reason"]
    assert s.loc["E", "p_cutoff"] is None or np.isnan(s.loc["E", "p_cutoff"])
    assert "Thade Khola" in s.loc["B", "reason"]


def test_chokepoints(run):
    _, _, _, chokes, spof = run
    assert chokes[0]["kind"] == "bridge"
    assert chokes[0]["osm_id"] == "6"
    assert chokes[0]["names"] == ["B"] and chokes[0]["people_reconnected"] == 1500
    assert chokes[1]["osm_id"] == "9" and chokes[1]["names"] == ["C"]
    for c in chokes:
        assert set(c) >= {"rank", "osm_id", "kind", "name", "lon", "lat", "villages_reconnected",
                          "people_reconnected", "names"}
    assert [s["osm_id"] for s in spof] == ["6"]


def test_seeded_and_reproducible():
    roads = gpd.read_file(FIX / "damage" / "roads.geojson")
    g = build_graph(roads)
    nodes = np.array([0, 1, 2])
    a = simulate(g, nodes, np.array([3]), n_draws=200, seed=11)
    b = simulate(g, nodes, np.array([3]), n_draws=200, seed=11)
    assert (a == b).all()
