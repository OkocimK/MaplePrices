"""ingest.py: observation receiver on the host (osmsfm.duckdns.org), standard library only.

Listens on 127.0.0.1:8781, nginx forwards `/api/` there.

POST /api/register  {"client": "<proposed label, optional>"}  ->  {"key": "...", "client": "<label>"}
    Every tracker calls it once and keeps the key (see clients.py). No secret is needed to register.
POST /api/ingest with the header `Authorization: Bearer <key>` and JSON:
    {"rows": [{...fields from store.ROW_FIELDS..., "crop": "<png base64>"}]}
    The rows are signed with the label that belongs to the key; a `client` in the payload is ignored.
    401 = unknown key (the tracker registers again), 403 = banned.
The server deduplicates (store.Store.add_rows), writes the crops into the site directory and after each
batch refreshes `data.json`, which the viewer reads from. Response: {"accepted": n, "dup": m}.

Trackers built before per-client keys send one shared token instead and name themselves in the
payload. They are accepted for as long as OSMSFM_TOKEN_FILE exists; delete the file and restart
the service to switch that off.

GET /api/health returns the row count, to check that it is alive.

Paths (environment variables, set by the systemd unit):
    OSMSFM_TOKEN_FILE  /etc/osmsfm/token        old shared token, one line; missing = old trackers refused
    OSMSFM_DB          /var/lib/osmsfm/prices.sqlite
    OSMSFM_WEB         /var/www/osmsfm          index.html, data.json, crops/
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clients import Clients, RegisterRefused  # noqa: E402
from store import ROW_FIELDS, Store  # noqa: E402

_TOKEN_FILE = Path(os.environ.get("OSMSFM_TOKEN_FILE", "/etc/osmsfm/token"))
LEGACY_TOKEN = _TOKEN_FILE.read_text(encoding="utf-8").strip() if _TOKEN_FILE.exists() else ""
DB = Path(os.environ.get("OSMSFM_DB", "/var/lib/osmsfm/prices.sqlite"))
WEB = Path(os.environ.get("OSMSFM_WEB", "/var/www/osmsfm"))
BIND = ("127.0.0.1", int(os.environ.get("OSMSFM_PORT", "8781")))
MAX_BODY = 48 * 1024 * 1024
MAX_ROWS = 500

store = Store(DB, crops=WEB / "crops")
clients = Clients(store.db, store.lock, DB.with_name("ip_salt"))
export_lock = threading.Lock()


def export() -> None:
    data = store.export()
    tmp = WEB / f"data.json.{os.getpid()}.tmp"  # admin.py exports from its own process
    with export_lock:
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(WEB / "data.json")  # atomic swap, nginx never serves half a file


def clean_row(raw: dict, client: str) -> dict | None:
    """Only known fields, with strict type validation; junk from the network must not enter the database."""
    try:
        row = {k: raw.get(k) for k in ROW_FIELDS}
        row["client"] = client[:40]
        if not isinstance(row["name"], str) or not row["name"] or len(row["name"]) > 120:
            return None
        row["price"] = int(row["price"])
        if row["price"] <= 0 or row["price"] > 10**12:
            return None
        row["sold"] = int(bool(row["sold"]))
        row["scroll"] = int(bool(row["scroll"]))
        row["complete"] = int(bool(row["complete"]))
        row["dark"] = None if row["dark"] is None else int(bool(row["dark"]))
        row["pct"] = None if row["pct"] is None else int(row["pct"])
        row["channel"] = None if row["channel"] is None else int(row["channel"])
        row["conf"] = None if row["conf"] is None else float(row["conf"])
        for k in ("ts", "expires"):
            if row[k] is not None:
                from datetime import datetime

                datetime.fromisoformat(row[k])  # ValueError when it is not a date
        if row["ts"] is None:
            return None
        for k in ("raw_name", "equip", "stat", "owner", "shop", "map"):
            if row[k] is not None:
                row[k] = str(row[k])[:200]
        return row
    except (TypeError, ValueError):
        return None


class H(BaseHTTPRequestHandler):
    server_version = "osmsfm-ingest/1"

    def log_message(self, fmt, *args):  # only errors go to the journal, successes are counted by the response
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bearer(self) -> str:
        auth = self.headers.get("Authorization", "")
        return auth[7:] if auth.startswith("Bearer ") else ""

    def _legacy(self, bearer: str) -> bool:
        return bool(LEGACY_TOKEN) and hmac.compare_digest(bearer.encode("utf-8"), LEGACY_TOKEN.encode("utf-8"))

    def _ip(self) -> str:
        """nginx is the only thing that can reach this port, so its header is the real address."""
        return self.headers.get("X-Real-IP") or self.client_address[0]

    def _body(self, limit: int) -> dict | None:
        """Parsed JSON object, or None after an error response has been sent."""
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n < 0 or n > limit:
            self._json({"error": "bad length"}, 413)
            return None
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if not isinstance(payload, dict):
                raise ValueError("not an object")
        except (ValueError, UnicodeDecodeError):
            self._json({"error": "bad json"}, 400)
            return None
        return payload

    def _register(self):
        payload = self._body(4096)
        if payload is None:
            return
        proposed = payload.get("client")
        try:
            key, label = clients.register(proposed if isinstance(proposed, str) else None, self._ip(),
                                          owns_legacy=self._legacy(self._bearer()))
        except RegisterRefused as e:
            return self._json({"error": e.reason}, e.code)
        print(f"registered {label}", flush=True)
        self._json({"key": key, "client": label})

    def do_GET(self):
        if self.path == "/api/health":
            n = store.db.execute("SELECT count(*) FROM obs").fetchone()[0]
            self._json({"ok": True, "rows": n})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/register":
            return self._register()
        if self.path != "/api/ingest":
            return self._json({"error": "not found"}, 404)
        bearer = self._bearer()
        legacy = self._legacy(bearer)
        label = None
        if not legacy:
            label, banned = clients.auth(bearer) if bearer else (None, False)
            if banned:
                return self._json({"error": "banned"}, 403)
            if label is None:
                return self._json({"error": "unauthorized"}, 401)
        payload = self._body(MAX_BODY)
        if payload is None:
            return
        raws = payload.get("rows")
        if not isinstance(raws, list) or len(raws) > MAX_ROWS:
            return self._json({"error": "bad json"}, 400)
        if label is not None:
            client = label  # the key names its owner, whatever the payload says
        else:  # old trackers name themselves, but must not write under a label that now has a key
            client = str(payload.get("client", ""))[:40] or "?"
            if clients.is_registered(client):
                client = "legacy:" + client[:33]
        rows, crops = [], []
        for raw in raws:
            row = clean_row(raw, client) if isinstance(raw, dict) else None
            if row is None:
                continue
            rows.append(row)
            crops.append(raw.get("crop"))
        ids = store.add_rows(rows)
        accepted = 0
        for oid, crop in zip(ids, crops):
            if oid is None:
                continue
            accepted += 1
            if isinstance(crop, str) and len(crop) < 400_000:
                try:
                    png = base64.b64decode(crop, validate=True)
                    if png[:8] == b"\x89PNG\r\n\x1a\n":
                        (store.crops / f"{oid}.png").write_bytes(png)
                except ValueError:
                    pass
        if accepted:
            export()
        print(f"{client}: {len(rows)} rows, {accepted} new", flush=True)
        self._json({"accepted": accepted, "dup": len(rows) - accepted, "rejected": len(raws) - len(rows)})


if __name__ == "__main__":
    if not (WEB / "data.json").exists():
        export()
    print(f"osmsfm-ingest on {BIND[0]}:{BIND[1]}, database {DB}, site {WEB}", flush=True)
    ThreadingHTTPServer(BIND, H).serve_forever()
