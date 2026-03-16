#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  # shellcheck disable=SC1091
  source ".env"
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 '<x_status_url_or_message_text>'"
  exit 1
fi

LOCK_DIR="/tmp/x_to_cdns_ingest.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[INFO] ingest is already running, skip."
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

RAW_TEXT="$*"
python3 x_links_to_kb.py capture-sync --text "$RAW_TEXT" --source openclaw
