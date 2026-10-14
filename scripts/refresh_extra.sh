#!/bin/sh
# Re-run the extra events with the current code and put them on the website.
cd "$(dirname "$0")/.."
uv run lifeline run --bbox 85.48 27.78 85.68 28.05 --date 2021-06-15 --id melamchi-2021 --label "Melamchi flood, Nepal, 15 Jun 2021" --no-optical > runs/melamchi-2021.log 2>&1
for id in placebo-2026-07 teesta-2023; do uv run lifeline finish $id >> runs/refresh_extra.log 2>&1; done
for id in melamchi-2021 placebo-2026-07 teesta-2023; do rm -rf web/data/$id; uv run python scripts/export_web.py $id >> runs/refresh_extra.log 2>&1; done
echo EXTRA_DONE >> runs/refresh_extra.log
