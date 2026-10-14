#!/bin/sh
# Generality and false-alarm checks: a no-flood date over the case-study area,
# and two other Himalayan floods the system was never tuned on.
set -u
cd "$(dirname "$0")/.."
until grep -qE "Finished in|Run failed" runs/trishuli-2026.log; do sleep 10; done
uv run lifeline run --bbox 84.93 27.78 85.45 28.30 --date 2026-07-25 --id placebo-2026-07 --label "No-flood check: Trishuli corridor, 25 Jul 2026" --no-optical > runs/placebo-2026-07.log 2>&1
uv run lifeline run --bbox 85.48 27.78 85.68 28.05 --date 2021-06-15 --id melamchi-2021 --label "Melamchi flood, Nepal, 15 Jun 2021" --no-optical > runs/melamchi-2021.log 2>&1
uv run lifeline run --bbox 88.40 27.15 88.70 27.65 --date 2023-10-04 --id teesta-2023 --label "Teesta glacial lake flood, Sikkim, 4 Oct 2023" --no-optical > runs/teesta-2023.log 2>&1
echo ALL_DONE
