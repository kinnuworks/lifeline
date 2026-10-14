import json

from lifeline.copilot.ledger import Ledger, build_facts, fmt_number, render_value
from lifeline.copilot.nepali import ne_words, to_dev, group_lakh


def test_build_facts_counts(ledger):
    assert ledger.value("access.villages.cut_off") == 5
    assert ledger.value("access.villages.total") == 10
    assert ledger.value("access.people.cut_off") == 712 + 1840 + 640 + 1215 + 388
    assert ledger.value("damage.buildings.destroyed_likely") == 6
    assert ledger.value("damage.bridges.destroyed_likely") == 2
    assert ledger.value("damage.roads_km.destroyed_likely") == 9.1
    assert ledger.value("flood.area_ha.water") == 24
    assert ledger.value("flood.area_ha.debris") == 10
    assert ledger.value("flood.area_km2.total") == 0.34
    assert ledger.value("chokepoint.1.villages") == 3
    assert ledger.value("meta.event_date") == "2026-08-26"
    assert ledger.value("access.villages.cut_off_names")[0] == "Haku"  # most certain first (p 0.93)


def test_ids_stable_and_merge(run_dir):
    L1 = Ledger.load(run_dir)
    L1.add("model.f1", 0.91, "", "Model F1", "मोडेल F1", "validation", decimals=2)
    L1.save()
    L2 = build_facts(run_dir)
    assert L2.get("model.f1")["value"] == 0.91      # other modules' facts kept
    for f in L1.facts:
        assert L2.get(f["key"])["id"] == f["id"]   # ids unchanged on rebuild
    assert len({f["id"] for f in L2.facts}) == len(L2.facts)


def test_missing_files_skipped(tmp_path):
    L = build_facts(tmp_path)
    assert len(L) == 0
    (tmp_path / "meta.json").write_text(json.dumps({"event_date": "2026-08-26"}))
    L = build_facts(tmp_path)
    assert L.value("meta.event_date") == "2026-08-26"
    assert L.get("access.villages.cut_off") is None


def test_render_nepali():
    assert to_dev("2026") == "२०२६"
    assert group_lakh("1234567") == "12,34,567"
    assert fmt_number(123456, 0, "ne") == "१,२३,४५६"
    assert fmt_number(1234.5, 1, "en") == "1,234.5"
    assert ne_words(12) == "बाह्र"
    assert ne_words(1234) == "एक हजार दुई सय चौँतीस"
    f = {"id": "F1", "key": "k", "value": 12, "unit": "villages"}
    assert render_value(f, "ne") == "१२ वटा गाउँ"
    assert render_value(f, "en") == "12 villages"
    p = {"id": "F2", "key": "p", "value": 4795, "unit": "people"}
    assert render_value(p, "ne") == "४,७९५ जना"
    d = {"id": "F3", "key": "d", "value": "2026-08-28", "kind": "date"}
    assert render_value(d, "en") == "28 August 2026"
    assert render_value(d, "ne") == "अगस्ट २८, २०२६"


def test_find_settlement(ledger):
    assert ledger.find_settlement("gatlang")[0]["value"] == "Gatlang"
    assert ledger.find_settlement("गत्लाङ")[0]["value"] == "Gatlang"
    assert ledger.find_settlement("Atlantis") == []
