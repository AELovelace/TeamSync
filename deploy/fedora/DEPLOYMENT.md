# Fedora deployment

Installs TeamSync as:

- a `teamsync` service account
- the checkout at `/opt/teamsync` with a virtualenv in `/opt/teamsync/.venv`, both root-owned and read-only to the service account
- app data (SQLite database and raw files) under `/var/lib/teamsync`
- server-only settings in `/etc/teamsync/teamsync.env`
- a `systemd` service named `teamsync` running `teamsync serve` on `127.0.0.1:8000`
- `nginx` on 443 for `teamsync.eternalhotels.com` with a self-signed certificate

## Requirements

- Fedora with `sudo`
- local DNS resolving `teamsync.eternalhotels.com` to this host
- clients that trust the self-signed cert, or that accept the warning, until a Let's Encrypt cert replaces it

## First install

One line on a fresh Fedora host (clones `main` from GitHub into `/opt/teamsync`, runs this
bundle's `install-server.sh`, starts the service):

```bash
curl -fsSL https://raw.githubusercontent.com/AELovelace/TeamSync/main/deploy/install.sh | sudo bash
```

Options go in as environment variables, for example a chosen admin password and a
different hostname (note the password lands in shell history; alternatively edit
`/etc/teamsync/teamsync.env` afterwards and restart):

```bash
curl -fsSL https://raw.githubusercontent.com/AELovelace/TeamSync/main/deploy/install.sh \
  | sudo ADMIN_PASSWORD='long-secret' SERVER_NAME=teamsync.eternalhotels.com bash
```

`BRANCH=v0.2.0` deploys a tag, `START=0` installs without starting. Re-running the same
line fast-forwards the checkout and rebuilds the venv; config and data are kept.

Manual equivalent:

1. `git clone https://github.com/AELovelace/TeamSync.git /opt/teamsync`
2. Run:

```bash
sudo bash /opt/teamsync/deploy/fedora/install-server.sh
```

3. Edit `/etc/teamsync/teamsync.env`. Setting `TEAMSYNC_ADMIN_PASSWORD` is optional; without it a password is generated on first start and written to `/var/lib/teamsync/initial-admin-password.txt`. Delete that file after signing in.
4. Start and check:

```bash
sudo systemctl start teamsync
sudo journalctl -u teamsync -f
curl http://127.0.0.1:8000/health
curl -k https://teamsync.eternalhotels.com/health
```

5. Sign in as `admin`, open Admin, create a device token for the workstation, and change the admin password. Leave the token's scope at `pusher`: it can only check hashes and upload. An `admin` token can call every API route (entries, export, reparse) and is for scripts that read data. The token is shown once, in the page that answers the create request; it is never put in a URL.

## Workstation push

On the Box workstation, from a checkout of this repo:

```powershell
.\tools\Register-PushTask.ps1 -Root "C:\Users\Edith\Box\Singh JE Reports" `
    -Server https://teamsync.eternalhotels.com -Token tsk_... -IntervalMinutes 15 -Insecure
```

Drop `-Insecure` once the server has a trusted certificate, or pass `-CaBundle` with the self-signed cert exported as PEM. The task runs `tools/push_from_box.py`, which uploads only files whose SHA-256 the server has not seen. Log: `%LOCALAPPDATA%\TeamSync\push.log`.

The task is registered with S4U logon: it runs as your account whether or not you are signed in, and no password is stored. Box Drive only syncs while you are signed in, so a signed-out run can only push files that had already arrived. To re-register (new interval, or an older registration that used interactive logon) without retyping the token:

```powershell
.\tools\Register-PushTask.ps1 -IntervalMinutes 15
```

## Updating

```bash
sudo bash /opt/teamsync/deploy/fedora/update-app.sh
```

Pulls if the checkout is a git repo, reinstalls the package into the venv, restarts the service, and hits `/health`. Migrations run automatically at startup.

Installs made before 0.2.0 left `/opt/teamsync` owned by the service account. Run `sudo bash /opt/teamsync/deploy/fedora/install-server.sh` once more (it is safe to repeat) to move ownership to root and pick up the `ReadOnlyPaths` line in the unit.

## Swapping to Let's Encrypt

When the hostname is public and port 80 reaches this host:

```bash
sudo dnf -y install certbot python3-certbot-nginx
sudo certbot --nginx -d teamsync.eternalhotels.com
```

certbot rewrites the `ssl_certificate` lines in `/etc/nginx/conf.d/teamsync.conf` and installs a renewal timer. Nothing in the app changes. Then re-register the push task without `-Insecure`.

## Backups

Everything lives in `/var/lib/teamsync`: `teamsync.sqlite3` (plus `-wal` and `-shm`) and `files/`. Include that directory in the host backup. For a consistent database copy while the service runs:

```bash
sudo -u teamsync sqlite3 /var/lib/teamsync/teamsync.sqlite3 ".backup /tmp/teamsync-backup.sqlite3"
```

Raw files under `files/` are immutable once written, so a plain copy is safe.

## Troubleshooting

- `502` from nginx: the app is down. `journalctl -u teamsync -n 100`.
- `403` on `/static/`: SELinux context. `restorecon -R /opt/teamsync/teamsync/web/static`.
- Login loops back to the form behind the proxy: `TEAMSYNC_COOKIE_SECURE=true` requires HTTPS end to end; confirm nginx sets `X-Forwarded-Proto https` and you are not hitting port 8000 directly over plain HTTP.
- Push log says `HTTP 403`: the token is `pusher`-scoped and the script is trying something other than hash check or upload, or an `admin` route was called with it. Check the scope in Admin.
- Push log says `HTTP 414`: a pre-0.2.0 server (GET hash check) with more than about 120 files in the Box folder. Update the server; the 0.2.0 script POSTs.
- Push log says `HTTP 405` on the hash check: 0.2.0 script against a pre-0.2.0 server. The script falls back to short GETs; update the server anyway.
