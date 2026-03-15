#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  # shellcheck disable=SC1091
  source ".env"
fi

LOCK_DIR="/tmp/x_to_cdns_sync.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[INFO] sync is already running, skip."
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

python3 sync_bookmarks.py
