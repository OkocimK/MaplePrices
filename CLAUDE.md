# MaplePrices: tracker cen scrolli w Old School Maple

Osobny projekt open source (MIT), publiczne repo https://github.com/OkocimK/MaplePrices,
wydzielony 15 września 2026 z repo kalkulatora DPM (`D:\Godot\Maple`), które zostaje osobno.
Strona publiczna: https://osmsfm.duckdns.org/ na hoście `rimhaven` (współdzielonym z kalkulatorem
i z RimHavenem; szczegóły hosta w `D:\Godot\Maple\deploy\README.md`).

**Stan projektu, decyzje i pomiary są w [`PLAN.md`](PLAN.md), czytać najpierw.** Mapa plików
w [`README.md`](README.md). Instrukcja dla użytkowników w [`README-znajomi.md`](README-znajomi.md),
ta sama jedzie w zipie.

## Zasady

- **Nigdy nie używać em dasha**, ani w prozie, ani w UI, ani w komunikatach. Spacja, przecinek
  albo dwukropek.
- **Nie edytować plików przez PowerShell** (`Get-Content`/`Set-Content` czyta w cp1250 i zjada
  `×`, `−`, `≤`). Edit/Write albo Python z `encoding="utf-8"`.
- **Sekrety poza gitem.** Token do serwera leży w `token.txt` (ignorowany), `build_exe.py`
  wkłada go do exe. Przed każdym pushem `git grep` po tokenie i adresach IP. Historia repo była
  raz przepisana, żeby usunąć token; nie wpuszczać go z powrotem.
- **Użytkownicy anonimowi.** Podpis wierszy to losowe `anon-xxxxxx`, nie nick ani nazwa konta
  Windows. Nie zbierać niczego o postaci, koncie ani czacie.
- Kod i UI dla użytkowników po angielsku (konsola trackera, strona, README dla znajomych).
  Komentarze w kodzie, `PLAN.md` i ten plik po polsku, to notatki robocze.
- Wzorów i geometrii nie zgadywać: wszystko, co w `PLAN.md` jest oznaczone jako zmierzone,
  zostało zmierzone na próbkach z `samples/` (poza gitem). Nowe wątpliwości dopisywać tam.

## Środowisko

- Windows, Python 3.12 w `.venv` (`py -3.12 -m venv .venv`, `pip install -r requirements.txt
  pyinstaller`). Windows OCR (winocr) i zrzut przez mss/win32, więc tylko Windows.
- ⚠️ winocr i RapidOCR w jednym procesie wywalają Pythona po cichu; nie łączyć.
- Testy bez gry: `tracker.py --replay "samples/*.png" --db data/replay.sqlite`, wpis
  `pricetrack-replay` w `.claude/launch.json`. Żywy tracker: `tracker.py`, F9 on/off, Ctrl+F9
  koniec, podgląd http://localhost:8778/, F10 zapisuje klatkę do `data/debug/`. Gra 1920 px
  szeroka: u mnie okno 1920×1080 przycięte do klienta 1920×1009, u znajomych też pełne 1920×1080;
  okno sklepu i minimapa są szukane w klatce, nie stoją na stałych współrzędnych.
- Poza gitem: `.venv/`, `data/` (baza, wycinki, `config.json` z tokenem), `samples/`, `dist/`,
  `build/`, `token.txt`.

## Wydanie

1. `build_exe.py` (PyInstaller, wymaga `token.txt`) i `make_dist.py` (zip z README).
2. `publish.py` wgrywa przez ssh kod odbiornika (`server/`), unit systemd, `index.html`
   i zip do `/download/`; `--nginx` dodatkowo `server/osmsfm.nginx.conf`.
   ⚠️ Po każdym ruchu certbota na serwerze ściągnąć konfigurację z powrotem:
   `scp rimhaven:/etc/nginx/sites-available/osmsfm server/osmsfm.nginx.conf`.
3. Commit i `git push` (remote `origin`, gałąź `main`).
