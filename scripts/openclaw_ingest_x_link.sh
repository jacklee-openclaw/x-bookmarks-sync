#!/usr/bin/env bash
set -euo pipefail

# Legacy compatibility wrapper (X-only name kept for old callers)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$ROOT_DIR/scripts/openclaw_ingest_link.sh" "$@"
