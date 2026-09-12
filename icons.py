"""icons.py: procent scrolla z ikony przez dopasowanie wzorca, także dla wyblakłych (wykupionych).

Średni kolor nasyconych pikseli (wcześniejsze `names.pct_from_icon`) wystarczał dla aktywnych
złotych i czerwonych ikon, ale fioletowe (30%) i brązowe (70%) mają mało nasyconych pikseli,
a wykupione wiersze są wyblakłe i kolor się zlewa. Grafika ikony jest jednak identyczna dla
każdego procentu, więc korelacja map chromy (R-G, G-B) z wzorcem rozdziela klasy pewnie.
Korelacja samej jasności nie działa (marginesy 0,02), bo kształt jest wspólny.

Wzorce (`icon_templates.json`) buduje się z żywej bazy: wiersze, w których procent stoi
w tekście, dają etykietę, a ikona z wycinka daje obraz. Osobno aktywne i wykupione.

    .venv\\Scripts\\python icons.py build data/prices.sqlite   # zbuduj wzorce
    .venv\\Scripts\\python icons.py test  data/prices.sqlite   # leave-one-out na tych samych danych
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
TEMPLATE_FILE = HERE / "icon_templates.json"
ICON_IN_CROP = (0, 4, 70, 72)  # ikona wewnątrz wycinka wiersza zapisanego przez tracker
SIZE = (35, 34)  # wzorzec w połowie rozdzielczości, wystarcza i mniej waży
MIN_SCORE = 0.80
MIN_MARGIN = 0.05  # drugi najlepszy musi być wyraźnie gorszy, inaczej „nie wiem"
# Na 274 wierszach z procentem w tekście (leave-one-out): 252 dobrze, 4 „nie wiem", 18 niezgodnych
# z pewnością 0,99, czyli to etykiety były złe (ikona z innego wiersza, patrz PLAN.md).


def _vec(icon: Image.Image) -> np.ndarray:
    """Cechy chromatyczne (R-G, G-B) zamiast jasności: kształt ikony jest ten sam dla każdego
    procentu, różni się tylko barwa, a wyblaknięcie skaluje chromę liniowo, co korelacja znosi."""
    a = np.asarray(icon.convert("RGB").resize(SIZE, Image.BILINEAR), dtype=float)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    v = np.concatenate([(r - g).ravel(), (g - b).ravel()])
    v = v - v.mean()
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


class Icons:
    def __init__(self, path: Path = TEMPLATE_FILE):
        d = json.loads(path.read_text(encoding="utf-8"))
        self.labels = [tuple(x) for x in d["labels"]]  # (sold, pct)
        self.vecs = np.array(d["vecs"], dtype=float)

    def pct(self, icon: Image.Image, sold: bool, allowed: set[int] | None = None) -> int | None:
        """`allowed` to zbiór procentów dopuszczalnych dla tej nazwy (dark scroll: {30, 70}).
        Gdy najlepsza klasa jest spoza zbioru, ikona jest niespójna z tekstem (jeszcze nie
        doładowana po otwarciu sklepu), więc odpowiedź brzmi „nie wiem", a nie „najlepsza
        z dozwolonych"."""
        v = _vec(icon)
        scores = self.vecs @ v
        idx = [i for i, (s, _) in enumerate(self.labels) if s == int(sold)]
        if not idx:
            return None
        order = sorted(idx, key=lambda i: -scores[i])
        best = order[0]
        if scores[best] < MIN_SCORE:
            return None
        # margines liczony do najlepszego wzorca INNEJ klasy
        other = next((i for i in order[1:] if self.labels[i][1] != self.labels[best][1]), None)
        if other is not None and scores[best] - scores[other] < MIN_MARGIN:
            return None
        pct = self.labels[best][1]
        if allowed is not None and pct not in allowed:
            return None
        return pct


def _samples(db_path: Path) -> list[tuple[int, int, int, Image.Image]]:
    db = sqlite3.connect(str(db_path))
    crops = db_path.with_name(db_path.stem + "_crops")
    out = []
    for oid, pct, sold in db.execute(
        "SELECT id, pct, sold FROM obs WHERE scroll=1 AND pct IS NOT NULL AND instr(raw_name, '%')>0"
    ):
        p = crops / f"{oid}.png"
        if p.exists():
            out.append((oid, int(pct), int(sold), Image.open(p).crop(ICON_IN_CROP)))
    return out


def build(db_path: Path) -> None:
    """Dwa przebiegi: po pierwszym wyrzuca próbki, które słabo pasują do własnej klasy
    (ikona jeszcze nie doładowana, więc z innego wiersza), i liczy wzorce jeszcze raz."""
    acc: dict[tuple[int, int], list[np.ndarray]] = {}
    for _, pct, sold, icon in _samples(db_path):
        acc.setdefault((sold, pct), []).append(_vec(icon))
    labels, vecs = [], []
    dropped = 0
    for k in sorted(acc):
        m = np.mean(acc[k], axis=0)
        m = m / np.linalg.norm(m)
        keep = [v for v in acc[k] if float(m @ v) >= 0.85] or acc[k]
        dropped += len(acc[k]) - len(keep)
        m = np.mean(keep, axis=0)
        m = m / np.linalg.norm(m)
        labels.append(list(k))
        vecs.append(m.tolist())
        print(f"sold={k[0]} pct={k[1]:3}: {len(keep)} próbek (odrzucone {len(acc[k]) - len(keep)})")
    print(f"odrzucone łącznie: {dropped} (ikony niezgodne z tekstem)")
    TEMPLATE_FILE.write_text(json.dumps({"labels": labels, "vecs": vecs}), encoding="utf-8")


def test(db_path: Path) -> None:
    samples = _samples(db_path)
    by_class: dict[tuple[int, int], list[np.ndarray]] = {}
    for _, pct, sold, icon in samples:
        by_class.setdefault((sold, pct), []).append(_vec(icon))
    ok = bad = unk = 0
    for oid, pct, sold, icon in samples:
        v = _vec(icon)
        best, best_s, second_s = None, -1.0, -1.0
        for k, vs in by_class.items():
            if k[0] != sold:
                continue
            # leave-one-out: własny wektor wyłączony ze średniej klasy
            others = [u for u in vs if u is not v and not np.array_equal(u, v)] if k[1] == pct else vs
            if not others:
                continue
            m = np.mean(others, axis=0)
            s = float(m @ v / np.linalg.norm(m))
            if s > best_s:
                if best is not None and best != k[1]:
                    second_s = best_s
                best, best_s = k[1], s
            elif k[1] != best and s > second_s:
                second_s = s
        if best_s < MIN_SCORE or best_s - second_s < MIN_MARGIN:
            unk += 1
            print(f"  ?  id={oid} sold={sold} pct={pct} best={best} s={best_s:.3f} margin={best_s - second_s:.3f}")
        elif best == pct:
            ok += 1
        else:
            bad += 1
            print(f"  ZLE id={oid} sold={sold} pct={pct} -> {best} s={best_s:.3f} margin={best_s - second_s:.3f}")
    print(f"dobrze {ok}, źle {bad}, nie wiem {unk}, razem {len(samples)}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("build", "test"):
        print(__doc__)
        sys.exit(2)
    (build if sys.argv[1] == "build" else test)(Path(sys.argv[2]))
