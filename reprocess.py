"""reprocess.py: przelicza niepełne obserwacje scrolli w bazie z zapisanych wycinków.

Po zmianie parsera nazw albo wzorców ikon nie trzeba chodzić po FM od nowa: każdy wiersz
ma wycinek (ikona + nazwa + cena), więc nazwę i procent da się odczytać jeszcze raz.
Cena zostaje, jak była (glify były pewne od początku).

    .venv\\Scripts\\python reprocess.py data/prices.sqlite          # tylko niepełne
    .venv\\Scripts\\python reprocess.py data/prices.sqlite --all    # wszystkie scrolle
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from PIL import Image

import names
import recognize

NAME_IN_CROP = (73, 3, 366, 30)  # = shopframe.NAME przesunięte o początek wycinka (x=482, y=0)
ICON_IN_CROP = (0, 4, 70, 72)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    db_path = Path(sys.argv[1])
    only_incomplete = "--all" not in sys.argv
    crops = db_path.with_name(db_path.stem + "_crops")
    db = sqlite3.connect(str(db_path))
    q = "SELECT id, name, raw_name, sold FROM obs WHERE scroll=1" + (" AND complete=0" if only_incomplete else "")
    rows = db.execute(q).fetchall()
    fixed = same = worse = 0
    for oid, old, old_raw, sold in rows:
        p = crops / f"{oid}.png"
        if not p.exists():
            continue
        crop = Image.open(p)
        raw = recognize.ocr_name(crop.crop(NAME_IN_CROP), bool(sold))
        sc = names.parse(raw, crop.crop(ICON_IN_CROP), bool(sold))
        if sc is None:
            worse += 1
            print(f"  {oid}: parser odrzucił {raw!r} (było {old!r}), zostawiam")
            continue
        complete = int(sc.complete and sc.score >= 0.8)
        if sc.name == old:
            same += 1
            continue
        if old.count("?") < sc.name.count("?"):
            worse += 1
            print(f"  {oid}: nowy odczyt gorszy {sc.name!r} (było {old!r}), zostawiam")
            continue
        db.execute(
            "UPDATE obs SET name=?, raw_name=?, complete=?, dark=?, equip=?, stat=?, pct=?, conf=? WHERE id=?",
            (sc.name, raw, complete, int(sc.dark), sc.equip, sc.stat, sc.pct, sc.score, oid),
        )
        fixed += 1
        print(f"  {oid}: {old!r} -> {sc.name!r}")
    db.commit()
    print(f"poprawione {fixed}, bez zmian {same}, gorsze/odrzucone {worse}, razem {len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
