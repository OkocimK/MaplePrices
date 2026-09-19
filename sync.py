"""sync.py: upload of observations from the local database to the receiver on the server (server/ingest.py).

Nobody types anything in and nothing secret is built into the exe: before the first upload the
tracker registers with the server (POST /api/register) and gets its own key, which lands in
`data/config.json` next to the server address and the anonymous client id:
    {"server": "https://osmsfm.duckdns.org", "client": "anon-3f9c2a", "key": "<this tracker's key>"}
The server signs the rows with the label tied to the key, so it can cut off one client without
touching the others. The label is the proposed `client` when nobody else uses it, otherwise the
server picks one and the config is updated. To sign with a nickname: set `client` and delete `key`,
the next upload registers again. A config from the shared-token days (field `token`) registers
with that token once, which lets it keep its old label, and then drops it.
Progress in `data/sync.json`: {"last_id": N}, i.e. up to which local id everything has already been sent.

Rows go in batches of BATCH, each with its crop as a base64 PNG (128-colour palette,
~16 KB). The server deduplicates on its own, so re-sending the same batch after a dropped
connection is safe. Nothing flows from the server down to the client: the local database stays
a local copy of what this client has seen.

    .venv\\Scripts\\python sync.py            # send everything that has not gone yet
    .venv\\Scripts\\python sync.py --setup    # ask for server and nickname, save the config
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

from store import DATA, DB, Store

CONFIG = DATA / "config.json"
STATE = DATA / "sync.json"
BATCH = 150
TIMEOUT = 120

DEFAULT_SERVER = "https://osmsfm.duckdns.org"


class SyncError(RuntimeError):
    pass


def _default_client() -> str:
    """A random identifier, fixed per installation, instead of a nickname: users are to stay
    anonymous, and the server only needs to tell clients apart anyway (e.g. to cut off junk from
    one of them). The Windows user name was rejected because it is often a real first and last name.
    It is only a proposal: the label that counts is the one the server ties to the key."""
    return "anon-" + secrets.token_hex(3)


def load_config() -> dict:
    """Reads `data/config.json`; when it is missing or incomplete, fills in the missing fields
    from the defaults and saves it. Never asks and never touches the network: the key is fetched
    by `ensure_key` right before the first upload, so the tracker still starts offline."""
    c: dict = {}
    if CONFIG.exists():
        try:
            c = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            c = {}
    changed = False
    for k, v in (("server", DEFAULT_SERVER), ("client", None)):
        if not c.get(k):
            c[k] = v if v is not None else _default_client()
            changed = True
    c["server"] = c["server"].rstrip("/")
    if changed:
        _save_config(c)
    return c


def _save_config(c: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_key(cfg: dict, log=print) -> None:
    """Registers with the server when the config has no key yet. Updates `cfg` in place (the
    tracker's uploader holds the same dict) and saves it."""
    if cfg.get("key"):
        return
    res = _request(cfg, "/api/register", {"client": cfg.get("client")}, bearer=cfg.get("token"))
    if not res.get("key") or not res.get("client"):
        raise SyncError("server did not hand out a key")
    cfg["key"], cfg["client"] = res["key"], res["client"]
    cfg.pop("token", None)  # the shared token has done its last job: proving the old label is ours
    _save_config(cfg)
    log(f"registered with the server as {cfg['client']}")


def setup(defaults: dict | None = None) -> dict:
    """Manual change of settings (`sync.py --setup`); a normal start asks nothing."""
    d = defaults or load_config()
    print("Upload settings (Enter keeps the value in brackets).")
    server = input(f"  server address [{d['server']}]: ").strip() or d["server"]
    client = input(f"  client id or nickname [{d['client']}]: ").strip() or d["client"]
    c = dict(d, server=server.rstrip("/"), client=client)
    if (c["server"], c["client"]) != (d["server"], d["client"]):
        c.pop("key", None)  # another server or another name: register again on the next upload
    _save_config(c)
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


class _Unauthorized(SyncError):
    pass


def _request(cfg: dict, path: str, payload: dict, bearer: str | None) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "osmsfm-tracker"}
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(cfg["server"] + path, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code == 401:
            raise _Unauthorized("server does not know this tracker's key (401)") from None
        if e.code == 403:
            raise SyncError("the server has blocked uploads from this tracker") from None
        if e.code == 429:
            raise SyncError("the server is refusing for now (too many requests), it will be retried later") from None
        raise SyncError(f"server responded {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise SyncError(f"cannot reach {cfg['server']}: {e}") from None


def _post(cfg: dict, payload: dict, log=print) -> dict:
    ensure_key(cfg, log)
    try:
        return _request(cfg, "/api/ingest", payload, bearer=cfg["key"])
    except _Unauthorized:  # the server lost its client list (rebuilt database): one fresh registration
        cfg.pop("key", None)
        ensure_key(cfg, log)
        return _request(cfg, "/api/ingest", payload, bearer=cfg["key"])


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
            r["crop"] = _crop_b64(store.crops / f"{oid}.png")
            payload_rows.append(r)
        res = _post(cfg, {"rows": payload_rows}, log)
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
