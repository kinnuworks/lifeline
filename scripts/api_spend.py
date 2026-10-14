"""Print Claude API spend from data/api_spend.jsonl: total, per model, per purpose."""
from collections import defaultdict

from lifeline.copilot.spend import budget_usd, records, spend_file


def main() -> None:
    recs = records()
    total = sum(r.get("usd", 0) for r in recs)
    print(f"ledger: {spend_file()}")
    print(f"calls: {len(recs)}   total: ${total:.4f}   budget: ${budget_usd():.2f}   left: ${budget_usd() - total:.4f}")
    for field in ("model", "purpose"):
        agg = defaultdict(lambda: [0, 0, 0, 0.0])
        for r in recs:
            a = agg[r.get(field) or "-"]
            a[0] += 1; a[1] += r.get("input_tokens", 0); a[2] += r.get("output_tokens", 0); a[3] += r.get("usd", 0)
        print(f"\nper {field}:")
        print(f"  {'name':<28}{'calls':>6}{'in tok':>10}{'out tok':>10}{'usd':>10}")
        for k, (n, i, o, u) in sorted(agg.items(), key=lambda kv: -kv[1][3]):
            print(f"  {k:<28}{n:>6}{i:>10}{o:>10}{u:>10.4f}")


if __name__ == "__main__":
    main()
