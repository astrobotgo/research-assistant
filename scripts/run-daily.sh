#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO/.venv/bin/python"
AUTO_PUSH_REPORTS="${AUTO_PUSH_REPORTS:-0}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $REPO/.venv" >&2
  exit 1
fi

cd "$REPO"
export PYTHONPATH="$REPO"

"$VENV_PY" -m app.main daily "$@"

if [[ "$AUTO_PUSH_REPORTS" != "1" ]]; then
  exit 0
fi

TODAY="$("$VENV_PY" - <<'PY'
from datetime import date
print(date.today().isoformat())
PY
)"

TARGETS=(
  "data/cache/latest.json"
  "data/cache/daily-$TODAY.json"
  "data/reports"
  "docs"
)

git add "${TARGETS[@]}"

if git diff --cached --quiet; then
  echo "No report/site changes to commit."
  exit 0
fi

git commit -m "Publish daily report for $TODAY"
git push origin main
