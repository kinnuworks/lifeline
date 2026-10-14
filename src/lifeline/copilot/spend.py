"""Hard spending cap for Claude API calls.

One ledger file is shared by every run and process: data/api_spend.jsonl, one
JSON line per call (timestamp, model, input_tokens, output_tokens, usd, run_id,
purpose). Before a call, cumulative spend plus a conservative estimate for the
call (the full max_tokens at the output price, plus the prompt size at the input
price) must stay within ANTHROPIC_BUDGET_USD (default 15). Otherwise the call is
not made and BudgetExhausted is raised.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

# USD per 1M tokens: (input, output)
PRICES = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}
DEFAULT_PRICE = PRICES["claude-opus-5-5"]
DEFAULT_BUDGET_USD = 15.0
ROOT = Path(__file__).resolve().parents[3]


class BudgetExhausted(RuntimeError):
    pass


def spend_file() -> Path:
    return Path(os.environ.get("BATO_SPEND_FILE") or ROOT / "data" / "api_spend.jsonl")


def budget_usd() -> float:
    try:
        return float(os.environ.get("ANTHROPIC_BUDGET_USD") or DEFAULT_BUDGET_USD)
    except ValueError:
        return DEFAULT_BUDGET_USD


def price(model: str) -> tuple[float, float]:
    return PRICES.get(model, DEFAULT_PRICE)


def cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = price(model)
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


def records() -> list[dict]:
    p = spend_file()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def total_spent() -> float:
    return sum(float(r.get("usd", 0)) for r in records())


def check_budget(model: str, max_tokens: int, prompt_chars: int = 0) -> float:
    """Raise BudgetExhausted if this call could push spend over the budget. Returns the estimate."""
    est = cost(model, prompt_chars // 3 + 1, max_tokens)
    spent = total_spent()
    if spent + est > budget_usd():
        raise BudgetExhausted(f"spent ${spent:.4f} + estimate ${est:.4f} > budget ${budget_usd():.2f}")
    return est


def record(model: str, input_tokens: int, output_tokens: int, run_id: str = "", purpose: str = "") -> dict:
    rec = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "model": model, "input_tokens": int(input_tokens),
           "output_tokens": int(output_tokens), "usd": round(cost(model, input_tokens, output_tokens), 6),
           "run_id": run_id, "purpose": purpose}
    p = spend_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(rec) + "\n")
        fcntl.flock(fh, fcntl.LOCK_UN)
    return rec
