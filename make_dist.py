"""make_dist.py: paczka trackera dla znajomych, `dist/osmsfm-tracker.zip`.

Do zipa idzie tylko to, czego tracker potrzebuje u kogoś innego: kod klienta, wzorce glifów
i ikon, viewer, requirements, install.bat, run.bat i README-znajomi.md. Nie idą: .venv,
samples, data (baza, config z tokenem!), server/, publish.py, PLAN.md.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = [
    "tracker.py", "store.py", "sync.py", "recognize.py", "shopframe.py", "glyphs.py", "names.py",
    "icons.py", "grab.py", "reprocess.py", "price_glyphs.json", "icon_templates.json",
    "viewer.html", "requirements.txt", "install.bat", "run.bat", "README-znajomi.md",
]


def main() -> None:
    out = HERE / "dist" / "osmsfm-tracker.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.write(HERE / f, f"osmsfm-tracker/{f}")
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB, {len(FILES)} plików)")


if __name__ == "__main__":
    main()
