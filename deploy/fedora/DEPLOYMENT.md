# Fedora deployment

Installs TeamSync as:

- a `teamsync` service account
- the checkout at `/opt/teamsync` with a virtualenv in `/opt/teamsync/.venv`
- app data (SQLite database and raw files) under `/var/lib/teamsync`
- server-only settings in `/etc/teamsync/teamsync.env`
- a `systemd` service named `teamsync` running `teamsync serve` on `127.0.0.1:8000`
- `nginx` on 443 for `teamsync.eternalhotels.com` with a self-signed certificate

## Requirements

- Fedora with `sudo`
- local DNS resolving `teamsync.eternalhotels.com` to this host
- clients that trust the self-signed cert, or that accept the warning, until a Let's Encrypt cert replaces it

## First install

1. Copy the repo to `/opt/teamsync` (WinSCP or `git clone`). `.venv`, `storage` and `example-data` are not needed.
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

5. Sign in as `admin`, open Admin, create a device token for the workstation, and change the admin password.

## Workstation push

On the Box workstation, from a checkout of this repo:

```powershell
.\tools\Register-PushTask.ps1 -Root "C:\Users\Edith\Box\Singh JE Reports" `
    -Server https://teamsync.eternalhotels.com -Token tsk_... -IntervalMinutes 15 -Insecure
```

Drop `-Insecure` once the server has a trusted certificate, or pass `-CaBundle` with the self-signed cert exported as PEM. The task runs `tools/push_from_box.py`, which uploads only files whose SHA-256 the server has not seen. Log: `%LOCALAPPDATA%\TeamSync\push.log`.

## Updating

```bash
sudo bash /opt/teamsync/deploy/fedora/update-app.sh
```

Pulls if the checkout is a git repo, reinstalls the package into the venv, restarts the service, and hits `/health`. Migrations run automatically at startup.

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
