"""paths.py: where resources and data live, the same way from sources and from a single exe file (PyInstaller).

- RES_DIR: files bundled with the program, read-only (glyph and icon templates, viewer.html).
  From sources it is the repository root, in the exe it is the temporary extraction
  directory (sys._MEIPASS), which disappears on exit, so NEVER write anything there.
- APP_DIR: the directory next to the program, for user data (`data/` with the database, config, crops).
  From sources it is also the repository root, in the exe it is the directory the exe file lives in.
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
