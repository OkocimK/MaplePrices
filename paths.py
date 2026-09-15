"""paths.py: gdzie leżą zasoby i dane, tak samo ze źródeł i z jednego pliku exe (PyInstaller).

- RES_DIR: pliki dołączone do programu, tylko do odczytu (wzorce glifów i ikon, viewer.html).
  Ze źródeł to katalog pricetrack/, w exe to tymczasowy katalog rozpakowania (sys._MEIPASS),
  który znika po wyjściu, więc NIC tam nie zapisywać.
- APP_DIR: katalog obok programu, na dane użytkownika (`data/` z bazą, configiem, wycinkami).
  Ze źródeł to też pricetrack/, w exe katalog, w którym leży plik exe.
"""

from __future__ import annotations

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
RES_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
