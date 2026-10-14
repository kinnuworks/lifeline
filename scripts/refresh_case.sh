#!/bin/sh
# Rebuild everything downstream of the radar map for one run, then the website data.
set -e
ID=${1:-trishuli-2026}
uv run lifeline finish "$ID"
sh scripts/validate_run.sh "$ID" || true
uv run python scripts/copilot_samples.py "$ID"
uv run python scripts/redteam.py "runs/$ID" --model
rm -rf "web/data/$ID"
uv run python scripts/export_web.py "$ID"
echo REFRESH_DONE
