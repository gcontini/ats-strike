#!/bin/bash
# Every ladder bullet of a v2 pool on the rung its printed lines fill (scripts/v2_rerung.py).
#   scripts/v2_rerung.sh report <pool.json>    list the moves
#   scripts/v2_rerung.sh edit <pool.json>      list them and save the file
[ $# -eq 2 ] || { echo "usage: $0 report|edit <pool.json>"; exit 1; }
pool=$(realpath "$2")
cd "$(dirname "$0")/.." && uv run python scripts/v2_rerung.py "$1" "$pool"
