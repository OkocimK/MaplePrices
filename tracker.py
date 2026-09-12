"""tracker.py: pętla na żywo. Zrzut okna gry, rozpoznanie, zapis do SQLite, podgląd w przeglądarce.

    .venv\\Scripts\\python tracker.py                 # start, przeglądarka: http://localhost:8778/
    .venv\\Scripts\\python tracker.py --replay "samples/*.png"   # test offline na zapisanych klatkach

Skróty (globalne):
    F9        włącz/wyłącz zbieranie (wyłączone = zero zrzutów)
    Ctrl+F9   zakończ

Co robi w pętli (co ~500 ms, tylko gdy włączone):
1. zrzut obszaru klienta okna gry (mss), pozycja kursora z systemu (win32api),
2. `recognize.recognize` -> obserwacje; wiersze pod kursorem i z niepewną ceną odpadają,
3. deduplikacja: ta sama (właściciel, nazwa, cena, wykupiony) w ciągu 10 minut = ta sama
   oferta, nie dopisujemy; po dłuższym czasie to nowa obserwacja,
4. zapis do `data/prices.sqlite`, wycinek wiersza do `data/crops/<id>.png` (do audytu).

HTTP na :8778 serwuje `viewer.html` i proste JSON API:
    GET  /api/state            stan (on/off, liczniki, ostatnie zdarzenia)
    GET  /api/items            zestawienie per przedmiot
    GET  /api/obs?name=...     obserwacje jednego przedmiotu
    POST /api/toggle           przełącz on/off
    POST /api/delete?id=N      usuń błędną obserwację
    GET  /crops/N.png          wycinek
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import hashlib
import json
import sqlite3
import statistics
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

import recognize

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
DB = DATA / "prices.sqlite"  # wycinki lądują obok, w data/prices_crops/
PORT = 8778
DEDUP_MINUTES = 10
TOGGLE_KEY = "f9"
QUIT_KEY = "ctrl+f9"
PERIOD = 0.5

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
  expires TEXT
);
CREATE INDEX IF NOT EXISTS obs_name ON obs(name, ts);
CREATE INDEX IF NOT EXISTS obs_dedup ON obs(owner, name, price, sold, ts);
"""
DEFAULT_TTL_H = 24  # gdy licznik sklepu był nieczytelny: tyle godzin oferta liczy się jako aktualna


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path = DB):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.crops = path.with_name(path.stem + "_crops")  # wycinki obok bazy, id nie mieszają się między bazami
        self.crops.mkdir(exist_ok=True)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(obs)")}
        if "expires" not in cols:  # baza sprzed wprowadzenia przedawniania
            self.db.execute("ALTER TABLE obs ADD COLUMN expires TEXT")
            self.db.commit()

    def add(self, r: recognize.Result, now: datetime) -> list[int]:
        new: list[int] = []
        since = _iso(now - timedelta(minutes=DEDUP_MINUTES))
        expires = _iso(now + timedelta(minutes=r.ttl_min)) if r.ttl_min is not None else None
        with self.lock:
            for o in r.obs:
                dup = self.db.execute(
                    "SELECT 1 FROM obs WHERE owner IS ? AND name=? AND price=? AND sold=? AND ts>=? LIMIT 1",
                    (r.owner, o.name, o.price, int(o.sold), since),
                ).fetchone()
                if dup:
                    continue
                cur = self.db.execute(
                    "INSERT INTO obs(ts,name,raw_name,scroll,complete,dark,equip,stat,pct,price,sold,owner,shop,map,channel,conf,expires)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_iso(now), o.name, o.raw_name, int(o.scroll), int(o.complete),
                     None if o.dark is None else int(o.dark), o.equip, o.stat, o.pct, o.price, int(o.sold),
                     r.owner, r.title, r.map, r.channel, o.conf, expires),
                )
                oid = int(cur.lastrowid)
                o.crop.save(self.crops / f"{oid}.png")
                new.append(oid)
            self.db.commit()
        return new

    def delete(self, oid: int) -> None:
        with self.lock:
            self.db.execute("DELETE FROM obs WHERE id=?", (oid,))
            self.db.commit()
        p = self.crops / f"{oid}.png"
        if p.exists():
            p.unlink()

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
                "SELECT id, ts, price, sold, owner, shop, map, channel, raw_name, conf, complete, expires FROM obs WHERE name=? ORDER BY ts DESC",
                (name,),
            ).fetchall()
        keys = ["id", "ts", "price", "sold", "owner", "shop", "map", "channel", "raw_name", "conf", "complete", "expires"]
        out = []
        for r in rows:
            d = dict(zip(keys, r))
            d["expires"] = self._expiry(d["ts"], d["expires"])
            d["expired"] = d["expires"] <= now_s
            out.append(d)
        return out


class State:
    def __init__(self) -> None:
        self.on = False
        self.frames = 0
        self.shops = 0
        self.added = 0
        self.stale = 0  # klatki z ikonami jeszcze z poprzedniego stanu listy
        self.log: list[str] = []
        self.lock = threading.Lock()
        self.last_owner: str | None = None
        # Potwierdzanie: oferta wchodzi do bazy dopiero, gdy ta sama (właściciel, nazwa, cena,
        # wykupiony) pokaże się w dwóch kolejnych rozpoznanych klatkach albo klatka nie zmieni
        # się przez jeden okres. Powód: gra po otwarciu sklepu i po przewinięciu przez chwilę
        # pokazuje nowe napisy ze starymi ikonami (procent z ikony byłby zły), a klatka
        # potrafi być rozdarta w trakcie przerysowania.
        self.pending: dict[tuple, recognize.Obs] = {}
        self.pending_result: recognize.Result | None = None

    def note(self, msg: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        with self.lock:
            self.log.append(f"{stamp} {msg}")
            del self.log[:-40]
        print(f"{stamp} {msg}", flush=True)

    def snapshot(self) -> dict:
        with self.lock:
            return {"on": self.on, "frames": self.frames, "shops": self.shops, "added": self.added,
                    "stale": self.stale, "log": list(reversed(self.log))}


def make_handler(store: Store, state: State, on_toggle):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:  # cisza w konsoli
            pass

        def _json(self, obj: object, code: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _file(self, path: Path, ctype: str) -> None:
            if not path.exists():
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path in ("/", "/viewer.html"):
                self._file(HERE / "viewer.html", "text/html; charset=utf-8")
            elif u.path == "/api/state":
                self._json(state.snapshot())
            elif u.path == "/api/items":
                self._json(store.items())
            elif u.path == "/api/obs":
                self._json(store.obs(q.get("name", [""])[0]))
            elif u.path.startswith("/crops/") and u.path.endswith(".png") and u.path[7:-4].isdigit():
                self._file(store.crops / u.path[7:], "image/png")
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            u = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(u.query)
            if u.path == "/api/toggle":
                on_toggle()
                self._json(state.snapshot())
            elif u.path == "/api/delete" and q.get("id", [""])[0].isdigit():
                store.delete(int(q["id"][0]))
                state.note(f"usunięto obserwację {q['id'][0]}")
                self._json({"ok": True})
            else:
                self.send_error(404)

    return H


def serve(store: Store, state: State, on_toggle) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(store, state, on_toggle))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _key(r: recognize.Result, o: recognize.Obs) -> tuple:
    return (r.owner, o.name, o.price, o.sold)


def commit(r: recognize.Result, obs: list[recognize.Obs], store: Store, state: State, now: datetime) -> None:
    if not obs:
        return
    sub = recognize.Result(True, r.owner, r.title, r.map, r.channel, obs, timer_raw=r.timer_raw, ttl_min=r.ttl_min)
    new = store.add(sub, now)
    if new:
        state.added += len(new)
        state.note(f"+{len(new)} nowych ofert u {r.owner}")


def process(frame: Image.Image, cursor: tuple[int, int] | None, store: Store, state: State, now: datetime,
            confirm: bool = True) -> None:
    r = recognize.recognize(frame, cursor)
    state.frames += 1
    if not r.open:
        state.last_owner = None
        state.pending.clear()
        return
    if r.owner != state.last_owner:
        state.shops += 1
        state.last_owner = r.owner
        state.note(f"sklep: {r.owner} ({r.map}<{r.channel}>)" + (f", kursor zasłania {r.skipped_cursor}" if r.skipped_cursor else ""))
        state.pending.clear()
        if r.channel is None or r.ttl_min is None:
            # nieczytelna minimapa albo licznik: odłóż wycinki do obejrzenia
            dbg = DATA / "debug"
            dbg.mkdir(exist_ok=True)
            stamp = now.strftime("%H%M%S")
            if r.channel is None:
                frame.crop(recognize.MINIMAP).save(dbg / f"{stamp}-minimap.png")
                state.note(f"kanał nieczytelny: {r.map_raw!r}")
            if r.ttl_min is None:
                frame.crop(recognize.sf.TIMER).save(dbg / f"{stamp}-timer.png")
                state.note(f"licznik nieczytelny: {r.timer_raw!r}")
    if r.stale_icons:
        state.stale += 1
        if r.skipped_stale:
            state.note(f"nieświeże ikony u {r.owner}, odłożone {r.skipped_stale} wierszy do następnej klatki")
    if not confirm:
        commit(r, r.obs, store, state, now)
        return
    confirmed = [o for o in r.obs if _key(r, o) in state.pending]
    commit(r, confirmed, store, state, now)
    state.pending = {_key(r, o): o for o in r.obs}
    state.pending_result = r


def process_same(store: Store, state: State, now: datetime) -> None:
    """Klatka identyczna z poprzednią: to, co czekało, jest potwierdzone samym trwaniem."""
    if state.pending and state.pending_result is not None:
        commit(state.pending_result, list(state.pending.values()), store, state, now)
        state.pending.clear()


def replay(pattern: str, store: Store, state: State) -> None:
    files = sorted(glob.glob(pattern))
    state.note(f"replay {len(files)} klatek")
    t = datetime.now()
    for f in files:
        process(Image.open(f), None, store, state, t, confirm=False)  # próbki to pojedyncze klatki
        t += timedelta(seconds=1)
    state.note(f"replay koniec: {state.shops} sklepów, {state.added} obserwacji")


def live(store: Store, state: State, title: str, start_on: bool = False, seconds: float | None = None) -> None:
    import keyboard
    import mss
    import win32api
    import win32gui

    import grab

    grab.set_dpi_aware()
    hwnd = grab.find_window(title)
    if hwnd is None:
        print(f"Nie znalazłem okna z '{title}' w tytule. grab.py --list pokaże, co jest.")
        sys.exit(1)
    state.note(f"okno gry: {win32gui.GetWindowText(hwnd)!r}")
    sct = (getattr(mss, "MSS", None) or mss.mss)()

    def toggle() -> None:
        state.on = not state.on
        state.note("zbieranie WŁĄCZONE" if state.on else "zbieranie wyłączone")

    keyboard.add_hotkey(TOGGLE_KEY, toggle)
    stop = threading.Event()
    keyboard.add_hotkey(QUIT_KEY, stop.set)
    srv = serve(store, state, toggle)
    state.note(f"podgląd: http://localhost:{PORT}/   [{TOGGLE_KEY.upper()}] on/off  [{QUIT_KEY.upper()}] koniec")
    last_hash = None
    if start_on:
        toggle()
    if seconds is not None:
        threading.Timer(seconds, stop.set).start()
    try:
        while not stop.is_set():
            if not state.on or not win32gui.IsWindow(hwnd):
                time.sleep(0.2)
                continue
            t0 = time.time()
            rect = grab.client_rect(hwnd)
            if rect["width"] <= 0:
                time.sleep(PERIOD)
                continue
            shot = sct.grab(rect)
            frame = Image.frombytes("RGB", shot.size, shot.rgb)
            h = hashlib.blake2b(shot.rgb, digest_size=16).digest()
            try:
                if h != last_hash:
                    last_hash = h
                    cx, cy = win32api.GetCursorPos()
                    cursor = (cx - rect["left"], cy - rect["top"])
                    process(frame, cursor, store, state, datetime.now())
                else:
                    process_same(store, state, datetime.now())
            except Exception as e:  # jedna zła klatka nie ma zabijać pętli
                state.note(f"błąd klatki: {type(e).__name__}: {e}")
            time.sleep(max(0.0, PERIOD - (time.time() - t0)))
    finally:
        keyboard.unhook_all()
        srv.shutdown()
        sct.close()
    state.note(f"koniec: {state.frames} klatek rozpoznanych, {state.shops} sklepów, {state.added} nowych ofert")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", default="MapleStory", help="fragment tytułu okna gry")
    ap.add_argument("--replay", help="glob klatek PNG zamiast gry (test offline)")
    ap.add_argument("--db", default=str(DB), help="ścieżka bazy (domyślnie data/prices.sqlite)")
    ap.add_argument("--serve", action="store_true", help="przy --replay zostaw HTTP włączony do obejrzenia")
    ap.add_argument("--view", action="store_true", help="sam podgląd bazy w przeglądarce, bez zrzutów i bez gry")
    ap.add_argument("--on", action="store_true", help="startuj z włączonym zbieraniem (bez F9)")
    ap.add_argument("--seconds", type=float, help="zakończ po N sekundach (test)")
    args = ap.parse_args()
    store = Store(Path(args.db))
    state = State()
    if args.replay or args.view:
        if args.replay:
            replay(args.replay, store, state)
        if args.serve or args.view:
            state.on = False
            serve(store, state, lambda: None)
            print(f"http://localhost:{PORT}/  (Ctrl+C kończy)")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        return 0
    live(store, state, args.title, args.on, args.seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
