# MaplePrices

Free Market price tracker for **Old School Maple** (a MapleStory Worlds world). A small Windows
program screenshots the hired merchant window while you browse shops, reads item names and
prices with OCR and uploads them to a shared page: **https://osmsfm.duckdns.org/**

It never touches the game: no memory reading, no input, no packets. Screenshot plus OCR.

- Want to contribute prices? Download the tracker from the page (one exe) and read
  [USER-GUIDE.md](USER-GUIDE.md).
- Want to hack on it? The module docstrings describe what was measured on real screenshots
  and why each threshold is what it is; start with `shopframe.py` and `recognize.py`.

## Layout

| File | What |
|---|---|
| `tracker.py` | main loop: capture, recognise, store, local preview on :8778, background upload |
| `shopframe.py` | shop window geometry: finds the list in the frame (movable window), empty/sold rows |
| `minimap.py`, `minimap_templates.json` | finds the minimap (movable, collapsed or hidden) to read the map and the number next to it (in the Free Market that is the room) |
| `glyphs.py`, `price_glyphs.json` | price digits by template matching (Windows OCR drops numbers) |
| `names.py`, `icons.py`, `icon_templates.json` | scroll name grammar, percent from icon colour |
| `recognize.py` | one frame in, observations out; scales the frame to the reference UI size first (cursor and stale-icon guards) |
| `ocrsetup.py` | checks for the English Windows OCR feature at startup, offers to install it (elevated `dism.exe`, one UAC prompt) |
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

Requires Windows (Windows OCR, win32 capture). Any game window size works (the game scales its
UI with the window and the tracker normalises the frame), 1920x1080 reads best.
