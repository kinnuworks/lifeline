#!/bin/sh
# Build the folder that is pushed to the Hugging Face Space (Docker).
set -e
cd "$(dirname "$0")/.."
OUT=${1:-/tmp/lifeline-space}
rm -rf "$OUT"; mkdir -p "$OUT/runs"
rsync -a --exclude __pycache__ src scripts docs pyproject.toml uv.lock Dockerfile LICENSE "$OUT/" 2>/dev/null || true
cp deploy/space_README.md "$OUT/README.md"
for r in trishuli-2026 melamchi-2021 teesta-2023 placebo-2026-07; do
  rsync -a --exclude 'rasters/s1_*' --exclude 'rasters/model_prob*' --exclude 'rasters/optical_change.tif' --exclude 'rasters/*_radar.tif' --exclude web --exclude webdata "runs/$r" "$OUT/runs/"
done
du -sh "$OUT"
