# MaplePrices

Free Market price tracker for **Old School Maple** (a MapleStory Worlds world). A small Windows
program screenshots the hired merchant window while you browse shops, reads item names and
prices with OCR and uploads them to a shared page: **https://osmsfm.duckdns.org/**

It never touches the game: no memory reading, no input, no packets. Screenshot plus OCR.

- Want to contribute prices? Download the tracker from the page (one exe) and read
  [README-znajomi.md](README-znajomi.md) (user guide).
- Want to hack on it? Read [PLAN.md](PLAN.md) (in Polish): design decisions, what was measured
  on real screenshots, what is still open.

## Layout

| File | What |
|---|---|
| `tracker.py` | main loop: capture, recognise, store, local preview on :8778, background upload |
| `shopframe.py` | fixed geometry of the shop window, empty/sold rows |
| `glyphs.py`, `price_glyphs.json` | price digits by template matching (Windows OCR drops numbers) |
| `names.py`, `icons.py`, `icon_templates.json` | scroll name grammar, percent from icon colour |
| `recognize.py` | one frame in, observations out (cursor and stale-icon guards) |
| `store.py` | SQLite schema and per-item summary, shared with the server |
| `sync.py` | upload to the server in batches, anonymous client id |
| `server/` | ingest service (stdlib only, systemd unit) |
| `viewer.html` | the page, works both locally (live API) and published (static `data.json`) |
| `publish.py` | deploys server code, the page and the tracker zip over ssh |
| `build_exe.py`, `make_dist.py` | PyInstaller build and the zip for download |

## Build

```
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python build_exe.py      # needs token.txt (upload token, not in git)
.venv\Scripts\python make_dist.py
```

Requires Windows (Windows OCR, win32 capture). Game must run windowed at 1920x1080.
