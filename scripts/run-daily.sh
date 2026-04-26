#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO/.venv/bin/python"
AUTO_PUSH_REPORTS="${AUTO_PUSH_REPORTS:-0}"
STATUS_FILE="$REPO/data/run-status.json"
FAILED_SENTINEL="$REPO/data/LAST_RUN_FAILED"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $REPO/.venv" >&2
  exit 1
fi

cd "$REPO"
export PYTHONPATH="$REPO"

mkdir -p "$REPO/data"

RUN_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# Run the daily pipeline; capture exit code without triggering set -e
set +e
"$VENV_PY" -m app.main daily "$@"
EXIT_CODE=$?
set -e

RUN_END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
TODAY="$("$VENV_PY" -c "from datetime import date; print(date.today().isoformat())")"

if [[ $EXIT_CODE -eq 0 ]]; then
  RUN_STATUS="success"
  rm -f "$FAILED_SENTINEL"
else
  RUN_STATUS="failed"
  echo "RESEARCH ASSISTANT DAILY RUN FAILED — $TODAY (exit $EXIT_CODE)" > "$FAILED_SENTINEL"
  echo "Check logs or run manually: $VENV_PY -m app.main daily" >> "$FAILED_SENTINEL"
fi

# Write machine-readable status for monitoring scripts or cron wrappers
"$VENV_PY" - <<PY
import json
with open("$STATUS_FILE", "w") as f:
    json.dump({
        "status": "$RUN_STATUS",
        "date": "$TODAY",
        "started": "$RUN_START",
        "finished": "$RUN_END",
        "exit_code": $EXIT_CODE,
    }, f, indent=2)
PY

if [[ $EXIT_CODE -ne 0 ]]; then
  echo "Daily run FAILED (exit $EXIT_CODE). Sentinel written to: $FAILED_SENTINEL" >&2
  exit $EXIT_CODE
fi

# Generated reports, caches, and docs are ignored by default so the
# repository stays focused on code. Flip AUTO_PUSH_REPORTS=1 only when this
# machine is intentionally publishing those artifacts.
if [[ "$AUTO_PUSH_REPORTS" != "1" ]]; then
  exit 0
fi

TARGETS=(
  "data/cache/latest.json"
  "data/cache/daily-$TODAY.json"
  "data/reports"
  "docs"
  "data/open_questions.md"
  "data/field_state.md"
  "data/run-status.json"
  "data/cache/figures"
)

# Only add files/dirs that actually exist
EXISTING=()
for t in "${TARGETS[@]}"; do
  [[ -e "$REPO/$t" ]] && EXISTING+=("$t")
done

if [[ ${#EXISTING[@]} -gt 0 ]]; then
  git add -f "${EXISTING[@]}"
fi

if git diff --cached --quiet; then
  echo "No report/site changes to commit."
  exit 0
fi

git commit -m "Publish daily report for $TODAY"
git push origin main
