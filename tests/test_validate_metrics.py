"""Metric math for lifeline.validate.compare on a synthetic run with hand-worked answers."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from lifeline.validate.compare import (
    block_counts, bootstrap_ci, compare_run, confusion_counts, scores, wilson_ci,
)
from lifeline.validate.fetch_ems import normalise_grade

FIX = Path(__file__).parent / "fixtures" / "validate_demo" / "build_fixture.py"


def _builder():
    spec = importlib.util.spec_from_file_location("validate_demo_fixture", FIX)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("validate_demo")
    run, ref = _builder().build(root)
    res = compare_run(run, ref_dir=ref, stage="first", n_boot=300, seed=1)
    return run, ref, res


def test_scores_basic():
    s = scores(tp=6, fp=2, fn=2)
    assert s["iou"] == pytest.approx(0.6)
    assert s["precision"] == pytest.approx(0.75)
    assert s["f1"] == pytest.approx(0.75)
    assert scores(0, 0, 0)["iou"] is None


def test_confusion_and_blocks_agree():
    rng = np.random.default_rng(0)
    pred, ref = rng.random((40, 40)) > 0.5, rng.random((40, 40)) > 0.5
    valid = np.ones((40, 40), bool)
    valid[:5] = False
    c = confusion_counts(pred, ref, valid)
    b = block_counts(pred, ref, valid, 10)
    assert b.sum(axis=0).tolist() == [c["tp"], c["fp"], c["fn"], c["tn"]]
    assert len(b) == 16


def test_bootstrap_ci_contains_point_estimate():
    blocks = np.array([[10, 2, 3, 50]] * 30)
    ci = bootstrap_ci(blocks, 200, 0)
    # identical blocks: interval collapses on the point value
    assert ci["iou"][0] == pytest.approx(10 / 15, abs=1e-3)
    assert ci["iou"][1] == pytest.approx(10 / 15, abs=1e-3)
    assert bootstrap_ci(blocks[:1], 200, 0)["iou"] is None


def test_wilson():
    assert wilson_ci(0, 0) is None
    lo, hi = wilson_ci(5, 10)
    assert lo < 0.5 < hi


def test_grade_normalisation():
    assert normalise_grade("Destroyed") == "destroyed"
    assert normalise_grade("Damaged") == "damaged"
    assert normalise_grade("Possibly damaged") == "possibly_damaged"
    assert normalise_grade("No visible damage") == "no_visible_damage"
    assert normalise_grade("Not Analysed") == "not_analysed"
    assert normalise_grade(None) == "not_analysed"


def test_extent_metrics(demo):
    _, _, res = demo
    a = next(x for x in res["aois"] if x["aoi"] == "AOI01")
    e = a["extent"]["all_pixels"]
    # AOI is 40x40 px; EMS event cols 10..29, ours cols 15..34
    assert e["pixels"] == {"tp": 600, "fp": 200, "fn": 200, "tn": 600}
    assert e["iou"] == pytest.approx(0.6)
    assert e["f1"] == pytest.approx(0.75)
    lo, hi = e["ci95"]["iou"]
    assert lo <= 0.6 <= hi
    # radar blind on cols 0..12: drops 8 EMS-event columns and 8 empty ones
    o = a["extent"]["observed_pixels_only"]
    assert o["pixels"] == {"tp": 600, "fp": 200, "fn": 80, "tn": 400}
    assert o["recall"] == pytest.approx(600 / 680, abs=1e-4)


def test_buildings(demo):
    b = next(x for x in demo[2]["aois"] if x["aoi"] == "AOI01")["buildings"]
    h = b["hit_rate_destroyed_damaged"]
    assert (h["flagged"], h["n"]) == (3, 5)
    assert b["hit_rate_destroyed_damaged_observed_only"]["n"] == 4
    fa = b["false_alarm_rate_no_visible_damage"]
    assert (fa["flagged"], fa["n"]) == (1, 2)
    assert b["n_ems_without_osm_match"] == 1
    assert b["ours_flagged_without_ems_record"] == 1
    # the monitoring-stage record must not leak into the first-stage comparison
    assert b["n_ems"] == 8


def test_roads(demo):
    r = next(x for x in demo[2]["aois"] if x["aoi"] == "AOI01")["roads"]
    L = r["length_m"]
    assert L["destroyed"]["destroyed_likely"] == pytest.approx(200, abs=0.5)
    # the destroyed buffer wins the overlap at the grade boundary: 100 m + sqrt(20^2 - 5^2)
    assert L["destroyed"]["clear"] == pytest.approx(100 + (400 - 25) ** 0.5, abs=0.5)
    assert L["no_visible_damage"]["damaged_possible"] == pytest.approx(200, abs=0.5)
    agr = r["destroyed_damaged_vs_no_visible_damage"]["agreement"]
    assert agr == pytest.approx((200 + 200 - 19.36) / 700, abs=0.002)


def test_bridges(demo):
    br = next(x for x in demo[2]["aois"] if x["aoi"] == "AOI01")["bridges"]
    assert br["text"] == "1/3"
    assert br["matched_osm"] == 2


def test_cutoff(demo):
    c = demo[2]["cutoff"]["all_settlements"]
    assert c["n"] == 3
    assert c["agree"] == 2
    assert c["both_cut_off"] == 1
    assert c["ours_only_cut_off"] == ["Mid"]
    excluded = {e["name"] for e in demo[2]["cutoff"]["excluded"]}
    assert "Far" in excluded


def test_cancelled_aoi_reported(demo):
    a = next(x for x in demo[2]["aois"] if x["aoi"] == "AOI04")
    assert a["status"] == "cancelled"
    assert "not compared" in a["result"]
    md = (demo[0] / "validation" / "emsr927.md").read_text()
    assert "AOI04" in md


def test_outputs_written(demo):
    run = demo[0]
    data = json.loads((run / "validation" / "emsr927.json").read_text())
    assert data["credit"] == "European Union, Copernicus Emergency Management Service data"
    assert "\u2014" not in (run / "validation" / "emsr927.md").read_text()


def test_latest_stage_uses_monitoring(tmp_path):
    run, ref = _builder().build(tmp_path)
    res = compare_run(run, ref_dir=ref, stage="latest", n_boot=50)
    a = next(x for x in res["aois"] if x["aoi"] == "AOI01")
    assert a["product_used"] == ["GRA_MONIT01"]
    assert (run / "validation" / "emsr927_latest.json").exists()
