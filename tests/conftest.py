import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """A fresh copy of the synthetic run with facts.json built."""
    from make_run_demo import write
    from lifeline.copilot.ledger import build_facts

    out = write(tmp_path / "run_demo")
    build_facts(out)
    return out


@pytest.fixture()
def ledger(run_dir):
    from lifeline.copilot.ledger import Ledger
    return Ledger.load(run_dir)
