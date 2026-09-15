"""store.py: observation database (SQLite) and the per-item summary.

Shared by the tracker (client, Windows) and the receiver on the server (`server/ingest.py`),
hence **standard library only**: no Pillow, numpy or winocr. Row crops (PNG) are written by
whoever has the image: the tracker in `add`, the server in `add_rows`.

Schema: one table `obs`, row = one observed offer. `expires` is the time the shop disappears
according to its timer, `client` is the collector's nickname (assigned by the server from the
token... no, from the payload, the token is shared; the nickname serves attribution and cutting
out junk from one client).
"""

from __future__ import annotations

import sqlite3
import statistics
import threading
from datetime import datetime, timedelta
from pathlib import Path

try:
    from paths import DATA_DIR as DATA  # client: next to the exe or the sources
except ImportError:  # the server only has store.py and ingest.py, it gets the paths from env
    DATA = Path(__file__).resolve().parent / "data"

DB = DATA / "prices.sqlite"  # crops land next to it, in data/prices_crops/
DEDUP_MINUTES = 10
DEFAULT_TTL_H = 24  # when the shop timer was unreadable: the offer counts as current for this many hours

SCHEMA = """
CREATE TABLE IF NOT EXISTS obs (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  name TEXT NOT NULL,
  raw_name TEXT,
  scroll INTEGER NOT NULL,
  complete INTEGER NOT NULL,
  dark INTEGER, equip TEXT, stat TEXT, pct INTEGER,
  price INTEGER NOT NULL,
  sold INTEGER NOT NULL,
  owner TEXT, shop TEXT, map TEXT, channel INTEGER,
  conf REAL,
  expires TEXT,
  client TEXT
);
CREATE INDEX IF NOT EXISTS obs_name ON obs(name, ts);
CREATE INDEX IF NOT EXISTS obs_dedup ON obs(owner, name, price, sold, ts);
"""

ROW_FIELDS = ["ts", "name", "raw_name", "scroll", "complete", "dark", "equip", "stat", "pct",
              "price", "sold", "owner", "shop", "map", "channel", "conf", "expires", "client"]


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path = DB, crops: Path | None = None):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # crops next to the database, ids do not mix between databases; the server passes its own directory
        self.path = path
        self.crops = crops if crops is not None else path.with_name(path.stem + "_crops")
        self.crops.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(obs)")}
        for col, typ in (("expires", "TEXT"), ("client", "TEXT")):
            if col not in cols:  # database from before the column was introduced
                self.db.execute(f"ALTER TABLE obs ADD COLUMN {col} {typ}")
        self.db.commit()

    # --- client-side write (from recognize.Result) ---

    def add(self, r, now: datetime, client: str | None = None) -> list[int]:
        """`r` is a recognize.Result: observations from one frame, common owner and timer."""
        expires = _iso(now + timedelta(minutes=r.ttl_min)) if r.ttl_min is not None else None
        rows = []
        for o in r.obs:
            rows.append({
                "ts": _iso(now), "name": o.name, "raw_name": o.raw_name, "scroll": int(o.scroll),
                "complete": int(o.complete), "dark": None if o.dark is None else int(o.dark),
                "equip": o.equip, "stat": o.stat, "pct": o.pct, "price": o.price, "sold": int(o.sold),
                "owner": r.owner, "shop": r.title, "map": r.map, "channel": r.channel, "conf": o.conf,
                "expires": expires, "client": client,
            })
        ids = self.add_rows(rows)
        new = []
        for o, oid in zip(r.obs, ids):
            if oid is not None:
                o.crop.save(self.crops / f"{oid}.png")
                new.append(oid)
        return new

    # --- writing dict rows (server, import) ---

    def add_rows(self, rows: list[dict]) -> list[int | None]:
        """Inserts rows; returns an id for each one, or None when it is a duplicate. Duplicate = the same
        (owner, name, price, sold) within a ±DEDUP_MINUTES window around the row's `ts`, so the
        same offer seen by two collectors at the same time enters once."""
        out: list[int | None] = []
        with self.lock:
            for row in rows:
                ts = datetime.fromisoformat(row["ts"])
                lo, hi = _iso(ts - timedelta(minutes=DEDUP_MINUTES)), _iso(ts + timedelta(minutes=DEDUP_MINUTES))
                dup = self.db.execute(
                    "SELECT 1 FROM obs WHERE owner IS ? AND name=? AND price=? AND sold=? AND ts BETWEEN ? AND ? LIMIT 1",
                    (row.get("owner"), row["name"], row["price"], int(row["sold"]), lo, hi),
                ).fetchone()
                if dup:
                    out.append(None)
                    continue
                cur = self.db.execute(
                    f"INSERT INTO obs({','.join(ROW_FIELDS)}) VALUES ({','.join('?' * len(ROW_FIELDS))})",
                    tuple(row.get(k) for k in ROW_FIELDS),
                )
                out.append(int(cur.lastrowid))
            self.db.commit()
        return out

    def rows_after(self, last_id: int, limit: int = 200) -> list[dict]:
        """Rows to upload to the server, in id order."""
        with self.lock:
            cur = self.db.execute(
                f"SELECT id,{','.join(ROW_FIELDS)} FROM obs WHERE id>? ORDER BY id LIMIT ?", (last_id, limit)
            )
            return [dict(zip(["id"] + ROW_FIELDS, r)) for r in cur.fetchall()]

    def delete(self, oid: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM obs WHERE id=?", (oid,))
            self.db.commit()
        p = self.crops / f"{oid}.png"
        if p.exists():
            p.unlink()

    # --- summary ---

    @staticmethod
    def _expiry(ts: str, expires: str | None) -> str:
        if expires:
            return expires
        return _iso(datetime.fromisoformat(ts) + timedelta(hours=DEFAULT_TTL_H))

    def items(self) -> list[dict]:
        """Summary per item. "Current" means active offers whose shop is still up
        (per the shop timer) and which were not seen as sold later."""
        now = datetime.now()
        now_s = _iso(now)
        d7 = _iso(now - timedelta(days=7))
        with self.lock:
            rows = self.db.execute(
                "SELECT name, scroll, complete, ts, price, sold, owner, expires FROM obs ORDER BY name, ts"
            ).fetchall()
        # last known state of each offer (owner, name, price): sold or not
        last_state: dict[tuple, tuple[str, int]] = {}
        for name, scroll, complete, ts, price, sold, owner, expires in rows:
            last_state[(owner, name, price)] = (ts, int(sold))
        out: dict[str, dict] = {}
        for name, scroll, complete, ts, price, sold, owner, expires in rows:
            it = out.setdefault(name, {"name": name, "scroll": bool(scroll), "complete": bool(complete),
                                       "n": 0, "n_sold": 0, "last": ts, "now": [], "ask7d": [], "sold7d": []})
            it["n"] += 1
            it["last"] = max(it["last"], ts)
            if sold:
                it["n_sold"] += 1
                if ts >= d7:
                    it["sold7d"].append(price)
            else:
                if ts >= d7:
                    it["ask7d"].append(price)
                alive = self._expiry(ts, expires) > now_s and last_state[(owner, name, price)][1] == 0
                if alive:
                    it["now"].append(price)
        res = []
        for it in out.values():
            cur, a7, s7 = it.pop("now"), it.pop("ask7d"), it.pop("sold7d")
            it["n_now"] = len(cur)
            it["ask_min_now"] = min(cur) if cur else None
            it["ask_med_7d"] = int(statistics.median(a7)) if a7 else None
            it["ask_q1_7d"] = int(statistics.quantiles(a7, n=4)[0]) if len(a7) >= 4 else (min(a7) if a7 else None)
            it["sold_med_7d"] = int(statistics.median(s7)) if s7 else None
            it["sold_max_7d"] = max(s7) if s7 else None
            res.append(it)
        res.sort(key=lambda x: (not x["scroll"], x["name"]))
        return res

    def obs(self, name: str) -> list[dict]:
        now_s = _iso(datetime.now())
        with self.lock:
            rows = self.db.execute(
                "SELECT id, ts, price, sold, owner, shop, map, channel, raw_name, complete, expires, client"
                " FROM obs WHERE name=? ORDER BY ts DESC",
                (name,),
            ).fetchall()
        keys = ["id", "ts", "price", "sold", "owner", "shop", "map", "channel", "raw_name", "complete", "expires", "client"]
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            d["expires"] = self._expiry(d["ts"], d["expires"])
            d["expired"] = d["expires"] <= now_s
            out.append(d)
        return out

    def export(self) -> dict:
        """Everything viewer.html needs in static mode (data.json)."""
        items = self.items()
        return {
            "generated": _iso(datetime.now()),
            "items": items,
            "obs": {it["name"]: self.obs(it["name"]) for it in items},
        }
