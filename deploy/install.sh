#!/usr/bin/env bash
# TeamSync one-shot installer. Clones (or updates) the repo from GitHub, runs the
# matching distro installer under deploy/<distro>/install-server.sh, starts the
# service and prints where the first admin password is.
#
# Fresh server:
#   curl -fsSL https://raw.githubusercontent.com/AELovelace/TeamSync/main/deploy/install.sh | sudo bash
#
# With options (environment variables, all optional):
#   curl -fsSL .../deploy/install.sh | sudo ADMIN_PASSWORD='long-secret' SERVER_NAME=teamsync.example.com bash
#
#   REPO_URL        git URL to clone          (default https://github.com/AELovelace/TeamSync.git)
#   BRANCH          branch or tag to deploy   (default main)
#   APP_DIR         checkout location         (default /opt/teamsync)
#   SERVER_NAME     nginx server_name / cert  (default teamsync.eternalhotels.com)
#   ADMIN_PASSWORD  first admin password; if unset one is generated on first start
#   START           1 to start the service when done, 0 to leave it stopped (default 1)
#   Any other variable the distro installer reads (APP_PORT, APP_DATA_DIR, TLS_*, ...) passes through.
#
# Re-running is safe: the checkout is fast-forwarded, the venv rebuilt, config and
# data under /etc/teamsync and /var/lib/teamsync are left alone.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/AELovelace/TeamSync.git}"
BRANCH="${BRANCH:-main}"
APP_DIR="${APP_DIR:-/opt/teamsync}"
APP_USER="${APP_USER:-teamsync}"
APP_GROUP="${APP_GROUP:-teamsync}"
APP_ENV_FILE="${APP_ENV_FILE:-/etc/teamsync/teamsync.env}"
APP_DATA_DIR="${APP_DATA_DIR:-/var/lib/teamsync}"
APP_PORT="${APP_PORT:-8000}"
SERVER_NAME="${SERVER_NAME:-teamsync.eternalhotels.com}"
START="${START:-1}"
export APP_DIR APP_USER APP_GROUP APP_ENV_FILE APP_DATA_DIR APP_PORT SERVER_NAME

die() { echo "error: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run with sudo"

# --- which distro bundle -----------------------------------------------------
. /etc/os-release 2>/dev/null || die "cannot read /etc/os-release"
case "${ID:-} ${ID_LIKE:-}" in
  *fedora*|*rhel*|*centos*) DISTRO=fedora; PKG="dnf -y install" ;;
  *ubuntu*|*debian*)        DISTRO=ubuntu; PKG="apt-get install -y -qq" ;;
  *) die "unsupported distro '${ID:-?}'; see deploy/ for Fedora and Ubuntu bundles" ;;
esac
echo "== $PRETTY_NAME -> deploy/$DISTRO"

# --- prerequisites for the bootstrap itself -----------------------------------
echo "== git and python"
[[ $DISTRO == ubuntu ]] && apt-get update -qq
$PKG git python3 ca-certificates >/dev/null
python3 - <<'PY' || die "python3 is older than 3.12. On Ubuntu use 24.04 or newer (or deadsnakes); on Fedora use 40 or newer."
import sys
sys.exit(0 if sys.version_info >= (3, 12) else 1)
PY

# --- checkout -----------------------------------------------------------------
echo "== checkout $REPO_URL ($BRANCH) -> $APP_DIR"
if [[ -d "$APP_DIR/.git" ]]; then
  # Run git as whoever owns the tree (root on current installs; older installs chowned
  # it to the service account, and install-server.sh below corrects that).
  owner="$(stat -c %U "$APP_DIR")"
  run_git() { if [[ $owner != root ]] && id "$owner" >/dev/null 2>&1; then sudo -u "$owner" git "$@"; else git "$@"; fi; }
  run_git -C "$APP_DIR" remote set-url origin "$REPO_URL"
  run_git -C "$APP_DIR" fetch --quiet --tags origin
  run_git -C "$APP_DIR" checkout --quiet "$BRANCH"
  run_git -C "$APP_DIR" merge --quiet --ff-only "origin/$BRANCH" 2>/dev/null || true   # tags have no remote branch
elif [[ -e "$APP_DIR" && -n "$(ls -A "$APP_DIR")" ]]; then
  die "$APP_DIR exists and is not a git checkout; move it aside or set APP_DIR"
else
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
fi
echo "   at $(git -C "$APP_DIR" describe --tags --always --dirty 2>/dev/null)"

# --- distro installer ----------------------------------------------------------
INSTALLER="$APP_DIR/deploy/$DISTRO/install-server.sh"
[[ -f "$INSTALLER" ]] || die "missing $INSTALLER"
bash "$INSTALLER"

# --- admin password ------------------------------------------------------------
if [[ -n "${ADMIN_PASSWORD:-}" ]]; then
  # Replace any existing (possibly commented) TEAMSYNC_ADMIN_PASSWORD line. Done with
  # grep + printf rather than sed so the password needs no escaping.
  tmp="$(mktemp)"
  grep -v '^#\?TEAMSYNC_ADMIN_PASSWORD=' "$APP_ENV_FILE" > "$tmp" || true
  printf 'TEAMSYNC_ADMIN_PASSWORD=%s\n' "$ADMIN_PASSWORD" >> "$tmp"
  install -o root -g "$APP_GROUP" -m 0640 "$tmp" "$APP_ENV_FILE"
  rm -f "$tmp"
  echo "== admin password written to $APP_ENV_FILE"
fi

# --- start ---------------------------------------------------------------------
if [[ $START == 1 ]]; then
  echo "== starting teamsync"
  systemctl restart teamsync
  for _ in $(seq 1 20); do
    if curl -fsS "http://127.0.0.1:$APP_PORT/health" >/dev/null 2>&1; then ok=1; break; fi
    sleep 1
  done
  if [[ -z ${ok:-} ]]; then
    systemctl --no-pager status teamsync | head -15 >&2 || true
    journalctl -u teamsync -n 40 --no-pager >&2 || true
    die "service did not answer on /health; see output above"
  fi
  echo "   healthy: $(curl -fsS "http://127.0.0.1:$APP_PORT/health")"
fi

cat <<EOT

TeamSync installed.
  URL:        https://$SERVER_NAME/   (self-signed cert until certbot replaces it)
  Service:    systemctl status teamsync      journalctl -u teamsync -f
  Settings:   $APP_ENV_FILE
  Data:       $APP_DATA_DIR
  Update:     sudo bash $APP_DIR/deploy/$DISTRO/update-app.sh
              (or re-run this script to switch BRANCH)
EOT
if [[ -z "${ADMIN_PASSWORD:-}" ]]; then
  if [[ -f "$APP_DATA_DIR/initial-admin-password.txt" ]]; then
    echo "  Admin:      user 'admin', password in $APP_DATA_DIR/initial-admin-password.txt (delete after signing in)"
  elif [[ $START == 1 ]]; then
    echo "  Admin:      existing users kept; no new password generated"
  else
    echo "  Admin:      a password is generated on first start into $APP_DATA_DIR/initial-admin-password.txt"
  fi
fi
echo "Next: sign in, Admin -> create a device token, then run tools/Register-PushTask.ps1 on the Box workstation."
