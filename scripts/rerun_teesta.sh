#!/bin/sh
cd "$(dirname "$0")/.."
uv run lifeline run --bbox 88.40 27.15 88.70 27.65 --date 2023-10-04 --id teesta-2023 --label "Teesta glacial lake flood, Sikkim, 4 Oct 2023" > runs/teesta-2023.log 2>&1
for id in placebo-2026-07 melamchi-2021 teesta-2023; do rm -rf web/data/$id; uv run python scripts/export_web.py $id; done > runs/export_extra.log 2>&1
echo EXTRA_DONE >> runs/export_extra.log
