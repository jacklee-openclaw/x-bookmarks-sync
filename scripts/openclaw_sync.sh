#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  # shellcheck disable=SC1091
  source ".env"
fi

STATE_ROOT="${KB_STATE_ROOT:-.state}"
LOCK_DIR="$STATE_ROOT/locks/openclaw-sync.lock"
mkdir -p "$STATE_ROOT/locks"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[INFO] sync is already running, skip."
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

SYNC_LIMIT="${KB_SYNC_LIMIT:-30}"
python3 x_links_to_kb.py sync --limit "$SYNC_LIMIT"
