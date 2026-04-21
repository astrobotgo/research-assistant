#!/usr/bin/env bash
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_PY="$REPO/.venv/bin/python"
MODE="${1:-systemd}"

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing venv at $REPO/.venv — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

install_systemd() {
  local UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  mkdir -p "$UNIT_DIR"

  cat >"$UNIT_DIR/research-assistant-daily.service" <<EOF
[Unit]
Description=Research assistant — daily arXiv scan and digest
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=$REPO
Environment=PYTHONPATH=$REPO
EnvironmentFile=-$REPO/.env
ExecStart=$VENV_PY -m app.main daily

[Install]
WantedBy=default.target
EOF

  cp -f "$REPO/systemd/research-assistant-daily.timer" "$UNIT_DIR/research-assistant-daily.timer"

  systemctl --user daemon-reload
  systemctl --user enable research-assistant-daily.timer
  systemctl --user start research-assistant-daily.timer

  echo "Installed user units under $UNIT_DIR"
  echo "Timer status:"
  systemctl --user status research-assistant-daily.timer --no-pager || true
  echo ""
  echo "Next runs (list timer):"
  systemctl --user list-timers research-assistant-daily.timer --no-pager || true
  echo ""
  if loginctl show-user "$(id -un)" 2>/dev/null | grep -q 'Linger=no'; then
    echo "To run while logged out / after reboot without a session, enable lingering once:"
    echo "  loginctl enable-linger $(id -un)"
  fi
}

install_cron() {
  local MARK="# research-assistant daily"
  local LINE="30 6 * * * cd $REPO && PYTHONPATH=$REPO $VENV_PY -m app.main daily >>$REPO/data/cron-daily.log 2>&1"
  mkdir -p "$REPO/data"
  (crontab -l 2>/dev/null | grep -vF "$MARK" || true; echo "$MARK"; echo "$LINE") | crontab -
  echo "Installed user crontab entry ($MARK). Log: $REPO/data/cron-daily.log"
  echo "Edit time with: crontab -e"
}

case "$MODE" in
  systemd) install_systemd ;;
  cron) install_cron ;;
  *)
    echo "Usage: $0 [systemd|cron]  (default: systemd)" >&2
    exit 1
    ;;
esac

echo ""
echo "Ollama must be running when the job fires (e.g. ollama serve as a user or system service)."
