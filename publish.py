"""publish.py: upload of the server-side part of pricetrack to the rimhaven host (administrator, ssh).

Data does NOT travel this way: every tracker sends its observations itself over HTTPS to the
receiver (`server/ingest.py`, see `sync.py`). This script uploads only code and configuration:

    index.html  (copy of viewer.html)          -> /var/www/osmsfm/
    dist/osmsfm-tracker.zip (if present)       -> /var/www/osmsfm/download/   (link on the page)
    store.py, server/{ingest,clients,admin}.py -> /opt/osmsfm/
    server/osmsfm-ingest.service               -> /etc/systemd/system/
    server/osmsfm.nginx.conf  (with --nginx)   -> /etc/nginx/sites-available/osmsfm

Then it restarts the receiver and checks /api/health. Trackers get their upload keys from the
receiver itself (server/clients.py); moderation is `ssh rimhaven python3 /opt/osmsfm/admin.py list`.
The old shared token (`/etc/osmsfm/token`) is left alone: while the file exists, trackers built
before per-client keys are still accepted; delete it and restart the service to end that.

    .venv\\Scripts\\python publish.py            # code + unit + restart
    .venv\\Scripts\\python publish.py --nginx    # additionally the nginx configuration

Requires a working `ssh rimhaven` and `scp` in PATH, like deploy/deploy.ps1.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REMOTE = "rimhaven"
DOMAIN = "osmsfm.duckdns.org"
WEB = "/var/www/osmsfm"
APP = "/opt/osmsfm"
NGINX_CONF = HERE / "server" / "osmsfm.nginx.conf"


def run(args: list[str]) -> str:
    r = subprocess.run(args, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=600)
    if r.returncode != 0:
        raise SystemExit(f"failed: {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def ssh(*cmds: str) -> str:
    return run(["ssh", REMOTE, " && ".join(cmds)])


def main() -> int:
    nginx = "--nginx" in sys.argv
    print("files...")
    run(["scp", str(HERE / "viewer.html"), f"{REMOTE}:/tmp/osmsfm.index.html"])
    server_py = [str(HERE / "server" / f) for f in ("ingest.py", "clients.py", "admin.py")]
    run(["scp", str(HERE / "store.py"), str(HERE / "paths.py"), *server_py, f"{REMOTE}:/tmp/"])
    run(["scp", str(HERE / "server" / "osmsfm-ingest.service"), f"{REMOTE}:/tmp/"])
    zip_path = HERE / "dist" / "osmsfm-tracker.zip"
    if zip_path.exists():
        print(f"tracker package ({zip_path.stat().st_size / 1e6:.1f} MB)...")
        run(["scp", str(zip_path), f"{REMOTE}:/tmp/osmsfm-tracker.zip"])
    if nginx:
        run(["scp", str(NGINX_CONF), f"{REMOTE}:/tmp/osmsfm.nginx.conf"])
    print("installing on the server...")
    ssh(
        f"mkdir -p {WEB}/crops {WEB}/download {APP} /var/lib/osmsfm /etc/osmsfm",
        f"mv /tmp/osmsfm.index.html {WEB}/index.html",
        f"[ -f /tmp/osmsfm-tracker.zip ] && mv /tmp/osmsfm-tracker.zip {WEB}/download/osmsfm-tracker.zip || true",
        f"mv /tmp/store.py /tmp/paths.py /tmp/ingest.py /tmp/clients.py /tmp/admin.py {APP}/",
        "mv /tmp/osmsfm-ingest.service /etc/systemd/system/osmsfm-ingest.service",
        # old shared token, when still there: www-data must be able to read it, nobody else
        "[ ! -f /etc/osmsfm/token ] || (chown root:www-data /etc/osmsfm/token && chmod 640 /etc/osmsfm/token)",
        f"chown -R www-data:www-data {WEB} /var/lib/osmsfm",
        f"chmod 755 {WEB} {WEB}/crops {WEB}/download", f"chmod 644 {WEB}/index.html",
        f"chmod 644 {WEB}/download/* 2>/dev/null || true",
        "systemctl daemon-reload",
        "systemctl enable --now osmsfm-ingest.service",
        "systemctl restart osmsfm-ingest.service",
        *(["mv /tmp/osmsfm.nginx.conf /etc/nginx/sites-available/osmsfm", "nginx -t", "systemctl reload nginx"] if nginx else []),
    )
    print("checking...")
    out = ssh("sleep 1", f'curl -s --resolve {DOMAIN}:443:127.0.0.1 https://{DOMAIN}/api/health',
              "echo", "systemctl is-active osmsfm-ingest.service",
              "( [ -s /etc/osmsfm/token ] && echo still accepted || echo refused )")
    lines = out.strip().splitlines()
    print("  /api/health:", lines[0] if lines else "?")
    print("  service:", lines[1] if len(lines) > 1 else "?")
    print("  trackers with the old shared token:", lines[2] if len(lines) > 2 else "?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
