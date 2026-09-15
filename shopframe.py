"""shopframe.py: z klatki gry wycina wiersze okna sklepu (etap M1 z PLAN.md).

Geometria wnętrza okna sklepu jest stała, zmierzona na 52 próbkach 1920×1009 (obszar klienta
okna gry), ale samo okno nie musi stać w jednym miejscu: u znajomych klient gry ma inną
wysokość (1920×1080 na pełnym ekranie), a okna UI można przesuwać. Dlatego okno sklepu jest
w każdej klatce lokalizowane: lista ma 5 separatorów (jasne pasma 4 px z ciemną kreską tuż
nad nimi) co 75 px, i ta sygnatura jest szukana w całej klatce. Wynik to przesunięcie
(dx, dy) względem geometrii referencyjnej; wszystkie prostokąty poniżej są w układzie
referencyjnym i `ShopFrame.box` przesuwa je do klatki.

Skala UI musi się zgadzać z referencją (szerokość klienta 1920 px). Przy innej skali
separatory nie trafiają w skok 75 px i sklep nie jest wykrywany; tracker o tym uprzedza.

Nic tu nie czyta tekstu, to robi recognizer; ten moduł tylko odpowiada „czy sklep jest
otwarty", „które wiersze są puste", „które są wykupione" i oddaje wycinki nazwy, ceny i ilości.

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

REF_W, REF_H = 1920, 1009  # klatka referencyjna, w niej przesunięcie to (0, 0)

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

# Sygnatura separatora (zmierzona na próbkach): pasmo 4..5 px o jasności 238..255 w wierszach
# ROW_TOP-4..ROW_TOP, nad nim 2..3 px wyżej ciemna kreska 120..190; tło wiersza pod spodem 204,
# podświetlonego 230. Pasmo zaczyna się przy x=475 (na lewo od niego ciemny piksel ramki) i ciągnie
# do x=862, ale ciemna kreska nad nim dopiero od x=481, bo wcześniej jest ramka ikony; dlatego do
# wykrycia służy para pasmo+kreska w kolumnach tekstu, a do lewej krawędzi samo pasmo.
SEP_Y = ROW_TOP - 2  # 450: wiersz, w którym na próbkach referencyjnych wszystkie 5 pasm jest „linią"
SEP_LEFT = 475
SEP_EVAL = (560, 840)  # kolumny, w których liczony jest udział „linii" (tekst, bez ikony i suwaka)
SEP_BRIGHT = 232
SEP_DARK = 200
SEP_MIN_FRAC = 0.6  # tyle kolumn okna SEP_EVAL musi być linią, żeby separator się liczył
SEP_MIN_HITS = ROWS - 1  # kursor gry albo ogonki liter potrafią zepsuć jeden separator
SEP_EDGE_RUN = 60  # tyle kolejnych kolumn z jasnym pasmem zaczyna lewą krawędź listy

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
    origin: tuple[int, int] = (0, 0)  # przesunięcie okna sklepu względem geometrii referencyjnej

    def box(self, y0: int, rel: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Prostokąt pola wiersza (jak NAME, PRICE) w tej klatce; y0 to góra wiersza w układzie
        referencyjnym, czyli ROW_TOP + k * ROW_PITCH."""
        return shift(_box(y0, rel), self.origin)


def shift(box: tuple[int, int, int, int], origin: tuple[int, int]) -> tuple[int, int, int, int]:
    dx, dy = origin
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def _box(y0: int, rel: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, dy0, x1, dy1 = rel
    return (x0, y0 + dy0, x1, y0 + dy1)


def _counts(a: np.ndarray) -> tuple[int, int]:
    """Ile pikseli aktywnego (ciemnego) i wykupionego (szarego) tekstu w wycinku."""
    dark = int((a < ACTIVE_MAX).all(axis=2).sum())
    grey = int(((a >= SOLD_LO) & (a <= SOLD_HI)).all(axis=2).sum())
    return dark, grey


def _line_mask(g: np.ndarray) -> np.ndarray:
    """Piksel jest „linią separatora", gdy on i piksel pod nim są jasne, a 2 albo 3 px wyżej
    jest ciemna kreska."""
    h = g.shape[0]
    bright = g >= SEP_BRIGHT
    dark = g <= SEP_DARK
    line = np.zeros_like(bright)
    line[3 : h - 1] = bright[3 : h - 1] & bright[4:h] & (dark[1 : h - 3] | dark[0 : h - 4])
    return line


def locate(a: np.ndarray) -> tuple[int, int] | None:
    """Szuka listy sklepu w całej klatce. Zwraca przesunięcie (dx, dy) geometrii referencyjnej
    albo None, gdy sklep nie jest otwarty. Około 100 ms na klatkę 1920×1009."""
    g = a.min(axis=2)
    h, w = g.shape
    span = (ROWS - 1) * ROW_PITCH + 2
    ew = SEP_EVAL[1] - SEP_EVAL[0]
    if h < span + 4 or w < ew + 1:
        return None
    line = _line_mask(g)
    cs = np.zeros((h, w + 1), dtype=np.int32)
    np.cumsum(line, axis=1, out=cs[:, 1:])
    frac = (cs[:, ew:] - cs[:, :-ew]) * (1.0 / ew)  # frac[y, x]: udział linii w kolumnach x..x+ew
    tol = frac.copy()  # tolerancja ±1 px w pionie na każdy separator z osobna
    np.maximum(tol[1:], frac[:-1], out=tol[1:])
    np.maximum(tol[:-1], frac[1:], out=tol[:-1])
    n = h - span
    hits = np.zeros((n, frac.shape[1]), dtype=np.int16)
    exact = np.zeros((n, frac.shape[1]), dtype=np.float32)
    for k in range(ROWS):
        sl = slice(k * ROW_PITCH, k * ROW_PITCH + n)
        hits += tol[sl] >= SEP_MIN_FRAC
        exact += frac[sl]
    score = hits + exact * 0.1  # dokładne trafienie rozstrzyga remis na plateau
    y, x = divmod(int(score.argmax()), score.shape[1])
    if hits[y, x] < SEP_MIN_HITS:
        return None
    # Lewa krawędź: pierwsza kolumna, od której SEP_EDGE_RUN kolejnych kolumn ma jasne pasmo
    # w co najmniej SEP_MIN_HITS separatorach. Okno SEP_EVAL zaczyna się 85 px za krawędzią,
    # a udział >= 0.6 pozwala oknu leżeć do 112 px w obie strony od tego miejsca, stąd zakres.
    cols = np.zeros(w, dtype=np.int16)
    for k in range(ROWS):
        yy = y + k * ROW_PITCH
        cols += (g[max(yy - 1, 0) : yy + 3] >= SEP_BRIGHT).any(axis=0)
    ok = np.zeros(w + 1, dtype=np.int32)
    np.cumsum(cols >= SEP_MIN_HITS, out=ok[1:])
    lo = max(x - 120, 0)
    hi = min(x + 120, w - SEP_EDGE_RUN)
    for i in range(lo, hi):
        if ok[i + SEP_EDGE_RUN] - ok[i] == SEP_EDGE_RUN:
            return (i - SEP_LEFT, y - SEP_Y)
    return None


def parse(im: Image.Image) -> ShopFrame:
    im = im.convert("RGB")
    a = np.asarray(im)
    origin = locate(a)
    if origin is None:
        return ShopFrame(False, None, [])
    fr = ShopFrame(True, None, [], origin)
    for k in range(ROWS):
        y0 = ROW_TOP + k * ROW_PITCH
        nb = fr.box(y0, NAME)
        dark, grey = _counts(a[nb[1] : nb[3], nb[0] : nb[2]])
        empty = dark + grey < MIN_TEXT_PX
        sold = (not empty) and grey > dark
        fr.rows.append(
            Row(
                index=k,
                empty=empty,
                sold=sold,
                name=im.crop(nb),
                price=im.crop(fr.box(y0, PRICE)),
                qty=im.crop(fr.box(y0, QTY)),
                icon=im.crop(fr.box(y0, ICON)),
            )
        )
    fr.title = im.crop(shift(TITLE, origin))
    return fr


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
        print(f"{stem}: otwarty @{fr.origin}, wiersze [{flags}]  (a=aktywny s=wykupiony .=pusty)")
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
