import pytest

from lifeline.copilot.guard import check, drop_violating_sentences, extract


def reasons(text, ledger, **kw):
    return [v.reason for v in check(text, ledger, **kw).violations]


@pytest.mark.parametrize("text", [
    "5 of 10 settlements are likely cut off by road [F042][F041].",
    "4,795 people live in them.",
    "५ वटा गाउँको सडक सम्पर्क टुटेको हुन सक्छ।",
    "विश्लेषण गरिएका १० वटा बस्तीमध्ये ५ वटाको सडक सम्पर्क टुटेको हुन सक्छ।",
    "पाँचवटा गाउँ।",
    "करिब ४,७९५ जना।",
    "Flooded area 0.34 km² and 34 ha, 9.1 km of road.",
    "Images from 28 August 2026 and 16 Aug.",
    "अगस्ट २८, २०२६ को तस्बिर।",
    "Sentinel-1 data, activation ABCD927, track 85.",
    "1. Reopen the bridge.\n2. Check roads.",
    "यो एक शैक्षिक नमुना हो। गाउँ सम्पर्कविहीन छ।",
    "छ वटा घर ध्वस्त भएको हुन सक्छ।",
    "Gatlang: 74% chance.",
    "I can't give numbers of deaths. Satellite maps cannot show them.",
    "मृत्यु भएका मानिसको संख्या म दिन सक्दिनँ।",
    "Five settlements are likely cut off.",
])
def test_clean_text_passes(text, ledger):
    r = check(text, ledger)
    assert r.ok, r.violations


@pytest.mark.parametrize("text,reason", [
    ("100 villages are cut off.", "not_in_ledger"),
    ("More than 5 villages are cut off.", "qualifier"),
    ("१२ भन्दा बढी गाउँ।", "qualifier"),
    ("Twelve villages are cut off.", "unit_mismatch"),
    ("A dozen bridges were destroyed.", "unit_mismatch"),
    ("Hundreds of houses were damaged.", "vague"),
    ("हजारौं घर डुबे।", "vague"),
    ("Roads may reopen in 3 days.", "unit_mismatch"),
    ("No one died.", "casualty_claim"),
    ("12 people died in Haku.", "casualty"),
    ("१०० जनाको मृत्यु भयो।", "casualty"),
    ("Image from 3 September 2026.", "date_not_in_ledger"),
    ("२०८३ भदौ १० मा बाढी आयो।", "date_not_in_ledger"),
    ("बाह्र वटा गाउँ सम्पर्कविहीन छन्।", "unit_mismatch"),
    ("तीन सय घर भत्किए।", "not_in_ledger"),
    ("Flooded area 35 ha.", "unit_mismatch"),
    ("The flood affected 6 families.", "unit_mismatch"),
    ("barha gaun cut off", "unit_mismatch"),
    ("At 10:30 am the bridge fell.", "time"),
    ("one hundred and twenty villages", "not_in_ledger"),
    ("twenty-three bridges", "not_in_ledger"),
    ("half of the villages", "ratio"),
    ("See fact [F999].", "bad_citation"),
    ("No settlements are cut off.", "not_in_ledger"),
])
def test_fabrications_caught(text, reason, ledger):
    assert reason in reasons(text, ledger)


def test_word_and_digit_parsing():
    ms = extract("one hundred and twenty-three villages, ३ लाख people, 1,23,456 ha, 12.5%")
    vals = [(m.value, m.unit) for m in ms]
    assert (123.0, "settlement") in vals
    assert (300000.0, "people") in vals
    assert (123456.0, "ha") in vals
    assert (12.5, "percent") in vals


def test_rounding_rules(ledger):
    assert check("0.3 km² flooded.", ledger).ok          # measure rounded to fewer decimals
    assert not check("0.5 km² flooded.", ledger).ok      # wrong rounding
    assert not check("4,800 people live there.", ledger).ok  # counts are never rounded


def test_allowed_scope(ledger):
    fid = ledger.get("access.villages.cut_off")["id"]
    assert check("5 settlements are cut off.", ledger, allowed_ids=[fid]).ok
    other = ledger.get("damage.buildings.total")["id"]
    assert not check("40 buildings mapped.", ledger, allowed_ids=[fid]).ok
    assert check(f"40 buildings mapped [{other}].", ledger, allowed_ids=[fid]).ok


def test_drop_sentences(ledger):
    text = "5 settlements are cut off. 100 people died. Gatlang is cut off."
    r = check(text, ledger)
    out = drop_violating_sentences(text, r)
    assert "100" not in out and "Gatlang" in out
