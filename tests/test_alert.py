from pathlib import Path

from lifeline.copilot.guard import check
from lifeline.copilot.ledger import Ledger
from lifeline.report.alert import build_alert

DEMO = Path(__file__).parent / "fixtures" / "run_demo"


def test_alert_is_short_and_every_number_is_a_fact(tmp_path):
    import shutil

    run = tmp_path / "run"
    shutil.copytree(DEMO, run)
    a = build_alert(run)
    L = Ledger.load(run)
    for lang in ("en", "ne"):
        assert a[lang], lang
        assert len(a[lang]) <= 480, lang
        assert check(a[lang], L).ok, (lang, a[lang])
    assert "not checked on the ground" in a["en"]
