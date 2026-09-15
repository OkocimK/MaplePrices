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
    POST /api/sync             wyślij teraz na serwer
    POST /api/delete?id=N      usuń błędną obserwację
    GET  /crops/N.png          wycinek
"""

from __future__ import annotations

import argparse
import ctypes
import glob
import hashlib
import json
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

import recognize
from store import DATA, DB, Store

from paths import RES_DIR as HERE  # viewer.html leży w zasobach, także w exe
PORT = 8778
TOGGLE_KEY = "f9"
QUIT_KEY = "ctrl+f9"
PERIOD = 0.5

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
        self.client: str | None = None  # nick zbierającego, podpis wierszy
        # Wysyłka na serwer (sync.py) chodzi w tle: co SYNC_MIN podczas zbierania,
        # gdy przybyły nowe oferty, i zaraz po wyłączeniu zbierania. Naraz najwyżej jedna.
        self.published_added = 0  # ile ofert było w bazie przy ostatniej udanej publikacji
        self.publish_at: str | None = None
        self.publishing = False
        self.publish_error: str | None = None
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
                    "stale": self.stale, "publishing": self.publishing, "publish_at": self.publish_at,
                    "publish_error": self.publish_error, "unpublished": self.added - self.published_added,
                    "log": list(reversed(self.log))}


def make_handler(store: Store, state: State, on_toggle, on_sync=lambda: False):
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
            elif u.path == "/api/sync":
                on_sync()
                self._json(state.snapshot())
            elif u.path == "/api/delete" and q.get("id", [""])[0].isdigit():
                store.delete(int(q["id"][0]))
                state.note(f"usunięto obserwację {q['id'][0]}")
                self._json({"ok": True})
            else:
                self.send_error(404)

    return H


def serve(store: Store, state: State, on_toggle, on_sync=lambda: False) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(store, state, on_toggle, on_sync))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _key(r: recognize.Result, o: recognize.Obs) -> tuple:
    return (r.owner, o.name, o.price, o.sold)


def commit(r: recognize.Result, obs: list[recognize.Obs], store: Store, state: State, now: datetime) -> None:
    if not obs:
        return
    sub = recognize.Result(True, r.owner, r.title, r.map, r.channel, obs, timer_raw=r.timer_raw, ttl_min=r.ttl_min)
    new = store.add(sub, now, state.client)
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


SYNC_MIN = 5  # co tyle minut wysyłka w tle podczas zbierania, jeśli coś przybyło


class Uploader:
    """Wysyłka na serwer (sync.push) w osobnym wątku, żeby sieć nie zatrzymywała pętli zrzutów.
    Naraz najwyżej jedna; nieudana próba nic nie psuje, wiersze czekają w lokalnej bazie."""

    def __init__(self, store: Store, state: State, cfg: dict) -> None:
        self.store = store
        self.state = state
        self.cfg = cfg
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self._last = 0.0

    def due(self, now: float) -> bool:
        s = self.state
        if s.publishing or s.added == s.published_added:
            return False
        return now - self._last >= SYNC_MIN * 60

    def start(self, reason: str) -> bool:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return False
            self.state.publishing = True
            self.thread = threading.Thread(target=self._run, args=(reason,), daemon=True)
            self.thread.start()
            return True

    def wait(self, timeout: float) -> None:
        t = self.thread
        if t is not None:
            t.join(timeout)

    def _run(self, reason: str) -> None:
        import sync

        s = self.state
        target = s.added
        s.note(f"wysyłka na serwer ({reason})...")
        try:
            sync.push(self.store, self.cfg, log=s.note)
            s.published_added = target
            s.publish_at = datetime.now().isoformat(timespec="seconds")
            s.publish_error = None
        except Exception as e:  # brak sieci, zły token: zapis lokalny jest bezpieczny, spróbujemy później
            s.publish_error = str(e).splitlines()[0]
            s.note(f"wysyłka nie przeszła: {s.publish_error}")
        finally:
            self._last = time.time()
            s.publishing = False


def live(store: Store, state: State, title: str, start_on: bool = False, seconds: float | None = None,
         auto_sync: bool = True) -> None:
    import sync

    cfg = sync.load_config() if auto_sync else None  # bez pytań: serwer i token wpisane na sztywno
    client = cfg["client"] if cfg else None
    state.client = client
    import keyboard
    import mss
    import win32api
    import win32gui

    import grab

    grab.set_dpi_aware()
    hwnd = grab.find_window(title)
    if hwnd is None:
        print(f"Nie znalazłem okna gry (z '{title}' w tytule). Uruchom grę w oknie i spróbuj jeszcze raz.")
        if getattr(sys, "frozen", False):
            input("Enter zamyka.")  # exe z dwukliku: okno konsoli zniknęłoby zanim ktoś przeczyta
        sys.exit(1)
    state.note(f"okno gry: {win32gui.GetWindowText(hwnd)!r}")
    sct = (getattr(mss, "MSS", None) or mss.mss)()

    pub = Uploader(store, state, cfg)

    def toggle() -> None:
        state.on = not state.on
        state.note("zbieranie WŁĄCZONE" if state.on else "zbieranie wyłączone")
        if not state.on and auto_sync and state.added != state.published_added:
            pub.start("po wyłączeniu zbierania")

    keyboard.add_hotkey(TOGGLE_KEY, toggle)
    stop = threading.Event()
    keyboard.add_hotkey(QUIT_KEY, stop.set)
    srv = serve(store, state, toggle, lambda: pub.start("na żądanie"))
    state.note(f"podgląd: http://localhost:{PORT}/   [{TOGGLE_KEY.upper()}] on/off  [{QUIT_KEY.upper()}] koniec"
               + (f"   wysyłka na {cfg['server']} jako {client}" if auto_sync else "   wysyłka na serwer: wyłączona"))
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
            if auto_sync and pub.due(time.time()):
                pub.start(f"co {SYNC_MIN} min")
            time.sleep(max(0.0, PERIOD - (time.time() - t0)))
    finally:
        keyboard.unhook_all()
        sct.close()
        if auto_sync and state.added != state.published_added:
            pub.start("na koniec")
        pub.wait(600)  # ostatnia publikacja ma dojść, zanim proces zniknie
        srv.shutdown()
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
    ap.add_argument("--no-sync", action="store_true", help="nie wysyłaj na serwer (tylko lokalna baza)")
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
    live(store, state, args.title, args.on, args.seconds, auto_sync=not args.no_sync)
    return 0


if __name__ == "__main__":
    sys.exit(main())
