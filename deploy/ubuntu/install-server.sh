#!/usr/bin/env bash
# Install TeamSync on Ubuntu/Debian: service account, venv, systemd unit, nginx with
# a self-signed certificate, ufw rules. Untested until the Fedora bundle has been
# exercised; the steps mirror it.
#
#   sudo bash /opt/teamsync/deploy/ubuntu/install-server.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/teamsync}"
APP_USER="${APP_USER:-teamsync}"
APP_GROUP="${APP_GROUP:-teamsync}"
APP_DATA_DIR="${APP_DATA_DIR:-/var/lib/teamsync}"
APP_ENV_DIR="${APP_ENV_DIR:-/etc/teamsync}"
APP_ENV_FILE="${APP_ENV_FILE:-$APP_ENV_DIR/teamsync.env}"
APP_PORT="${APP_PORT:-8000}"
SERVER_NAME="${SERVER_NAME:-teamsync.eternalhotels.com}"
TLS_DIR="${TLS_DIR:-/etc/ssl/teamsync}"
TLS_CERT_FILE="${TLS_CERT_FILE:-$TLS_DIR/$SERVER_NAME.crt}"
TLS_KEY_FILE="${TLS_KEY_FILE:-$TLS_DIR/$SERVER_NAME.key}"
PYTHON="${PYTHON:-python3}"

if [[ $EUID -ne 0 ]]; then echo "run with sudo" >&2; exit 1; fi
if [[ ! -f "$APP_DIR/pyproject.toml" ]]; then echo "copy the repo to $APP_DIR first" >&2; exit 1; fi

echo "== packages"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-pip nginx openssl ufw

echo "== service account and directories"
getent group "$APP_GROUP" >/dev/null || groupadd --system "$APP_GROUP"
getent passwd "$APP_USER" >/dev/null || useradd --system --gid "$APP_GROUP" --home-dir "$APP_DATA_DIR" --shell /usr/sbin/nologin "$APP_USER"
install -d -o "$APP_USER" -g "$APP_GROUP" -m 0750 "$APP_DATA_DIR" "$APP_DATA_DIR/files"
install -d -o root -g "$APP_GROUP" -m 0750 "$APP_ENV_DIR"
if [[ ! -f "$APP_ENV_FILE" ]]; then
  install -o root -g "$APP_GROUP" -m 0640 "$APP_DIR/deploy/ubuntu/env.production.example" "$APP_ENV_FILE"
  echo "   wrote $APP_ENV_FILE from the example; edit it before starting"
fi

echo "== python virtualenv"
rm -rf "$APP_DIR/.venv"
"$PYTHON" -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/.venv/bin/pip" install --quiet "$APP_DIR"

# The service account only reads the code. root owns the checkout and the venv; the
# group (and nginx, for /static) may read and traverse. A compromised app process
# therefore cannot rewrite its own code. Earlier installs chowned this tree to the
# service account; re-running this script corrects that.
echo "== ownership"
chown -R "root:$APP_GROUP" "$APP_DIR"
chmod -R u+rwX,go+rX,go-w "$APP_DIR"

echo "== TLS certificate"
install -d -m 0755 "$TLS_DIR"
if [[ ! -f "$TLS_CERT_FILE" || ! -f "$TLS_KEY_FILE" ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
    -subj "/CN=$SERVER_NAME" -addext "subjectAltName=DNS:$SERVER_NAME" \
    -keyout "$TLS_KEY_FILE" -out "$TLS_CERT_FILE"
  chmod 0600 "$TLS_KEY_FILE"
  echo "   generated self-signed certificate for $SERVER_NAME"
fi

echo "== systemd"
sed -e "s|__APP_USER__|$APP_USER|g" -e "s|__APP_GROUP__|$APP_GROUP|g" -e "s|__APP_DIR__|$APP_DIR|g" \
    -e "s|__APP_ENV_FILE__|$APP_ENV_FILE|g" -e "s|__APP_DATA_DIR__|$APP_DATA_DIR|g" \
    "$APP_DIR/deploy/ubuntu/teamsync.service" > /etc/systemd/system/teamsync.service
systemctl daemon-reload
systemctl enable teamsync

echo "== nginx"
sed -e "s|__SERVER_NAME__|$SERVER_NAME|g" -e "s|__TLS_CERT_FILE__|$TLS_CERT_FILE|g" \
    -e "s|__TLS_KEY_FILE__|$TLS_KEY_FILE|g" -e "s|__APP_DIR__|$APP_DIR|g" -e "s|__APP_PORT__|$APP_PORT|g" \
    "$APP_DIR/deploy/ubuntu/nginx.conf" > /etc/nginx/sites-available/teamsync
ln -sf /etc/nginx/sites-available/teamsync /etc/nginx/sites-enabled/teamsync
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl enable --now nginx
systemctl reload nginx

echo "== firewall"
ufw allow OpenSSH >/dev/null || true
ufw allow 'Nginx Full' >/dev/null
ufw --force enable >/dev/null

cat <<EOF

Install complete.
  1. Edit $APP_ENV_FILE (set TEAMSYNC_ADMIN_PASSWORD or let one be generated).
  2. sudo systemctl start teamsync
  3. sudo journalctl -u teamsync -f
  4. curl -k https://$SERVER_NAME/health
If no admin password was set, read it from $APP_DATA_DIR/initial-admin-password.txt and delete that file.
EOF
