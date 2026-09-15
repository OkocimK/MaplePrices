"""make_dist.py: paczka dla znajomych, `dist/osmsfm-tracker.zip` = exe + README.

Najpierw `build_exe.py` (PyInstaller), potem to. Zip zamiast gołego exe, bo przeglądarki
i komunikatory krzywo patrzą na pobierany .exe, a .zip przechodzi. Źródła w zipie nie ma:
klucz do serwera i tak jest w exe, a znajomy nie ma go czytać, tylko uruchomić.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    exe = HERE / "dist" / "osmsfm-tracker.exe"
    if not exe.exists():
        raise SystemExit("brak dist/osmsfm-tracker.exe, najpierw: .venv\\Scripts\\python build_exe.py")
    out = HERE / "dist" / "osmsfm-tracker.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, exe.name)
        z.write(HERE / "README-znajomi.md", "README.md")
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
