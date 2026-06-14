#!/usr/bin/env bash
# Pull the latest code and (re)deploy the stack. Run on the Pi:
#
#   bash scripts/deploy.sh
#
# Builds from source (arm64), brings the containers up, applies any new tables.
set -euo pipefail
cd "$(dirname "$0")/.."

git pull
docker compose up -d --build
docker compose run --rm app python -m app.initdb
echo "deployed."
