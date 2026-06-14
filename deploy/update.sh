#!/usr/bin/env bash
# Refresh the goamines deployment. Run as the `goamines` user from anywhere:
#   ~/goamines/deploy/update.sh
# Pulls latest code, syncs deps, rebuilds the DB if raw data is present (otherwise
# just refreshes the route map from the existing DB), then prompts for a restart.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull --ff-only
uv sync

if [ -d data1 ] && [ -d data2 ]; then
  uv run python ingest.py            # full rebuild: regenerates goamines.db + route map
else
  uv run python build_route_map.py   # no raw data on box: just refresh map from current DB
  echo "NOTE: data1/ or data2/ absent — goamines.db served as-is (not rebuilt)."
fi

echo
echo "Done. Now restart the service:"
echo "    sudo systemctl restart goamines"
