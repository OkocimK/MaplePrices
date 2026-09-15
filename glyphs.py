"""glyphs.py: reading numbers (the price) by glyph matching, without OCR.

Why not OCR: Windows OCR (winocr) reads the names flawlessly, but in some of the price crops
it returns just "mesos" and loses the number, regardless of scale, margin and binarisation. The
game font is fixed (Noto Sans, tabular digits), so every digit can be recognised by comparing
it with an averaged template.

The templates (`price_glyphs.json`) are learned from the crops that winocr read in full:
the number of segments in the image must equal the number of characters in the reading, then
every segment gets a label. A template is a normalised greyscale image, so it works both for
active (dark) and for sold-out (grey) text.

    .venv\\Scripts\\python glyphs.py build crops_dir ocr.json   # build the templates
    .venv\\Scripts\\python glyphs.py test  crops_dir ocr.json   # compare with the winocr readings
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from paths import RES_DIR

GLYPH_FILE = RES_DIR / "price_glyphs.json"
TEMPLATE_H = 24  # height of the common template box (glyphs aligned to the bottom line)
TEMPLATE_W = 14
SPACE_GAP = 5  # this many background columns separate the number from "mesos"
CHARS = "0123456789,"


def _fg_mask(a: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Mask of the text pixels and (background, text) as brightness. Text is darker than background."""
    g = a.mean(axis=2) if a.ndim == 3 else a.astype(float)
    bg = float(np.median(g))
    fg = float(np.percentile(g, 2))
    thr = bg - (bg - fg) * 0.45
    return g < thr, bg, fg


def segments(img: Image.Image) -> tuple[list[tuple[int, int]], np.ndarray]:
    """Returns the list of (x0, x1) of the consecutive glyphs of the number (up to the first
    space) and a brightness map normalised so that background = 0, full text = 1."""
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
                break  # end of the number, "mesos" follows
            x += 1
    return segs, norm


def _crop_glyph(norm: np.ndarray, x0: int, x1: int) -> np.ndarray:
    """Glyph in a TEMPLATE_H×TEMPLATE_W box, aligned to the bottom edge of the text and to the left."""
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
    """Bottom line of the digits (the comma hangs lower, so we take the median over the segments)."""
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
        # comma: its bottom is below the baseline of the digits; we keep the position relative to
        # the baseline to tell it apart from a dot/digit of similar shape
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
        """(number or None, worst match in [0,1], raw character string)."""
        segs, norm = segments(img)
        if not segs:
            return None, 0.0, ""
        feats = _features(norm, segs)
        chars = []
        worst = 1.0
        for (x0, x1), f in zip(segs, feats):
            fv = f.ravel()
            tv = self.templates.reshape(len(self.labels), -1)
            # normalised correlation (robust to contrast: active vs sold-out text)
            fc = fv - fv.mean()
            tc = tv - tv.mean(axis=1, keepdims=True)
            den = np.linalg.norm(fc) * np.linalg.norm(tc, axis=1) + 1e-9
            score = (tc @ fc) / den
            # penalty for a different glyph width (the comma is narrow, digits are wide)
            score = score - 0.08 * np.abs(self.widths - (x1 - x0)) / TEMPLATE_W
            i = int(score.argmax())
            chars.append(self.labels[i])
            worst = min(worst, float(score[i]))
        raw = "".join(chars)
        digits = raw.replace(",", "")
        if not digits.isdigit():
            return None, worst, raw
        # commas must stand every three digits from the right, otherwise the reading is suspect
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
    print(f"templates from {used} rows: " + ", ".join(f"{c}:{len(acc[c])}" for c in labels))
    if missing:
        print("NO samples for:", missing)


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
            print(f"  NONE  {o['row']}  raw={raw!r} conf={conf:.2f} winocr={o.get('price_raw')!r}")
        elif ref is not None and val == ref:
            agree += 1
        else:
            differ += 1
            print(f"  DIFF  {o['row']}  glyph={val:,} conf={conf:.2f} winocr={o.get('price_raw')!r}")
    print(f"agree with winocr: {agree}, differ/missing in winocr: {differ}, unread: {none}, total {len(d)}")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("build", "test"):
        print(__doc__)
        sys.exit(2)
    (build if sys.argv[1] == "build" else test)(Path(sys.argv[2]), Path(sys.argv[3]))
