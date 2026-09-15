"""minimap.py: gdzie w klatce jest minimapa i skąd czytać nazwę mapy z kanałem.

Okno minimapy jest przesuwalne i ma dwa stany (zmierzone na zrzutach z 15 września 2026):
- rozwinięte: nagłówek „MINI MAP" + przyciski [−][+][WORLD], pod nim ikona i dwie linie
  tekstu („Hidden Street" / „Free Market<1>"),
- zwinięte: jeden pasek „Hidden Street : Free Market<1>" + te same przyciski.
Może też być schowane całkiem; wtedy oferty idą bez mapy i kanału, to nie jest błąd.

Kotwicą jest przycisk WORLD (ten sam w obu stanach): szukany znormalizowaną korelacją
szablonu w połowie rozdzielczości, potem doprecyzowany w pełnej, a między klatkami
sprawdzany najpierw w ostatnim znanym miejscu. Stan rozróżnia etykieta „MINI MAP" na lewo
od przycisków. Szablony leżą w `minimap_templates.json`, wycięte z próbki referencyjnej
1920×1009 przez:

    .venv\\Scripts\\python minimap.py build samples\\klatka.png
    .venv\\Scripts\\python minimap.py test samples\\*.png     # gdzie i w jakim stanie
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from paths import RES_DIR

TEMPLATE_FILE = RES_DIR / "minimap_templates.json"

# W klatce referencyjnej (okno minimapy rozwinięte, dosunięte do lewego górnego rogu).
WORLD_BOX = (194, 15, 254, 33)  # wnętrze przycisku WORLD, bez ramki
LABEL_BOX = (8, 14, 92, 32)  # etykieta „MINI MAP"
# Prostokąty względem lewego górnego rogu WORLD_BOX.
LABEL_AT = (LABEL_BOX[0] - WORLD_BOX[0], LABEL_BOX[1] - WORLD_BOX[1])
TEXT_OPEN = (84 - WORLD_BOX[0], 50 - WORLD_BOX[1], 262 - WORLD_BOX[0], 108 - WORLD_BOX[1])  # dwie linie pod nagłówkiem
TEXT_COLLAPSED = (-357, -4, -54, 21)  # pasek na lewo od przycisków, do przycisku „−"

WORLD_MIN = 0.85  # korelacja przycisku w pełnej rozdzielczości
WORLD_MIN_HALF = 0.6  # kandydat z połowy rozdzielczości
LABEL_MIN = 0.8
REFINE = 3  # promień doprecyzowania w px


def _ncc_map(g: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Znormalizowana korelacja szablonu t po obrazie g (oba float64) przez FFT.
    Wynik [y, x] dotyczy okna o lewym górnym rogu (x, y). Sumy w float64, bo w float32
    różnice sum kwadratów po całej klatce tracą precyzję."""
    hh, ww = g.shape
    h, w = t.shape
    t0 = t - t.mean()
    tn = np.sqrt((t0**2).sum()) + 1e-6
    corr = np.fft.irfft2(np.fft.rfft2(g) * np.fft.rfft2(t0[::-1, ::-1], s=(hh, ww)), s=(hh, ww))[h - 1 :, w - 1 :]
    cs = np.zeros((hh + 1, ww + 1))
    cs[1:, 1:] = g.cumsum(0).cumsum(1)
    cs2 = np.zeros((hh + 1, ww + 1))
    cs2[1:, 1:] = (g * g).cumsum(0).cumsum(1)

    def win(c: np.ndarray) -> np.ndarray:
        return c[h:, w:] - c[:-h, w:] - c[h:, :-w] + c[:-h, :-w]

    s1 = win(cs)
    var = win(cs2) - s1 * s1 / (h * w)
    return corr / (np.sqrt(np.maximum(var, 1e-6)) * tn)


def _ncc_at(g: np.ndarray, t: np.ndarray, x: int, y: int) -> float:
    h, w = t.shape
    if x < 0 or y < 0 or y + h > g.shape[0] or x + w > g.shape[1]:
        return -1.0
    win = g[y : y + h, x : x + w]
    w0 = win - win.mean()
    t0 = t - t.mean()
    den = np.sqrt((w0**2).sum() * (t0**2).sum()) + 1e-6
    return float((w0 * t0).sum() / den)


def _best_near(g: np.ndarray, t: np.ndarray, x: int, y: int, r: int) -> tuple[int, int, float]:
    best = (x, y, -1.0)
    for yy in range(y - r, y + r + 1):
        for xx in range(x - r, x + r + 1):
            s = _ncc_at(g, t, xx, yy)
            if s > best[2]:
                best = (xx, yy, s)
    return best


def _half(g: np.ndarray) -> np.ndarray:
    h, w = g.shape[0] // 2 * 2, g.shape[1] // 2 * 2
    return g[:h, :w].reshape(h // 2, 2, w // 2, 2).mean(axis=(1, 3))


class Minimap:
    def __init__(self, path: Path = TEMPLATE_FILE):
        d = json.loads(path.read_text(encoding="utf-8"))
        self.world = np.asarray(d["world"], dtype=np.float64)
        self.label = np.asarray(d["label"], dtype=np.float64)
        self.world_half = _half(self.world)
        self.last: tuple[int, int] | None = None

    def find(self, frame: Image.Image) -> tuple[str, tuple[int, int, int, int]] | None:
        """(stan, prostokąt z tekstem mapy) albo None, gdy minimapy nie widać.
        Stan to 'open' (rozwinięta) albo 'collapsed' (zwinięty pasek)."""
        g = np.asarray(frame.convert("L"), dtype=np.float64)
        pos = None
        if self.last is not None:
            x, y, s = _best_near(g, self.world, *self.last, REFINE)
            if s >= WORLD_MIN:
                pos = (x, y)
        if pos is None:
            m = _ncc_map(_half(g), self.world_half)
            y2, x2 = divmod(int(m.argmax()), m.shape[1])
            if m[y2, x2] >= WORLD_MIN_HALF:
                x, y, s = _best_near(g, self.world, 2 * x2, 2 * y2, REFINE)
                if s >= WORLD_MIN:
                    pos = (x, y)
        self.last = pos
        if pos is None:
            return None
        lx, ly, ls = _best_near(g, self.label, pos[0] + LABEL_AT[0], pos[1] + LABEL_AT[1], 2)
        state = "open" if ls >= LABEL_MIN else "collapsed"
        rel = TEXT_OPEN if state == "open" else TEXT_COLLAPSED
        box = (pos[0] + rel[0], pos[1] + rel[1], pos[0] + rel[2], pos[1] + rel[3])
        w, h = frame.size
        box = (max(box[0], 0), max(box[1], 0), min(box[2], w), min(box[3], h))
        if box[2] - box[0] < 20 or box[3] - box[1] < 10:
            return None
        return state, box


def build(sample: Path) -> None:
    g = np.asarray(Image.open(sample).convert("L"))
    d = {
        "source": sample.name,
        "world_box": WORLD_BOX,
        "label_box": LABEL_BOX,
        "world": g[WORLD_BOX[1] : WORLD_BOX[3], WORLD_BOX[0] : WORLD_BOX[2]].tolist(),
        "label": g[LABEL_BOX[1] : LABEL_BOX[3], LABEL_BOX[0] : LABEL_BOX[2]].tolist(),
    }
    TEMPLATE_FILE.write_text(json.dumps(d), encoding="utf-8")
    print(f"zapisano {TEMPLATE_FILE} z {sample}")


def main(argv: list[str]) -> int:
    import glob
    import time

    if len(argv) >= 2 and argv[0] == "build":
        build(Path(argv[1]))
        return 0
    if len(argv) >= 2 and argv[0] == "test":
        mm = Minimap()
        for pat in argv[1:]:
            for f in sorted(glob.glob(pat)):
                mm.last = None  # każdą klatkę szukaj od zera
                t0 = time.time()
                r = mm.find(Image.open(f))
                print(f"{Path(f).name}: {r}  WORLD@{mm.last}  {1000 * (time.time() - t0):.0f} ms")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
