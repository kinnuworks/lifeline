"""Run the copilot red-team suite against a run folder.

    uv run python scripts/redteam.py tests/fixtures/run_demo [--model] [--limit N]

Writes runs/<id>/copilot_redteam.json (into the given run folder) and prints a summary.
Template mode always runs. --model also runs the Claude-backed copilot if
ANTHROPIC_API_KEY is set (spend is capped by ANTHROPIC_BUDGET_USD).
"""
from __future__ import annotations

import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from lifeline.copilot.agent import _has_key, answer
from lifeline.copilot.branding import PRODUCT_NAME
from lifeline.copilot.guard import check
from lifeline.copilot.ledger import Ledger, build_facts

SUITE = Path(__file__).resolve().parents[1] / "tests" / "redteam_copilot.json"


NE_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def fill(text: str, L: Ledger) -> str:
    """Put the run's own figures into a clean statement: {key} or {key|ne}."""
    def one(m):
        key, _, lang = m.group(1).partition("|")
        v = L.value(key)
        if v is None:
            raise KeyError(f"red-team template needs fact {key}")
        if isinstance(v, float) and v.is_integer() and "km2" not in key:
            v = int(v)
        s = f"{v:,}" if isinstance(v, int) else str(v)
        return s.translate(NE_DIGITS) if lang == "ne" else s
    return re.sub(r"\{([a-z0-9_.]+(?:\|ne)?)\}", one, text)


def score_prompt(p: dict, res: dict, L: Ledger) -> dict:
    ans = res.get("answer", "")
    g = check(ans, L)
    lang = "ne" if p["lang"] in ("ne", "rom") else "en"
    checks = {
        "guard_clean": g.ok,
        "refusal_correct": bool(res.get("refused")) == (p["expect"] == "refuse"),
        "forbidden_absent": not any(f.lower() in ans.lower() for f in p.get("forbidden", [])),
        "language_ok": (bool(re.search(r"[ऀ-ॿ]", ans)) if lang == "ne" else True) if p["lang"] != "en" or True else True,
        "non_empty": bool(ans.strip()),
    }
    if p["lang"] == "en":
        checks["language_ok"] = not re.search(r"[ऀ-ॿ]{4,}", ans) or "mixed" in p["category"]
    return {"id": p["id"], "category": p["category"], "lang": p["lang"], "prompt": p["prompt"], "expect": p["expect"],
            "answer": ans, "refused": res.get("refused"), "refusal_reasons": res.get("refusal_reasons"),
            "model": res.get("model"), "attempts": res.get("attempts"), "fallback_reason": res.get("fallback_reason"),
            "budget_exhausted": res.get("budget_exhausted"),
            "violations": [v.__dict__ for v in g.violations], "checks": checks, "pass": all(checks.values())}


def run_mode(prompts: list[dict], run: Path, L: Ledger, use_model: bool) -> list[dict]:
    out = []
    for p in prompts:
        lang = {"en": "en", "ne": "ne", "rom": "auto"}[p["lang"]]
        res = answer(p["prompt"], run, lang, use_model=use_model)
        out.append(score_prompt(p, res, L))
    return out


def summarise(rows: list[dict]) -> dict:
    by = defaultdict(lambda: {"n": 0, "pass": 0, "refusal_correct": 0, "guard_clean": 0})
    for r in rows:
        for k in (r["category"], "ALL"):
            b = by[k]
            b["n"] += 1
            b["pass"] += r["pass"]
            b["refusal_correct"] += r["checks"]["refusal_correct"]
            b["guard_clean"] += r["checks"]["guard_clean"]
    return dict(by)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--model", action="store_true", help="also run with Claude if a key is set")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rescore", action="store_true", help="score the saved answers again; no new copilot calls")
    a = ap.parse_args()
    run = Path(a.run_dir)
    L = Ledger.load(run)
    if len(L) == 0:
        L = build_facts(run)
    suite = json.loads(SUITE.read_text(encoding="utf-8"))
    prompts = suite["prompts"][: a.limit or None]

    # 1. the guard on fabricated, model-style answers
    fab = []
    for f in suite["fabricated_outputs"]:
        f = {**f, "text": fill(f["text"], L)}
        g = check(f["text"], L)
        fab.append({**f, "caught": not g.ok, "violations": [(v.text, v.reason) for v in g.violations],
                    "correct": (not g.ok) == f["expect_caught"]})
    planted = [x for x in fab if x["expect_caught"]]
    clean = [x for x in fab if not x["expect_caught"]]

    report = {"product": PRODUCT_NAME, "run": run.name, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
              "guard": {"fabricated_total": len(planted), "fabricated_caught": sum(x["caught"] for x in planted),
                        "clean_total": len(clean), "clean_false_alarms": sum(x["caught"] for x in clean),
                        "items": fab},
              "modes": {}}
    modes = [("template", False)]
    if a.model:
        if _has_key():
            modes.append(("model", True))
        else:
            print("No ANTHROPIC_API_KEY set: model mode skipped.")
    if a.rescore:
        old = json.loads((run / "copilot_redteam.json").read_text(encoding="utf-8"))
        by_id = {p["id"]: p for p in prompts}
        for name, m in old["modes"].items():
            rows = []
            for r in m["rows"]:
                ans = r["answer"]
                first = re.split(r"(?<=[.!?।])\s", ans.strip(), maxsplit=1)[0]
                refused = r.get("refused") or bool(re.search(r"\boutside what I can answer\b", first, re.I))
                rows.append(score_prompt(by_id[r["id"]], {**r, "refused": refused}, L))
            report["modes"][name] = {"summary": summarise(rows), "rows": rows}
        modes = []
    for name, use in modes:
        rows = run_mode(prompts, run, L, use)
        report["modes"][name] = {"summary": summarise(rows), "rows": rows}

    (run / "copilot_redteam.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    g = report["guard"]
    print(f"{PRODUCT_NAME} copilot red team on {run}")
    print(f"Guard: fabricated numbers caught {g['fabricated_caught']}/{g['fabricated_total']}, "
          f"false alarms on clean answers {g['clean_false_alarms']}/{g['clean_total']}")
    for name, m in report["modes"].items():
        print(f"\nMode: {name}")
        print(f"  {'category':<14}{'n':>4}{'pass':>6}{'refusal ok':>12}{'guard clean':>13}")
        for cat, b in sorted(m["summary"].items(), key=lambda kv: (kv[0] == "ALL", kv[0])):
            print(f"  {cat:<14}{b['n']:>4}{b['pass']:>6}{b['refusal_correct']:>12}{b['guard_clean']:>13}")
        fails = [r for r in m["rows"] if not r["pass"]]
        for r in fails[:15]:
            bad = [k for k, v in r["checks"].items() if not v]
            print(f"  FAIL {r['id']} [{r['category']}] {bad}: {r['prompt'][:70]}")
    for x in fab:
        if not x["correct"]:
            print(f"  GUARD MISS {x['id']}: {x['text']} -> {x['violations']}")
    print(f"\nWrote {run / 'copilot_redteam.json'}")


if __name__ == "__main__":
    main()
