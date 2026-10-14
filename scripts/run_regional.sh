#!/bin/sh
# Two more floods the settings never saw: the Beas at Mandi (July 2023) and the Chamoli disaster (February 2021).
cd "$(dirname "$0")/.."
uv run lifeline run --bbox 76.85 31.62 77.17 31.92 --date 2023-07-09 --id beas-mandi-2023 --label "Beas flood, Mandi and Kullu, Himachal, 9 Jul 2023" > runs/beas-mandi-2023.log 2>&1
uv run lifeline run --bbox 79.55 30.38 79.85 30.62 --date 2021-02-07 --id chamoli-2021 --label "Chamoli rock-ice avalanche and flood, Uttarakhand, 7 Feb 2021" > runs/chamoli-2021.log 2>&1
for id in beas-mandi-2023 chamoli-2021; do rm -rf web/data/$id; uv run python scripts/export_web.py $id; done > runs/export_regional.log 2>&1
echo REGIONAL_DONE >> runs/export_regional.log
