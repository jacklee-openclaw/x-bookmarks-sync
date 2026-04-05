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

usage() {
  cat <<'USAGE'
Usage:
  openclaw_ingest_link.sh [--source <label>] [--no-git] [--force] [--dry-run] '<x_status_url_or_text>'

Options:
  --source <label>  Source label for queue items (default: telegram-auto)
  --no-git          Disable auto git commit/push for this run
  --force           Force re-enqueue if task already in done
  --dry-run         Enqueue only; do not process sync
USAGE
}

log_ts() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

SOURCE_LABEL="${KB_INGEST_SOURCE:-telegram-auto}"
NO_GIT=0
FORCE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      [[ $# -ge 2 ]] || { echo "[ERROR] --source requires a value"; usage; exit 64; }
      SOURCE_LABEL="$2"
      shift 2
      ;;
    --no-git)
      NO_GIT=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "[ERROR] unknown option: $1"
      usage
      exit 64
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -lt 1 ]]; then
  usage
  exit 64
fi

RAW_TEXT="$*"
if ! grep -Eqi 'https?://(x\.com|twitter\.com)/[^[:space:]]+/status/[0-9]+' <<<"$RAW_TEXT"; then
  echo "[ERROR] no valid X status URL found in input text"
  exit 65
fi

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

CLI="$ROOT_DIR/x_links_to_kb.py"
if [[ ! -f "$CLI" ]]; then
  echo "[ERROR] cli entry not found: $CLI"
  exit 66
fi

STATE_ROOT="${KB_STATE_ROOT:-.state}"
if [[ "$STATE_ROOT" != /* ]]; then
  STATE_ROOT="$ROOT_DIR/$STATE_ROOT"
fi
LOCK_DIR="$STATE_ROOT/locks/openclaw-ingest.lock"
BRIDGE_LOG_DIR="$STATE_ROOT/bridge"
BRIDGE_LOG_FILE="$BRIDGE_LOG_DIR/openclaw-ingest.log"
mkdir -p "$STATE_ROOT/locks" "$BRIDGE_LOG_DIR"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  printf '%s [WARN] lock active, skip ingest lock=%s\n' "$(log_ts)" "$LOCK_DIR" | tee -a "$BRIDGE_LOG_FILE"
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

printf '%s [INFO] start ingest source=%s dry_run=%s force=%s no_git=%s cwd=%s python=%s\n' \
  "$(log_ts)" "$SOURCE_LABEL" "$DRY_RUN" "$FORCE" "$NO_GIT" "$PWD" "$PYTHON_BIN" >> "$BRIDGE_LOG_FILE"
printf '%s [INFO] input=%s\n' "$(log_ts)" "$(echo "$RAW_TEXT" | tr '\n' ' ' | cut -c1-600)" >> "$BRIDGE_LOG_FILE"

CMD=("$PYTHON_BIN" "$CLI")
if [[ "$DRY_RUN" -eq 1 ]]; then
  CMD+=("enqueue" "--text" "$RAW_TEXT" "--source" "$SOURCE_LABEL")
  if [[ "$FORCE" -eq 1 ]]; then
    printf '%s [WARN] --force ignored in dry-run mode\n' "$(log_ts)" >> "$BRIDGE_LOG_FILE"
  fi
else
  CMD+=("sync" "--text" "$RAW_TEXT" "--source" "$SOURCE_LABEL")
  if [[ "$FORCE" -eq 1 ]]; then
    CMD+=("--force")
  fi
  if [[ "$NO_GIT" -eq 1 || "${KB_INGEST_FORCE_NO_GIT:-0}" == "1" ]]; then
    CMD+=("--no-git")
  fi
fi

set +e
OUTPUT="$("${CMD[@]}" 2>&1)"
RC=$?
set -e
printf '%s [INFO] command_rc=%s\n' "$(log_ts)" "$RC" >> "$BRIDGE_LOG_FILE"

if [[ $RC -ne 0 ]]; then
  printf '%s [ERROR] command_failed output=%s\n' "$(log_ts)" "$(echo "$OUTPUT" | tr '\n' ' ' | cut -c1-1000)" >> "$BRIDGE_LOG_FILE"
  echo "$OUTPUT"
  exit $RC
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "$OUTPUT"
  printf '%s [INFO] dry-run completed\n' "$(log_ts)" >> "$BRIDGE_LOG_FILE"
  exit 0
fi

# Print concise receipt if sync output is parseable JSON.
"$PYTHON_BIN" - <<'PY' "$OUTPUT"
import json
import sys

raw = sys.argv[1]
obj = None
for line in raw.splitlines():
    s = line.strip()
    if not (s.startswith("{") and s.endswith("}")):
        continue
    try:
        item = json.loads(s)
    except Exception:
        continue
    if item.get("action") == "sync":
        obj = item

if obj is None:
    print(raw)
    raise SystemExit(0)

processed = obj.get("processed") or []
errors = obj.get("errors") or []
queued = obj.get("queued") or {}

if not processed:
    print("[INFO] no item processed in this run")
    print(json.dumps({"queued": queued, "errors": errors}, ensure_ascii=False))
    raise SystemExit(0)

first = processed[0]
print("✅ 已入库")
print(f"- 文件: {first.get('path', '')}")
print(f"- 分类: {first.get('category', '')}")
print(f"- 链接: {first.get('url', '')}")
print(f"- 质量分: {first.get('quality_score', '')}")

run_log = obj.get("run_log", "")
if run_log:
    print(f"- run log: {run_log}")

git = obj.get("git") or {}
status = git.get("status")
if status == "ok":
    print(f"- git push: ok ({git.get('branch','')}/{git.get('commit','')})")
elif status == "skipped":
    print(f"- git push: skipped ({git.get('reason','')})")
else:
    print(f"- git push: {status or 'unknown'}")
PY
printf '%s [INFO] ingest completed successfully\n' "$(log_ts)" >> "$BRIDGE_LOG_FILE"
