"""clients.py: per-client upload keys on the server (standard library only).

Every tracker registers once (POST /api/register) and gets its own random key; the server keeps
only the SHA-256 of it. The key decides the `client` label of the uploaded rows, whatever the
payload claims, so one client's rows can be told apart, that client can be banned and its rows
purged (server/admin.py) without touching anybody else. There is nothing secret inside the exe
any more.

This is attribution and revocation, not identity: a banned person can register again. What
slows that down is the cap of REG_PER_IP_DAY registrations per address per day and the ban of
the address itself (`ban --ip`). Addresses are never stored, only a salted hash, and the salt
lives in a file next to the database.

Labels: the client may propose one (its old anonymous id or a nickname). It is accepted when
nobody uses it, neither in `clients` nor in rows uploaded in the shared-token days; a tracker
that still holds the shared token proves it owns its old label and keeps it. Otherwise the
server hands out a fresh `anon-xxxxxx`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

REG_PER_IP_DAY = 5
LABEL_RE = re.compile(r"^[A-Za-z0-9_.-]{3,24}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    key_hash  TEXT PRIMARY KEY,
    label     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created   TEXT NOT NULL,
    last_seen TEXT,
    ip_hash   TEXT,
    banned    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS banned_ips (ip_hash TEXT PRIMARY KEY, since TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS obs_client ON obs(client);
"""


class RegisterRefused(Exception):
    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class Clients:
    """Works on the Store's connection and lock: same database file, one writer at a time."""

    def __init__(self, db: sqlite3.Connection, lock: threading.Lock, salt_file: Path):
        self.db = db
        self.lock = lock
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()
        if not salt_file.exists():
            salt_file.write_text(secrets.token_hex(16), encoding="utf-8")
            salt_file.chmod(0o600)
        self.salt = salt_file.read_text(encoding="utf-8").strip().encode("utf-8")

    def ip_hash(self, ip: str) -> str:
        return hmac.new(self.salt, ip.encode("utf-8"), hashlib.sha256).hexdigest()[:16]

    def _label_free(self, label: str, owns_legacy: bool) -> bool:
        if self.db.execute("SELECT 1 FROM clients WHERE label=?", (label,)).fetchone():
            return False
        if owns_legacy:
            return True
        return not self.db.execute("SELECT 1 FROM obs WHERE client=? COLLATE NOCASE LIMIT 1", (label,)).fetchone()

    def register(self, proposed: str | None, ip: str, owns_legacy: bool = False) -> tuple[str, str]:
        """Returns (key, label). `owns_legacy`: the request carried the old shared token."""
        iph = self.ip_hash(ip)
        with self.lock:
            if self.db.execute("SELECT 1 FROM banned_ips WHERE ip_hash=?", (iph,)).fetchone():
                raise RegisterRefused(403, "banned")
            since = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
            n = self.db.execute("SELECT count(*) FROM clients WHERE ip_hash=? AND created>?", (iph, since)).fetchone()[0]
            if n >= REG_PER_IP_DAY:
                raise RegisterRefused(429, "too many registrations")
            label = proposed if proposed and LABEL_RE.match(proposed) and self._label_free(proposed, owns_legacy) else None
            while label is None:
                candidate = "anon-" + secrets.token_hex(3)
                if self._label_free(candidate, False):
                    label = candidate
            key = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO clients(key_hash,label,created,ip_hash) VALUES (?,?,?,?)",
                            (_hash_key(key), label, _now(), iph))
            self.db.commit()
        return key, label

    def auth(self, key: str) -> tuple[str | None, bool]:
        """(label, banned) for a known key, (None, False) for an unknown one."""
        with self.lock:
            row = self.db.execute("SELECT label, banned FROM clients WHERE key_hash=?", (_hash_key(key),)).fetchone()
            if row is None:
                return None, False
            if not row[1]:
                self.db.execute("UPDATE clients SET last_seen=? WHERE key_hash=?", (_now(), _hash_key(key)))
                self.db.commit()
            return row[0], bool(row[1])

    def is_registered(self, label: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM clients WHERE label=?", (label,)).fetchone() is not None

    # --- administration (server/admin.py) ---

    def listing(self) -> list[dict]:
        with self.lock:
            counts = dict(self.db.execute("SELECT client, count(*) FROM obs GROUP BY client").fetchall())
            banned_ips = {r[0] for r in self.db.execute("SELECT ip_hash FROM banned_ips")}
            rows = self.db.execute("SELECT label, created, last_seen, ip_hash, banned FROM clients ORDER BY created").fetchall()
        known = set()
        out = []
        for label, created, last_seen, iph, banned in rows:
            known.add(label.lower())
            out.append({"label": label, "created": created, "last_seen": last_seen, "ip_hash": iph,
                        "banned": bool(banned), "ip_banned": iph in banned_ips, "rows": counts.get(label, 0), "key": True})
        for label, n in counts.items():  # rows from the shared-token days, nobody holds a key for them
            if (label or "").lower() not in known:
                out.append({"label": label, "created": None, "last_seen": None, "ip_hash": None,
                            "banned": False, "ip_banned": False, "rows": n, "key": False})
        return out

    def set_banned(self, label: str, banned: bool, with_ip: bool = False) -> bool:
        with self.lock:
            row = self.db.execute("SELECT ip_hash FROM clients WHERE label=?", (label,)).fetchone()
            if row is None:
                return False
            self.db.execute("UPDATE clients SET banned=? WHERE label=?", (int(banned), label))
            if with_ip and row[0]:
                if banned:
                    self.db.execute("INSERT OR IGNORE INTO banned_ips(ip_hash, since) VALUES (?,?)", (row[0], _now()))
                    self.db.execute("UPDATE clients SET banned=1 WHERE ip_hash=?", (row[0],))
                else:
                    self.db.execute("DELETE FROM banned_ips WHERE ip_hash=?", (row[0],))
            self.db.commit()
        return True

    def purge(self, label: str, crops: Path) -> int:
        """Deletes every row uploaded under this label, with the crops. Returns how many."""
        with self.lock:
            ids = [r[0] for r in self.db.execute("SELECT id FROM obs WHERE client=? COLLATE NOCASE", (label,))]
            self.db.execute("DELETE FROM obs WHERE client=? COLLATE NOCASE", (label,))
            self.db.commit()
        for oid in ids:
            p = crops / f"{oid}.png"
            if p.exists():
                p.unlink()
        return len(ids)
