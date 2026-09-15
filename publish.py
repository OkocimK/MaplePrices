"""publish.py: wgranie serwerowej części pricetracka na hosta rimhaven (administrator, ssh).

Dane NIE jadą tędy: obserwacje wysyła każdy tracker sam przez HTTPS do odbiornika
(`server/ingest.py`, patrz `sync.py`). Ten skrypt wgrywa tylko kod i konfigurację:

    index.html  (kopia viewer.html)           -> /var/www/osmsfm/
    dist/osmsfm-tracker.zip (jeśli jest)      -> /var/www/osmsfm/download/   (link na stronie)
    store.py, server/ingest.py                -> /opt/osmsfm/
    server/osmsfm-ingest.service              -> /etc/systemd/system/
    server/osmsfm.nginx.conf  (z --nginx)     -> /etc/nginx/sites-available/osmsfm

Potem restartuje odbiornik i sprawdza /api/health. Token (`/etc/osmsfm/token`) zakłada
tylko wtedy, gdy go nie ma, i wypisuje go na końcu, żeby dało się rozdać znajomym.

    .venv\\Scripts\\python publish.py            # kod + unit + restart
    .venv\\Scripts\\python publish.py --nginx    # dodatkowo konfiguracja nginksa

Wymaga działającego `ssh rimhaven` i `scp` w PATH, jak deploy/deploy.ps1.
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
        raise SystemExit(f"nie przeszło: {' '.join(args)}\n{r.stderr.strip()}")
    return r.stdout


def ssh(*cmds: str) -> str:
    return run(["ssh", REMOTE, " && ".join(cmds)])


def main() -> int:
    nginx = "--nginx" in sys.argv
    print("pliki...")
    run(["scp", str(HERE / "viewer.html"), f"{REMOTE}:/tmp/osmsfm.index.html"])
    run(["scp", str(HERE / "store.py"), str(HERE / "paths.py"), str(HERE / "server" / "ingest.py"), f"{REMOTE}:/tmp/"])
    run(["scp", str(HERE / "server" / "osmsfm-ingest.service"), f"{REMOTE}:/tmp/"])
    zip_path = HERE / "dist" / "osmsfm-tracker.zip"
    if zip_path.exists():
        print(f"paczka trackera ({zip_path.stat().st_size / 1e6:.1f} MB)...")
        run(["scp", str(zip_path), f"{REMOTE}:/tmp/osmsfm-tracker.zip"])
    if nginx:
        run(["scp", str(NGINX_CONF), f"{REMOTE}:/tmp/osmsfm.nginx.conf"])
    print("instalacja na serwerze...")
    ssh(
        f"mkdir -p {WEB}/crops {WEB}/download {APP} /var/lib/osmsfm /etc/osmsfm",
        f"mv /tmp/osmsfm.index.html {WEB}/index.html",
        f"[ -f /tmp/osmsfm-tracker.zip ] && mv /tmp/osmsfm-tracker.zip {WEB}/download/osmsfm-tracker.zip || true",
        f"mv /tmp/store.py /tmp/paths.py /tmp/ingest.py {APP}/",
        "mv /tmp/osmsfm-ingest.service /etc/systemd/system/osmsfm-ingest.service",
        # token: tylko gdy go nie ma; www-data ma go czytać, nikt inny
        "[ -s /etc/osmsfm/token ] || (openssl rand -hex 16 > /etc/osmsfm/token)",
        "chown root:www-data /etc/osmsfm/token", "chmod 640 /etc/osmsfm/token",
        f"chown -R www-data:www-data {WEB} /var/lib/osmsfm",
        f"chmod 755 {WEB} {WEB}/crops {WEB}/download", f"chmod 644 {WEB}/index.html",
        f"chmod 644 {WEB}/download/* 2>/dev/null || true",
        "systemctl daemon-reload",
        "systemctl enable --now osmsfm-ingest.service",
        "systemctl restart osmsfm-ingest.service",
        *(["mv /tmp/osmsfm.nginx.conf /etc/nginx/sites-available/osmsfm", "nginx -t", "systemctl reload nginx"] if nginx else []),
    )
    print("sprawdzenie...")
    out = ssh("sleep 1", f'curl -s --resolve {DOMAIN}:443:127.0.0.1 https://{DOMAIN}/api/health',
              "echo", "systemctl is-active osmsfm-ingest.service",
              "cat /etc/osmsfm/token")
    lines = out.strip().splitlines()
    print("  /api/health:", lines[0] if lines else "?")
    print("  usługa:", lines[1] if len(lines) > 1 else "?")
    print("  token dla trackerów:", lines[2] if len(lines) > 2 else "?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
