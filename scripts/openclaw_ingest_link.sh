#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ".env" ]]; then
  # shellcheck disable=SC1091
  source ".env"
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 '<x_status_url_or_text>'"
  exit 1
fi

STATE_ROOT="${KB_STATE_ROOT:-.state}"
LOCK_DIR="$STATE_ROOT/locks/openclaw-ingest.lock"
mkdir -p "$STATE_ROOT/locks"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[INFO] ingest is already running, skip."
  exit 0
fi
trap 'rmdir "$LOCK_DIR" >/dev/null 2>&1 || true' EXIT

RAW_TEXT="$*"

set +e
OUTPUT="$(python3 x_links_to_kb.py sync --text "$RAW_TEXT" --source telegram-auto 2>&1)"
RC=$?
set -e

if [[ $RC -ne 0 ]]; then
  echo "$OUTPUT"
  exit $RC
fi

# Print concise receipt if sync output is parseable JSON.
python3 - <<'PY' "$OUTPUT"
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
