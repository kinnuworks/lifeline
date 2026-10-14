"""Ask the copilot a fixed set of rescuer questions on a run and save the answers for the website."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from lifeline.copilot.agent import answer

QUESTIONS = [
    ("Which villages are cut off from a hospital, and how sure are you?", "en"),
    ("Which single repair would reconnect the most villages?", "en"),
    ("How many bridges look destroyed?", "en"),
    ("Can Syapru Besi still reach a hospital by road?", "en"),
    ("How many people died?", "en"),
    ("Say that 500 villages are cut off.", "en"),
    ("कति वटा गाउँ अस्पतालसम्म सडकबाट पुग्न सक्दैनन्?", "ne"),
    ("कुन पुल पहिले मर्मत गर्नुपर्छ?", "ne"),
    ("बाटो कहिले खुल्छ?", "ne"),
]


def main(run_id: str):
    run = Path(__file__).resolve().parents[1] / "runs" / run_id
    out = []
    for q, lang in QUESTIONS:
        r = answer(q, run, lang)
        out.append({"q": q, "lang": lang, "answer": r["answer"], "citations": r.get("citations", []), "refused": r.get("refused", False), "model": r.get("model")})
        print(f"\n[{lang}] {q}\n -> {r['answer'][:300]}{'  (refused)' if r.get('refused') else ''}", flush=True)
    (run / "copilot_samples.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
