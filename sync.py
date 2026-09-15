"""sync.py: upload of observations from the local database to the receiver on the server (server/ingest.py).

The server address and token are hard-coded (DEFAULT_SERVER, DEFAULT_TOKEN), so a friend
running the exe does not have to type anything. On first start `data/config.json` is created
with these values and a random, anonymous client id:
    {"server": "https://osmsfm.duckdns.org", "token": "<shared secret>", "client": "anon-3f9c2a"}
Whoever wants to sign with a nickname edits that file. Progress in `data/sync.json`: {"last_id": N},
i.e. up to which local id everything has already been sent.

Rows go in batches of BATCH, each with its crop as a base64 PNG (128-colour palette,
~16 KB). The server deduplicates on its own, so re-sending the same batch after a dropped
connection is safe. Nothing flows from the server down to the client: the local database stays
a local copy of what this client has seen.

    .venv\\Scripts\\python sync.py            # send everything that has not gone yet
    .venv\\Scripts\\python sync.py --setup    # ask for server, token and nickname, save the config
"""

from __future__ import annotations

import base64
import io
import json
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

from paths import RES_DIR
from store import DATA, DB, Store

CONFIG = DATA / "config.json"
STATE = DATA / "sync.json"
BATCH = 150
TIMEOUT = 120

# The shared token for the friends' trackers lives in `token.txt` next to the sources (outside git,
# the repo is public) and is built into the exe by build_exe.py. The same one sits in /etc/osmsfm/token
# on the server; to rotate it: delete that file, publish.py creates a new one, put it here, build the exe.
DEFAULT_SERVER = "https://osmsfm.duckdns.org"
_TOKEN_FILE = RES_DIR / "token.txt"
DEFAULT_TOKEN = _TOKEN_FILE.read_text(encoding="utf-8").strip() if _TOKEN_FILE.exists() else ""


class SyncError(RuntimeError):
    pass


def _default_client() -> str:
    """A random identifier, fixed per installation, instead of a nickname: users are to stay
    anonymous, and the server only needs to tell clients apart anyway (e.g. to cut off junk from
    one of them). The Windows user name was rejected because it is often a real first and last name."""
    return "anon-" + secrets.token_hex(3)


def load_config() -> dict:
    """Reads `data/config.json`; when it is missing or incomplete, fills in the missing fields
    from the defaults and saves it. Never asks."""
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
    if not c["token"]:
        raise SyncError("no upload token: put it in token.txt next to the sources (build) or in data/config.json")
    if changed:
        DATA.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    return c


def setup(defaults: dict | None = None) -> dict:
    """Manual change of settings (`sync.py --setup`); a normal start asks nothing."""
    d = defaults or load_config()
    print("Upload settings (Enter keeps the value in brackets).")
    server = input(f"  server address [{d['server']}]: ").strip() or d["server"]
    token = input(f"  token [{d['token']}]: ").strip() or d["token"]
    client = input(f"  client id or nickname [{d['client']}]: ").strip() or d["client"]
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
    from PIL import Image  # only here, store.py must stay free of Pillow

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
            raise SyncError("server rejected the token (401); check data/config.json") from None
        raise SyncError(f"server responded {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SyncError(f"cannot reach {cfg['server']}: {e}") from None


def push(store: Store, cfg: dict, log=print) -> tuple[int, int]:
    """Sends everything above last_id. Returns (sent, accepted as new)."""
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
        log(f"sent {sent} rows, server accepted {accepted} new (the rest were duplicates)")
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
