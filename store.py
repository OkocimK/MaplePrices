"""store.py: baza obserwacji (SQLite) i zestawienie per przedmiot.

Wspólne dla trackera (klient, Windows) i odbiornika na serwerze (`server/ingest.py`),
dlatego **tylko biblioteka standardowa**: żadnego Pillow, numpy ani winocr. Wycinki wierszy
(PNG) zapisuje ten, kto ma obraz: tracker przy `add`, serwer przy `add_rows`.

Schemat: jedna tabela `obs`, wiersz = jedna zaobserwowana oferta. `expires` to czas zniknięcia
sklepu z licznika, `client` to nick zbierającego (nadaje serwer z tokenu... nie, z payloadu,
token jest wspólny; nick służy do atrybucji i do wycięcia śmieci od jednego klienta).
"""

from __future__ import annotations

import sqlite3
import statistics
import threading
from datetime import datetime, timedelta
from pathlib import Path

from paths import DATA_DIR as DATA

DB = DATA / "prices.sqlite"  # wycinki lądują obok, w data/prices_crops/
DEDUP_MINUTES = 10
DEFAULT_TTL_H = 24  # gdy licznik sklepu był nieczytelny: tyle godzin oferta liczy się jako aktualna

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
        # wycinki obok bazy, id nie mieszają się między bazami; serwer podaje własny katalog
        self.path = path
        self.crops = crops if crops is not None else path.with_name(path.stem + "_crops")
        self.crops.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(obs)")}
        for col, typ in (("expires", "TEXT"), ("client", "TEXT")):
            if col not in cols:  # baza sprzed wprowadzenia kolumny
                self.db.execute(f"ALTER TABLE obs ADD COLUMN {col} {typ}")
        self.db.commit()

    # --- zapis po stronie klienta (z recognize.Result) ---

    def add(self, r, now: datetime, client: str | None = None) -> list[int]:
        """`r` to recognize.Result: obserwacje z jednej klatki, wspólny właściciel i licznik."""
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

    # --- zapis wierszy słownikowych (serwer, import) ---

    def add_rows(self, rows: list[dict]) -> list[int | None]:
        """Wstawia wiersze; zwraca id dla każdego albo None, gdy to duplikat. Duplikat = ta sama
        (właściciel, nazwa, cena, wykupiony) w oknie ±DEDUP_MINUTES wokół `ts` wiersza, więc
        ta sama oferta widziana przez dwóch zbierających w tym samym czasie wchodzi raz."""
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
        """Wiersze do wysłania na serwer, w kolejności id."""
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

    # --- zestawienie ---

    @staticmethod
    def _expiry(ts: str, expires: str | None) -> str:
        if expires:
            return expires
        return _iso(datetime.fromisoformat(ts) + timedelta(hours=DEFAULT_TTL_H))

    def items(self) -> list[dict]:
        """Zestawienie per przedmiot. „Aktualne" to aktywne oferty, których sklep jeszcze stoi
        (licznik ze sklepu) i których nie widziano później jako wykupione."""
        now = datetime.now()
        now_s = _iso(now)
        d7 = _iso(now - timedelta(days=7))
        with self.lock:
            rows = self.db.execute(
                "SELECT name, scroll, complete, ts, price, sold, owner, expires FROM obs ORDER BY name, ts"
            ).fetchall()
        # ostatni znany stan każdej oferty (właściciel, nazwa, cena): wykupiona czy nie
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
        """Wszystko, czego potrzebuje viewer.html w trybie statycznym (data.json)."""
        items = self.items()
        return {
            "generated": _iso(datetime.now()),
            "items": items,
            "obs": {it["name"]: self.obs(it["name"]) for it in items},
        }
