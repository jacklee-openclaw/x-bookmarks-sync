#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE="${KB_ENV_FILE:-$ROOT_DIR/.env}"
load_env_defaults() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    [[ -n "$key" ]] || continue
    if [[ -z "${!key+x}" ]]; then
      val="${val#"${val%%[![:space:]]*}"}"
      val="${val%"${val##*[![:space:]]}"}"
      val="${val%\"}"
      val="${val#\"}"
      val="${val%\'}"
      val="${val#\'}"
      export "$key=$val"
    fi
  done < "$env_file"
}
load_env_defaults "$ENV_FILE"

STATE_ROOT="${KB_STATE_ROOT:-.state}"
if [[ "$STATE_ROOT" != /* ]]; then
  STATE_ROOT="$ROOT_DIR/$STATE_ROOT"
fi
LOCK_DIR="$STATE_ROOT/locks/openclaw-sync.lock"
BRIDGE_LOG_DIR="$STATE_ROOT/bridge"
BRIDGE_LOG_FILE="$BRIDGE_LOG_DIR/openclaw-sync.log"
mkdir -p "$STATE_ROOT/locks"
mkdir -p "$BRIDGE_LOG_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[INFO] sync is already running, skip."
  printf '%s [WARN] lock active skip lock=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$LOCK_DIR" >> "$BRIDGE_LOG_FILE"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

resolve_python_bin() {
  if [[ -n "${KB_PYTHON_BIN:-}" ]]; then
    if [[ -x "$KB_PYTHON_BIN" ]]; then
      echo "$KB_PYTHON_BIN"
      return 0
    fi
    return 1
  fi
  if [[ -x "$ROOT_DIR/.venv/bin/python3" ]]; then
    echo "$ROOT_DIR/.venv/bin/python3"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

if ! PYTHON_BIN="$(resolve_python_bin)"; then
  echo "[ERROR] python3 not found. Set KB_PYTHON_BIN or install python3."
  exit 69
fi

SYNC_LIMIT="${KB_SYNC_LIMIT:-30}"
printf '%s [INFO] start sync limit=%s python=%s cwd=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$SYNC_LIMIT" "$PYTHON_BIN" "$PWD" >> "$BRIDGE_LOG_FILE"
if ! OUTPUT="$("$PYTHON_BIN" "$ROOT_DIR/x_links_to_kb.py" sync --limit "$SYNC_LIMIT" 2>&1)"; then
  printf '%s [ERROR] sync failed output=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$(echo "$OUTPUT" | tr '\n' ' ' | cut -c1-1000)" >> "$BRIDGE_LOG_FILE"
  echo "$OUTPUT"
  exit 1
fi
echo "$OUTPUT"
printf '%s [INFO] sync completed\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" >> "$BRIDGE_LOG_FILE"
