# Ubuntu deployment

Same layout as the Fedora bundle (see [../fedora/DEPLOYMENT.md](../fedora/DEPLOYMENT.md) for the full walkthrough, workstation push setup, Let's Encrypt swap and backups). Differences:

- packages via `apt-get`, firewall via `ufw`
- nginx site at `/etc/nginx/sites-available/teamsync`, enabled by symlink, default site removed
- TLS files under `/etc/ssl/teamsync/`
- no SELinux steps

This bundle is written alongside the Fedora one but is tested second.

```bash
sudo bash /opt/teamsync/deploy/ubuntu/install-server.sh
sudo systemctl start teamsync
curl -k https://teamsync.eternalhotels.com/health
```

Update with `sudo bash /opt/teamsync/deploy/ubuntu/update-app.sh`.

Let's Encrypt: `sudo apt-get install certbot python3-certbot-nginx && sudo certbot --nginx -d teamsync.eternalhotels.com`.
