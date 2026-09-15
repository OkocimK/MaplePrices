"""sync.py: wysyłka obserwacji z lokalnej bazy do odbiornika na serwerze (server/ingest.py).

Adres serwera i token są wpisane na sztywno (DEFAULT_SERVER, DEFAULT_TOKEN), żeby znajomy
po uruchomieniu exe nie musiał nic wpisywać. Przy pierwszym starcie powstaje
`data/config.json` z tymi wartościami i nickiem z nazwy użytkownika Windows:
    {"server": "https://osmsfm.duckdns.org", "token": "<wspólny sekret>", "client": "<nick>"}
Kto chce inny nick, edytuje ten plik. Postęp w `data/sync.json`: {"last_id": N}, czyli do
którego lokalnego id wszystko już poszło.

Wiersze idą paczkami po BATCH, każdy z wycinkiem jako PNG w base64 (paleta 128 kolorów,
~16 KB). Serwer deduplikuje sam, więc ponowne wysłanie tej samej paczki po zerwanym
połączeniu jest bezpieczne. Nic nie schodzi z serwera na klienta: lokalna baza zostaje
lokalną kopią tego, co ten klient widział.

    .venv\\Scripts\\python sync.py            # wyślij wszystko, co jeszcze nie poszło
    .venv\\Scripts\\python sync.py --setup    # zapytaj o serwer, token i nick, zapisz config
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from store import DATA, DB, Store

CONFIG = DATA / "config.json"
STATE = DATA / "sync.json"
BATCH = 150
TIMEOUT = 120

# Wspólny token dla trackerów znajomych. Siedzi też w /etc/osmsfm/token na serwerze;
# nowy: usunąć tamten plik, publish.py założy inny, wpisać tutaj i zbudować exe od nowa.
DEFAULT_SERVER = "https://osmsfm.duckdns.org"
DEFAULT_TOKEN = "TOKEN-REMOVED"


class SyncError(RuntimeError):
    pass


def _default_client() -> str:
    for k in ("USERNAME", "USER"):
        v = os.environ.get(k, "").strip()
        if v:
            return v[:40]
    return "anon"


def load_config() -> dict:
    """Czyta `data/config.json`, a gdy go nie ma albo jest niepełny, dopisuje brakujące pola
    z wartości domyślnych i zapisuje. Nigdy nie pyta."""
    c: dict = {}
    if CONFIG.exists():
        try:
            c = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            c = {}
    changed = False
    for k, v in (("server", DEFAULT_SERVER), ("token", DEFAULT_TOKEN), ("client", None)):
        if not c.get(k):
            c[k] = v if v is not None else _default_client()
            changed = True
    c["server"] = c["server"].rstrip("/")
    if changed:
        DATA.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    return c


def setup(defaults: dict | None = None) -> dict:
    """Ręczna zmiana ustawień (`sync.py --setup`); zwykły start nie pyta o nic."""
    d = defaults or load_config()
    print("Ustawienia wysyłki na serwer (Enter zostawia wartość w nawiasie).")
    server = input(f"  adres serwera [{d['server']}]: ").strip() or d["server"]
    token = input(f"  token [{d['token']}]: ").strip() or d["token"]
    client = input(f"  nick (podpis danych) [{d['client']}]: ").strip() or d["client"]
    c = {"server": server.rstrip("/"), "token": token, "client": client}
    DATA.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    return c


def _last_id() -> int:
    try:
        return int(json.loads(STATE.read_text(encoding="utf-8")).get("last_id", 0))
    except (OSError, ValueError):
        return 0


def _save_last_id(n: int) -> None:
    STATE.write_text(json.dumps({"last_id": n}), encoding="utf-8")


def _crop_b64(path: Path) -> str | None:
    if not path.exists():
        return None
    from PIL import Image  # tylko tu, store.py ma zostać bez Pillow

    buf = io.BytesIO()
    Image.open(path).convert("RGB").quantize(128, dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _post(cfg: dict, payload: dict) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        cfg["server"] + "/api/ingest", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + cfg["token"],
                 "User-Agent": "osmsfm-tracker"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code == 401:
            raise SyncError("serwer odrzucił token (401); sprawdź data/config.json") from None
        raise SyncError(f"serwer odpowiedział {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SyncError(f"brak połączenia z {cfg['server']}: {e}") from None


def push(store: Store, cfg: dict, log=print) -> tuple[int, int]:
    """Wysyła wszystko powyżej last_id. Zwraca (wysłane, przyjęte jako nowe)."""
    last = _last_id()
    sent = accepted = 0
    while True:
        rows = store.rows_after(last, BATCH)
        if not rows:
            break
        payload_rows = []
        for r in rows:
            r = dict(r)
            oid = r.pop("id")
            r["client"] = cfg["client"]
            r["crop"] = _crop_b64(store.crops / f"{oid}.png")
            payload_rows.append(r)
        res = _post(cfg, {"client": cfg["client"], "rows": payload_rows})
        last = rows[-1]["id"]
        _save_last_id(last)
        sent += len(rows)
        accepted += int(res.get("accepted", 0))
    if sent:
        log(f"wysłano {sent} wierszy, serwer przyjął {accepted} nowych (reszta to duplikaty)")
    return sent, accepted


def main() -> int:
    if "--setup" in sys.argv:
        setup()
        return 0
    cfg = load_config()
    try:
        push(Store(DB), cfg)
    except SyncError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
