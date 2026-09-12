"""shopframe.py: z klatki gry wycina wiersze okna sklepu (etap M1 z PLAN.md).

Geometria jest stała, zmierzona na 52 próbkach 1920×1009 (obszar klienta okna gry):
okno sklepu zawsze otwiera się w tym samym miejscu. Nic tu nie czyta tekstu, to robi
recognizer; ten moduł tylko odpowiada „czy sklep jest otwarty", „które wiersze są puste",
„które są wykupione" i oddaje wycinki nazwy, ceny i ilości.

Użycie z konsoli, żeby obejrzeć wycinki ze wszystkich próbek:

    .venv\\Scripts\\python shopframe.py samples\\*.png --dump out_dir
"""

from __future__ import annotations

import argparse
import glob
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

FRAME_W, FRAME_H = 1920, 1009

# Pasek tytułu sklepu: „voda's Hired Merchant : S> ..." po lewej, licznik czasu po prawej.
TITLE = (500, 176, 1330, 204)
TIMER = (1330, 176, 1410, 204)

# Lista: 5 wierszy, skok 75 px, pierwszy separator y=452.
ROWS = 5
ROW_PITCH = 75
ROW_TOP = 452
ICON = (482, 4, 552, 72)  # względem góry wiersza: x0, dy0, x1, dy1
NAME = (555, 3, 848, 30)
PRICE = (582, 38, 848, 65)  # bez monety (x 555..580)
QTY = (482, 48, 512, 72)  # cyfra ilości w lewym dolnym rogu ikony

# Kolory tekstu (RGB): aktywny ~ (42,44,46), wykupiony ~ (131,131,131).
ACTIVE_MAX = 90  # piksel „ciemny" gdy każdy kanał poniżej
SOLD_LO, SOLD_HI = 110, 160  # piksel „szary" gdy wszystkie kanały w tym przedziale
MIN_TEXT_PX = 60  # mniej ciemnych/szarych pikseli w polu nazwy = wiersz pusty


@dataclass
class Row:
    index: int
    empty: bool
    sold: bool
    name: Image.Image
    price: Image.Image
    qty: Image.Image
    icon: Image.Image


@dataclass
class ShopFrame:
    open: bool
    title: Image.Image | None
    rows: list[Row]


def _box(y0: int, rel: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, dy0, x1, dy1 = rel
    return (x0, y0 + dy0, x1, y0 + dy1)


def _counts(a: np.ndarray) -> tuple[int, int]:
    """Ile pikseli aktywnego (ciemnego) i wykupionego (szarego) tekstu w wycinku."""
    dark = int((a < ACTIVE_MAX).all(axis=2).sum())
    grey = int(((a >= SOLD_LO) & (a <= SOLD_HI)).all(axis=2).sum())
    return dark, grey


def shop_open(a: np.ndarray) -> bool:
    """Sklep otwarty = separatory listy stoją na swoich miejscach (jasne poziome linie)."""
    # Tło wiersza ma jasność ~215, separator ~245, ale kursor gry albo ogonki liter potrafią
    # ściągnąć jeden separator do ~235, więc próg jest luźny i wystarczą 4 z 5.
    band = a[440:830, 560:840].mean(axis=(1, 2))
    hits = 0
    for k in range(ROWS):
        s = ROW_TOP + k * ROW_PITCH - 440
        if band[s - 1 : s + 3].max() > 230:
            hits += 1
    return hits >= ROWS - 1


def parse(im: Image.Image) -> ShopFrame:
    if im.size != (FRAME_W, FRAME_H):
        raise ValueError(f"klatka {im.size}, oczekiwana {(FRAME_W, FRAME_H)}; inna rozdzielczość gry?")
    im = im.convert("RGB")
    a = np.asarray(im)
    if not shop_open(a):
        return ShopFrame(False, None, [])
    rows: list[Row] = []
    for k in range(ROWS):
        y0 = ROW_TOP + k * ROW_PITCH
        nb = _box(y0, NAME)
        dark, grey = _counts(a[nb[1] : nb[3], nb[0] : nb[2]])
        empty = dark + grey < MIN_TEXT_PX
        sold = (not empty) and grey > dark
        rows.append(
            Row(
                index=k,
                empty=empty,
                sold=sold,
                name=im.crop(nb),
                price=im.crop(_box(y0, PRICE)),
                qty=im.crop(_box(y0, QTY)),
                icon=im.crop(_box(y0, ICON)),
            )
        )
    return ShopFrame(True, im.crop(TITLE), rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="klatki PNG (glob dozwolony)")
    ap.add_argument("--dump", help="folder na wycinki nazwa/cena/ilość do obejrzenia")
    args = ap.parse_args()
    files = [f for pat in args.files for f in sorted(glob.glob(pat))]
    out = Path(args.dump) if args.dump else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    for f in files:
        fr = parse(Image.open(f))
        stem = Path(f).stem
        if not fr.open:
            print(f"{stem}: sklep zamknięty")
            continue
        flags = "".join("." if r.empty else ("s" if r.sold else "a") for r in fr.rows)
        print(f"{stem}: otwarty, wiersze [{flags}]  (a=aktywny s=wykupiony .=pusty)")
        if out:
            fr.title.save(out / f"{stem}-title.png")
            for r in fr.rows:
                if r.empty:
                    continue
                tag = "sold" if r.sold else "act"
                r.name.save(out / f"{stem}-r{r.index}-{tag}-name.png")
                r.price.save(out / f"{stem}-r{r.index}-{tag}-price.png")
                r.qty.save(out / f"{stem}-r{r.index}-{tag}-qty.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
