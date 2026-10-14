#!/bin/sh
# The extra runs: a no-flood date (radar only: any Sentinel-2 image in the 40 days after
# 25 July 2026 would also show the 26 August flood) and two floods the settings never saw.
cd "$(dirname "$0")/.."
uv run lifeline run --bbox 84.93 27.78 85.45 28.30 --date 2026-07-25 --id placebo-2026-07 --label "No-flood check: Trishuli corridor, 25 Jul 2026" --no-optical > runs/placebo-2026-07.log 2>&1
uv run lifeline run --bbox 85.48 27.78 85.68 28.05 --date 2021-06-15 --id melamchi-2021 --label "Melamchi flood, Nepal, 15 Jun 2021" > runs/melamchi-2021.log 2>&1
uv run lifeline run --bbox 88.40 27.15 88.70 27.65 --date 2023-10-04 --id teesta-2023 --label "Teesta glacial lake flood, Sikkim, 4 Oct 2023" > runs/teesta-2023.log 2>&1
for id in placebo-2026-07 melamchi-2021 teesta-2023; do rm -rf web/data/$id; uv run python scripts/export_web.py $id; done > runs/export_extra.log 2>&1
echo EXTRA_DONE >> runs/export_extra.log
