"""glyphs.py: odczyt liczb (cena) przez dopasowanie glifów, bez OCR.

Dlaczego nie OCR: Windows OCR (winocr) czyta nazwy bezbłędnie, ale w części wycinków cen
zwraca samo „mesos" i gubi liczbę, niezależnie od skali, marginesu i binaryzacji. Font gry
jest stały (Noto Sans, cyfry tabelaryczne), więc każdą cyfrę da się rozpoznać porównując
z uśrednionym wzorcem.

Wzorce (`price_glyphs.json`) uczy się z wycinków, które winocr przeczytał w całości:
liczba segmentów w obrazie musi równać się liczbie znaków w odczycie, wtedy każdy segment
dostaje etykietę. Wzorzec to znormalizowany obraz w skali szarości, więc działa i dla
aktywnego (ciemnego), i dla wykupionego (szarego) tekstu.

    .venv\\Scripts\\python glyphs.py build crops_dir ocr.json   # zbuduj wzorce
    .venv\\Scripts\\python glyphs.py test  crops_dir ocr.json   # porównaj z odczytami winocr
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
GLYPH_FILE = HERE / "price_glyphs.json"
TEMPLATE_H = 24  # wysokość wspólnego pudełka wzorca (glify wyrównane do dolnej linii)
TEMPLATE_W = 14
SPACE_GAP = 5  # tyle kolumn tła oddziela liczbę od „mesos"
CHARS = "0123456789,"


def _fg_mask(a: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Maska pikseli tekstu i (tło, tekst) jako jasności. Tekst jest ciemniejszy od tła."""
    g = a.mean(axis=2) if a.ndim == 3 else a.astype(float)
    bg = float(np.median(g))
    fg = float(np.percentile(g, 2))
    thr = bg - (bg - fg) * 0.45
    return g < thr, bg, fg


def segments(img: Image.Image) -> tuple[list[tuple[int, int]], np.ndarray]:
    """Zwraca listę (x0, x1) kolejnych glifów liczby (do pierwszej spacji) i mapę jasności
    znormalizowaną tak, że tło = 0, pełny tekst = 1."""
    a = np.asarray(img.convert("RGB")).astype(float)
    mask, bg, fg = _fg_mask(a)
    g = a.mean(axis=2)
    norm = np.clip((bg - g) / max(bg - fg, 1.0), 0.0, 1.0)
    cols = mask.any(axis=0)
    segs: list[tuple[int, int]] = []
    x = 0
    w = len(cols)
    gap = 0
    while x < w:
        if cols[x]:
            x0 = x
            while x < w and cols[x]:
                x += 1
            segs.append((x0, x))
            gap = 0
        else:
            gap += 1
            if segs and gap >= SPACE_GAP:
                break  # koniec liczby, dalej jest „mesos"
            x += 1
    return segs, norm


def _crop_glyph(norm: np.ndarray, x0: int, x1: int) -> np.ndarray:
    """Glif w pudełku TEMPLATE_H×TEMPLATE_W, wyrównany do dolnej krawędzi tekstu i do lewej."""
    col = norm[:, x0:x1]
    rows = np.where((col > 0.45).any(axis=1))[0]
    if len(rows) == 0:
        return np.zeros((TEMPLATE_H, TEMPLATE_W))
    bottom = rows.max() + 1
    top = max(0, bottom - TEMPLATE_H)
    box = np.zeros((TEMPLATE_H, TEMPLATE_W))
    piece = col[top:bottom, : TEMPLATE_W]
    box[TEMPLATE_H - piece.shape[0] :, : piece.shape[1]] = piece
    return box


def _baseline(norm: np.ndarray, segs: list[tuple[int, int]]) -> int:
    """Dolna linia cyfr (przecinek wisi niżej, więc bierzemy medianę po segmentach)."""
    bottoms = []
    for x0, x1 in segs:
        rows = np.where((norm[:, x0:x1] > 0.45).any(axis=1))[0]
        if len(rows):
            bottoms.append(rows.max() + 1)
    return int(np.median(bottoms)) if bottoms else norm.shape[0]


def _features(norm: np.ndarray, segs: list[tuple[int, int]]) -> list[np.ndarray]:
    base = _baseline(norm, segs)
    out = []
    for x0, x1 in segs:
        col = norm[:, x0:x1]
        rows = np.where((col > 0.45).any(axis=1))[0]
        bottom = rows.max() + 1 if len(rows) else base
        # przecinek: dół poniżej linii bazowej cyfr; trzymamy pozycję względem bazy, żeby
        # odróżnić go od kropki/cyfry o podobnym kształcie
        top = max(0, base - TEMPLATE_H + 4)
        box = np.zeros((TEMPLATE_H, TEMPLATE_W))
        piece = col[top : base + 4, :TEMPLATE_W]
        box[: piece.shape[0], : piece.shape[1]] = piece
        out.append(box)
    return out


class Glyphs:
    def __init__(self, path: Path = GLYPH_FILE):
        d = json.loads(path.read_text(encoding="utf-8"))
        self.labels = list(d["labels"])
        self.templates = np.array(d["templates"], dtype=float)  # (n, H, W)
        self.widths = np.array(d["widths"], dtype=float)

    def read(self, img: Image.Image) -> tuple[int | None, float, str]:
        """(liczba albo None, najgorsze dopasowanie w [0,1], surowy ciąg znaków)."""
        segs, norm = segments(img)
        if not segs:
            return None, 0.0, ""
        feats = _features(norm, segs)
        chars = []
        worst = 1.0
        for (x0, x1), f in zip(segs, feats):
            fv = f.ravel()
            tv = self.templates.reshape(len(self.labels), -1)
            # korelacja znormalizowana (odporna na kontrast: aktywny vs wykupiony tekst)
            fc = fv - fv.mean()
            tc = tv - tv.mean(axis=1, keepdims=True)
            den = np.linalg.norm(fc) * np.linalg.norm(tc, axis=1) + 1e-9
            score = (tc @ fc) / den
            # kara za inną szerokość glifu (przecinek jest wąski, cyfry szerokie)
            score = score - 0.08 * np.abs(self.widths - (x1 - x0)) / TEMPLATE_W
            i = int(score.argmax())
            chars.append(self.labels[i])
            worst = min(worst, float(score[i]))
        raw = "".join(chars)
        digits = raw.replace(",", "")
        if not digits.isdigit():
            return None, worst, raw
        # przecinki muszą stać co trzy cyfry od prawej, inaczej odczyt jest podejrzany
        expect = f"{int(digits):,}"
        if raw != expect:
            worst = min(worst, 0.0)
        return int(digits), worst, raw


def build(crops: Path, ocr_json: Path) -> None:
    d = json.loads(ocr_json.read_text(encoding="utf-8"))
    acc: dict[str, list[np.ndarray]] = {c: [] for c in CHARS}
    wid: dict[str, list[int]] = {c: [] for c in CHARS}
    used = 0
    for o in d:
        raw = (o.get("price_raw") or "").split("mes")[0].strip()
        if not raw or not all(c in CHARS for c in raw):
            continue
        img = Image.open(crops / f"{o['row']}-price.png")
        segs, norm = segments(img)
        if len(segs) != len(raw):
            continue
        for (x0, x1), f, c in zip(segs, _features(norm, segs), raw):
            acc[c].append(f)
            wid[c].append(x1 - x0)
        used += 1
    labels = [c for c in CHARS if acc[c]]
    missing = [c for c in CHARS if not acc[c]]
    templates = [np.mean(acc[c], axis=0).tolist() for c in labels]
    widths = [float(np.median(wid[c])) for c in labels]
    GLYPH_FILE.write_text(json.dumps({"labels": labels, "templates": templates, "widths": widths}), encoding="utf-8")
    print(f"wzorce z {used} wierszy: " + ", ".join(f"{c}:{len(acc[c])}" for c in labels))
    if missing:
        print("BRAK próbek dla:", missing)


def test(crops: Path, ocr_json: Path) -> None:
    g = Glyphs()
    d = json.loads(ocr_json.read_text(encoding="utf-8"))
    agree = differ = none = 0
    for o in d:
        img = Image.open(crops / f"{o['row']}-price.png")
        val, conf, raw = g.read(img)
        ref = o.get("price")
        if val is None:
            none += 1
            print(f"  BRAK  {o['row']}  raw={raw!r} conf={conf:.2f} winocr={o.get('price_raw')!r}")
        elif ref is not None and val == ref:
            agree += 1
        else:
            differ += 1
            print(f"  ROZNI {o['row']}  glyph={val:,} conf={conf:.2f} winocr={o.get('price_raw')!r}")
    print(f"zgodne z winocr: {agree}, różne/brak w winocr: {differ}, nieodczytane: {none}, razem {len(d)}")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("build", "test"):
        print(__doc__)
        sys.exit(2)
    (build if sys.argv[1] == "build" else test)(Path(sys.argv[2]), Path(sys.argv[3]))
