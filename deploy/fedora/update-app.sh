#!/usr/bin/env bash
# Update a running install after copying a new checkout to APP_DIR (or git pull there).
#   sudo bash /opt/teamsync/deploy/fedora/update-app.sh
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/teamsync}"
APP_USER="${APP_USER:-teamsync}"
APP_GROUP="${APP_GROUP:-teamsync}"

if [[ $EUID -ne 0 ]]; then echo "run with sudo" >&2; exit 1; fi
chown -R "$APP_USER:$APP_GROUP" "$APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only; fi
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --quiet --upgrade "$APP_DIR"
systemctl restart teamsync
sleep 2
systemctl --no-pager status teamsync | head -5
curl -fsS http://127.0.0.1:${APP_PORT:-8000}/health && echo
