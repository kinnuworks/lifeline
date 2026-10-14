#!/bin/sh
# Score one run against the official maps, after the run. Never an input.
uv run python scripts/validate_emsr927.py compare "runs/${1:-trishuli-2026}" --stage both
