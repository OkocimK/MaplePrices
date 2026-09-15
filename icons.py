"""icons.py: scroll percent from the icon by template matching, also for faded (sold-out) ones.

The mean colour of saturated pixels (the earlier `names.pct_from_icon`) was enough for active
golden and red icons, but the purple (30%) and brown (70%) ones have few saturated pixels,
and sold-out rows are faded, so the colour blurs together. The icon artwork is however identical
for every percent, so correlating chroma maps (R-G, G-B) with a template separates the classes
reliably. Correlating brightness alone does not work (margins of 0.02), because the shape is shared.

The templates (`icon_templates.json`) are built from the live database: rows where the percent
is present in the text give the label, and the icon from the crop gives the image. Active and
sold-out separately.

    .venv\\Scripts\\python icons.py build data/prices.sqlite   # build the templates
    .venv\\Scripts\\python icons.py test  data/prices.sqlite   # leave-one-out on the same data
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from paths import RES_DIR

TEMPLATE_FILE = RES_DIR / "icon_templates.json"
ICON_IN_CROP = (0, 4, 70, 72)  # the icon inside the row crop saved by the tracker
SIZE = (35, 34)  # template at half resolution, enough and weighs less
MIN_SCORE = 0.80
MIN_MARGIN = 0.05  # the second best must be clearly worse, otherwise "don't know"
# On 274 rows with the percent in the text (leave-one-out): 252 correct, 4 "don't know", 18 mismatched
# with confidence 0.99, i.e. the labels were wrong (icon from another row, see PLAN.md, design
# notes kept outside the repo).


def _vec(icon: Image.Image) -> np.ndarray:
    """Chroma features (R-G, G-B) instead of brightness: the icon shape is the same for every
    percent, only the hue differs, and fading scales the chroma linearly, which correlation cancels."""
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
        """`allowed` is the set of percents admissible for this name (dark scroll: {30, 70}).
        When the best class is outside the set, the icon is inconsistent with the text (not yet
        loaded after opening the shop), so the answer is "don't know", not "the best of the
        allowed ones"."""
        v = _vec(icon)
        scores = self.vecs @ v
        idx = [i for i, (s, _) in enumerate(self.labels) if s == int(sold)]
        if not idx:
            return None
        order = sorted(idx, key=lambda i: -scores[i])
        best = order[0]
        if scores[best] < MIN_SCORE:
            return None
        # margin measured against the best template of ANOTHER class
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
    """Two passes: after the first it drops the samples that match their own class poorly
    (icon not yet loaded, so from another row), and computes the templates once more."""
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
        print(f"sold={k[0]} pct={k[1]:3}: {len(keep)} samples (dropped {len(acc[k]) - len(keep)})")
    print(f"dropped in total: {dropped} (icons inconsistent with the text)")
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
            # leave-one-out: the own vector is excluded from the class mean
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
            print(f"  BAD id={oid} sold={sold} pct={pct} -> {best} s={best_s:.3f} margin={best_s - second_s:.3f}")
    print(f"correct {ok}, wrong {bad}, don't know {unk}, total {len(samples)}")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("build", "test"):
        print(__doc__)
        sys.exit(2)
    (build if sys.argv[1] == "build" else test)(Path(sys.argv[2]))
