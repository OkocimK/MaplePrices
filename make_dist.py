"""make_dist.py: the package for friends, `dist/osmsfm-tracker.zip` = exe + README.

First `build_exe.py` (PyInstaller), then this. A zip instead of a bare exe, because browsers
and messengers frown upon a downloaded .exe, while a .zip gets through. The sources are not in
the zip: the server key is in the exe anyway, and a friend is not meant to read it, only run it.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = Path(os.environ.get("OSMSFM_DIST") or HERE / "dist").resolve()  # same override as build_exe.py


def main() -> None:
    exe = DIST / "osmsfm-tracker.exe"
    if not exe.exists():
        raise SystemExit("missing dist/osmsfm-tracker.exe, first run: .venv\\Scripts\\python build_exe.py")
    out = DIST / "osmsfm-tracker.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, exe.name)
        z.write(HERE / "USER-GUIDE.md", "README.md")
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
