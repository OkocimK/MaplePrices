"""build_exe.py: a single file `dist/osmsfm-tracker.exe` via PyInstaller.

    .venv\\Scripts\\python build_exe.py

What goes in: tracker.py with its dependencies, glyph and icon templates, viewer.html (via paths.RES_DIR).
Nothing secret: every tracker registers with the server for its own upload key (sync.py).
Pitfalls that have to be worked around by hand:
- winocr imports winrt modules (`winrt.windows.media.ocr` etc.), which load their .pyd
  files by name at runtime, so PyInstaller will not find them on its own: collect-all on
  every winrt-* package.
- keyboard and mss are pure, pywin32 has its own hook in PyInstaller.
- User data (`data/`) lands next to the exe (paths.APP_DIR), not in the extraction directory.

The exe is a console one on purpose: the tracker log is the only place where you can see what is going on.
"""

from __future__ import annotations

import importlib.metadata as md
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "osmsfm-tracker"
# OSMSFM_DIST=some/dir builds elsewhere, e.g. while dist/osmsfm-tracker.exe is running and locked.
DIST = Path(os.environ.get("OSMSFM_DIST") or HERE / "dist").resolve()

# Every winrt-* distribution drops into the shared `winrt` package one .pyd like
# `winrt/_winrt_windows_media_ocr.cp312-win_amd64.pyd`, loaded by name at runtime.
# collect-all on `winrt` gathers the package, and an explicit hidden-import on every .pyd
# pins down what static analysis does not see.
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

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--console",
    "--name", NAME,
    "--distpath", str(DIST),
    "--workpath", str(HERE / "build"),
    "--specpath", str(HERE / "build"),
    "--add-data", f"{HERE / 'price_glyphs.json'};.",
    "--add-data", f"{HERE / 'icon_templates.json'};.",
    "--add-data", f"{HERE / 'minimap_templates.json'};.",
    "--add-data", f"{HERE / 'viewer.html'};.",
    "--exclude-module", "cv2",  # unused, and it would add 60 MB
    "--exclude-module", "tkinter",
    "--exclude-module", "rapidocr_onnxruntime",
    "--exclude-module", "onnxruntime",
    "--hidden-import", "winocr",
    *collect,
    str(HERE / "tracker.py"),
]
print("winrt packages:", ", ".join(winrt_pkgs))
r = subprocess.run(cmd, cwd=HERE)
if r.returncode == 0:
    exe = DIST / f"{NAME}.exe"
    print(f"\n{exe}  {exe.stat().st_size / 1e6:.1f} MB")
sys.exit(r.returncode)
