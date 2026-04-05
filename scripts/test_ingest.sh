#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INGEST_SCRIPT="$ROOT_DIR/scripts/openclaw_ingest_link.sh"

usage() {
  cat <<'USAGE'
Usage:
  scripts/test_ingest.sh '<x_status_url_or_text>'

Purpose:
  Minimal bridge test without Telegram.
  It calls openclaw_ingest_link.sh with --no-git and prints status summary.
USAGE
}

if [[ $# -lt 1 ]]; then
  usage
  exit 64
fi

if [[ ! -x "$INGEST_SCRIPT" ]]; then
  echo "[ERROR] ingest script not executable: $INGEST_SCRIPT"
  exit 66
fi

RAW_TEXT="$*"
echo "[INFO] testing ingest via: $INGEST_SCRIPT"
echo "[INFO] input: $RAW_TEXT"

"$INGEST_SCRIPT" --no-git "$RAW_TEXT"

echo "[INFO] current status:"
(cd "$ROOT_DIR" && python3 "$ROOT_DIR/x_links_to_kb.py" status)
