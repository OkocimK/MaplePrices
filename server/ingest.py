"""ingest.py: observation receiver on the host (osmsfm.duckdns.org), standard library only.

Listens on 127.0.0.1:8781, nginx forwards `/api/` there. The friends' trackers send
POST /api/ingest with the header `Authorization: Bearer <token>` and JSON:
    {"client": "nick", "rows": [{...fields from store.ROW_FIELDS..., "crop": "<png base64>"}]}
The server deduplicates (store.Store.add_rows), writes the crops into the site directory and after each
batch refreshes `data.json`, which the viewer reads from. Response: {"accepted": n, "dup": m}.

GET /api/health returns the row count, to check that it is alive.

Paths (environment variables, set by the systemd unit):
    OSMSFM_TOKEN_FILE  /etc/osmsfm/token        shared secret, one line
    OSMSFM_DB          /var/lib/osmsfm/prices.sqlite
    OSMSFM_WEB         /var/www/osmsfm          index.html, data.json, crops/
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import ROW_FIELDS, Store  # noqa: E402

TOKEN = Path(os.environ.get("OSMSFM_TOKEN_FILE", "/etc/osmsfm/token")).read_text(encoding="utf-8").strip()
DB = Path(os.environ.get("OSMSFM_DB", "/var/lib/osmsfm/prices.sqlite"))
WEB = Path(os.environ.get("OSMSFM_WEB", "/var/www/osmsfm"))
BIND = ("127.0.0.1", int(os.environ.get("OSMSFM_PORT", "8781")))
MAX_BODY = 48 * 1024 * 1024
MAX_ROWS = 500

store = Store(DB, crops=WEB / "crops")
export_lock = threading.Lock()


def export() -> None:
    data = store.export()
    tmp = WEB / "data.json.tmp"
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

    def _authed(self) -> bool:
        return self.headers.get("Authorization", "") == "Bearer " + TOKEN

    def do_GET(self):
        if self.path == "/api/health":
            n = store.db.execute("SELECT count(*) FROM obs").fetchone()[0]
            self._json({"ok": True, "rows": n})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/api/ingest":
            return self._json({"error": "not found"}, 404)
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > MAX_BODY:
            return self._json({"error": "bad length"}, 413)
        try:
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
            client = str(payload.get("client", ""))[:40] or "?"
            raws = payload["rows"]
            if not isinstance(raws, list) or len(raws) > MAX_ROWS:
                raise ValueError("rows")
        except (ValueError, KeyError, UnicodeDecodeError):
            return self._json({"error": "bad json"}, 400)
        rows, crops = [], []
        for raw in raws:
            row = clean_row(raw, client)
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
