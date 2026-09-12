"""publish.py: eksport bazy do strony statycznej i wgranie jej na hosta (osmsfm.duckdns.org).

Strona publiczna nie ma Pythona ani bazy: to `index.html` (viewer w trybie statycznym),
`data.json` (zestawienie i obserwacje, liczone tu, w chwili publikacji) oraz `crops/`
(wycinki wierszy). Na serwerze chodzi tylko nginx, tak jak przy kalkulatorze.

    .venv\\Scripts\\python publish.py            # zbuduj data/public/ i wgraj na hosta
    .venv\\Scripts\\python publish.py --build    # tylko zbuduj, do obejrzenia lokalnie
    .venv\\Scripts\\python publish.py --nginx    # dodatkowo wgraj konfigurację nginksa

Wycinki jadą przyrostowo: skrypt pyta serwer, które już ma, i wysyła tylko brakujące
(jeden tar przez scp, rozpakowany po stronie serwera). Pierwsza publikacja to kilkadziesiąt
MB, każda następna tylko nowe wiersze.

Wymaga działającego `ssh rimhaven` i `scp` w PATH, jak deploy/deploy.ps1.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
PUBLIC = HERE / "data" / "public"
REMOTE = "rimhaven"
DOCS = "/var/www/osmsfm"
DOMAIN = "osmsfm.duckdns.org"
NGINX_CONF = HERE.parent / "deploy" / "osmsfm.nginx.conf"


def build(db_path: Path) -> dict:
    import tracker

    store = tracker.Store(db_path)
    items = store.items()
    obs = {it["name"]: store.obs(it["name"]) for it in items}
    for rows in obs.values():
        for o in rows:
            o.pop("conf", None)
    data = {"generated": datetime.now().isoformat(timespec="seconds"), "items": items, "obs": obs}
    PUBLIC.mkdir(parents=True, exist_ok=True)
    (PUBLIC / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    shutil.copy(HERE / "viewer.html", PUBLIC / "index.html")
    crops = PUBLIC / "crops"
    crops.mkdir(exist_ok=True)
    src = store.crops
    n = 0
    for rows in obs.values():
        for o in rows:
            p = src / f"{o['id']}.png"
            q = crops / p.name
            if p.exists() and not q.exists():
                # RGB PNG ma ~40 KB, paleta 128 kolorów ~8 KB; to UI z pixel artem, różnicy nie widać
                Image.open(p).convert("RGB").quantize(128, dither=Image.Dither.NONE).save(q, optimize=True)
                n += 1
    print(f"zbudowano data/public: {len(items)} przedmiotów, {sum(len(r) for r in obs.values())} obserwacji, {n} nowych wycinków")
    return data


def _run(args: list[str], capture: bool = False) -> str:
    r = subprocess.run(args, capture_output=capture, text=True)
    if r.returncode != 0:
        raise SystemExit(f"nie przeszło: {' '.join(args)}\n{r.stderr if capture else ''}")
    return r.stdout if capture else ""


def upload(nginx: bool) -> None:
    if nginx:
        print("konfiguracja nginksa...")
        _run(["scp", str(NGINX_CONF), f"{REMOTE}:/tmp/osmsfm.nginx.conf"])
        _run(["ssh", REMOTE, " && ".join([
            f"mkdir -p {DOCS}/crops",
            "mv /tmp/osmsfm.nginx.conf /etc/nginx/sites-available/osmsfm",
            "ln -sf /etc/nginx/sites-available/osmsfm /etc/nginx/sites-enabled/osmsfm",
            "nginx -t",
        ])])
    print("sprawdzam, które wycinki serwer już ma...")
    have = set(_run(["ssh", REMOTE, f"mkdir -p {DOCS}/crops && ls {DOCS}/crops"], capture=True).split())
    local = sorted(p for p in (PUBLIC / "crops").glob("*.png") if p.name not in have)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(PUBLIC / "index.html", arcname="index.html")
        tar.add(PUBLIC / "data.json", arcname="data.json")
        for p in local:
            tar.add(p, arcname=f"crops/{p.name}")
    tmp = PUBLIC / "upload.tar.gz"
    tmp.write_bytes(buf.getvalue())
    print(f"wysyłam {tmp.stat().st_size / 1e6:.1f} MB ({len(local)} nowych wycinków)...")
    _run(["scp", str(tmp), f"{REMOTE}:/tmp/osmsfm.tar.gz"])
    tmp.unlink()
    _run(["ssh", REMOTE, " && ".join([
        f"tar -xzf /tmp/osmsfm.tar.gz -C {DOCS}",
        "rm /tmp/osmsfm.tar.gz",
        f"chown -R www-data:www-data {DOCS}",
        f"find {DOCS} -type d -exec chmod 755 {{}} +",
        f"find {DOCS} -type f -exec chmod 644 {{}} +",
        "systemctl reload nginx",
    ])])
    code = _run(["ssh", REMOTE, f'curl -s -o /dev/null -w "%{{http_code}}" --resolve {DOMAIN}:80:127.0.0.1 http://{DOMAIN}/data.json'], capture=True).strip()
    print(f"serwer odpowiada {code} na http://{DOMAIN}/data.json (po HTTPS dopiero z certyfikatem)")


def main() -> int:
    build_only = "--build" in sys.argv
    db = HERE / "data" / "prices.sqlite"
    build(db)
    if not build_only:
        upload("--nginx" in sys.argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
