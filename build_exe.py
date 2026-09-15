"""build_exe.py: jeden plik `dist/osmsfm-tracker.exe` przez PyInstaller.

    .venv\\Scripts\\python build_exe.py

Co wchodzi: tracker.py z zależnościami, wzorce glifów i ikon, viewer.html (przez paths.RES_DIR).
Pułapki, które trzeba obejść ręcznie:
- winocr importuje moduły winrt (`winrt.windows.media.ocr` itd.), które ładują swoje .pyd
  po nazwie w czasie działania, więc PyInstaller ich sam nie znajdzie: collect-all na
  każdym pakiecie winrt-*.
- keyboard i mss są czyste, pywin32 ma własny hook w PyInstallerze.
- Dane użytkownika (`data/`) lądują obok exe (paths.APP_DIR), nie w katalogu rozpakowania.

Exe jest konsolowe celowo: log trackera to jedyne miejsce, gdzie widać, co się dzieje.
"""

from __future__ import annotations

import importlib.metadata as md
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "osmsfm-tracker"

# Każda dystrybucja winrt-* wrzuca do wspólnego pakietu `winrt` jeden .pyd w rodzaju
# `winrt/_winrt_windows_media_ocr.cp312-win_amd64.pyd`, ładowany po nazwie w czasie działania.
# collect-all na `winrt` zbiera pakiet, a jawne hidden-import na każdym .pyd dopina to,
# czego analiza statyczna nie widzi.
pyds = sorted(
    "winrt." + Path(str(f)).name.split(".")[0]
    for d in md.distributions()
    if d.metadata["Name"].lower().startswith("winrt")
    for f in (d.files or [])
    if str(f).endswith(".pyd")
)
collect = ["--collect-all", "winrt"]
for m in pyds:
    collect += ["--hidden-import", m]
winrt_pkgs = pyds

if not (HERE / "token.txt").exists():
    sys.exit("brak token.txt (token z /etc/osmsfm/token na serwerze, publish.py go wypisuje)")

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--console",
    "--name", NAME,
    "--distpath", str(HERE / "dist"),
    "--workpath", str(HERE / "build"),
    "--specpath", str(HERE / "build"),
    "--add-data", f"{HERE / 'price_glyphs.json'};.",
    "--add-data", f"{HERE / 'icon_templates.json'};.",
    "--add-data", f"{HERE / 'minimap_templates.json'};.",
    "--add-data", f"{HERE / 'viewer.html'};.",
    "--add-data", f"{HERE / 'token.txt'};.",  # sekret poza gitem, patrz sync.py
    "--exclude-module", "cv2",  # nieużywane, a doklejałoby 60 MB
    "--exclude-module", "tkinter",
    "--exclude-module", "rapidocr_onnxruntime",
    "--exclude-module", "onnxruntime",
    "--hidden-import", "winocr",
    *collect,
    str(HERE / "tracker.py"),
]
print("pakiety winrt:", ", ".join(winrt_pkgs))
r = subprocess.run(cmd, cwd=HERE)
if r.returncode == 0:
    exe = HERE / "dist" / f"{NAME}.exe"
    print(f"\n{exe}  {exe.stat().st_size / 1e6:.1f} MB")
sys.exit(r.returncode)
