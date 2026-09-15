"""shopframe.py: cuts the rows of the shop window out of a game frame (stage M1 in PLAN.md).

The geometry of the shop window's interior is fixed, measured on 52 samples of 1920×1009 (the
client area of the game window), but the window itself does not have to stay in one place: on
friends' machines the game client has a different height (1920×1080 in full screen), and UI
windows can be moved. So the shop window is located in every frame: the list has 5 separators
(bright 4 px bands with a dark stroke just above them) every 75 px, and this signature is searched
for in the whole frame. The result is the offset (dx, dy) relative to the reference geometry;
all the rectangles below are in the reference layout and `ShopFrame.box` shifts them into
the frame.

The UI scale has to match the reference (client width 1920 px). At a different scale the
separators do not land on the 75 px pitch and the shop is not detected; the tracker warns about it.

Nothing here reads text, the recognizer does that; this module only answers "is the shop
open", "which rows are empty", "which are sold out" and hands out crops of name, price and quantity.

Usage from the console, to look at the crops from all samples:

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

REF_W, REF_H = 1920, 1009  # reference frame, in it the offset is (0, 0)

# Shop title bar: "voda's Hired Merchant : S> ..." on the left, the timer on the right.
TITLE = (500, 176, 1330, 204)
TIMER = (1330, 176, 1410, 204)

# List: 5 rows, pitch 75 px, first separator at y=452.
ROWS = 5
ROW_PITCH = 75
ROW_TOP = 452
ICON = (482, 4, 552, 72)  # relative to the top of the row: x0, dy0, x1, dy1
NAME = (555, 3, 848, 30)
PRICE = (582, 38, 848, 65)  # without the coin (x 555..580)
QTY = (482, 48, 512, 72)  # quantity digit in the bottom left corner of the icon

# Separator signature (measured on the samples): a 4..5 px band of brightness 238..255 in rows
# ROW_TOP-4..ROW_TOP, above it 2..3 px higher a dark stroke 120..190; the row background below is 204,
# highlighted 230. The band starts at x=475 (to its left a dark frame pixel) and runs
# to x=862, but the dark stroke above it only starts at x=481, because before that is the icon frame;
# therefore the band+stroke pair in the text columns is used for detection, and the band alone for the left edge.
SEP_Y = ROW_TOP - 2  # 450: the row in which on the reference samples all 5 bands are a "line"
SEP_LEFT = 475
SEP_EVAL = (560, 840)  # columns in which the "line" fraction is computed (text, without icon and scrollbar)
SEP_BRIGHT = 232
SEP_DARK = 200
SEP_MIN_FRAC = 0.6  # this many columns of the SEP_EVAL window must be a line for the separator to count
SEP_MIN_HITS = ROWS - 1  # the game cursor or letter descenders can spoil one separator
SEP_EDGE_RUN = 60  # this many consecutive columns with a bright band start the left edge of the list

# Text colours (RGB): active ~ (42,44,46), sold out ~ (131,131,131).
ACTIVE_MAX = 90  # a pixel is "dark" when every channel is below
SOLD_LO, SOLD_HI = 110, 160  # a pixel is "grey" when all channels are within this range
MIN_TEXT_PX = 60  # fewer dark/grey pixels in the name field = empty row


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
    origin: tuple[int, int] = (0, 0)  # offset of the shop window relative to the reference geometry

    def box(self, y0: int, rel: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Rectangle of a row field (like NAME, PRICE) in this frame; y0 is the top of the row in the
        reference layout, i.e. ROW_TOP + k * ROW_PITCH."""
        return shift(_box(y0, rel), self.origin)


def shift(box: tuple[int, int, int, int], origin: tuple[int, int]) -> tuple[int, int, int, int]:
    dx, dy = origin
    return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)


def _box(y0: int, rel: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, dy0, x1, dy1 = rel
    return (x0, y0 + dy0, x1, y0 + dy1)


def _counts(a: np.ndarray) -> tuple[int, int]:
    """How many pixels of active (dark) and sold-out (grey) text there are in the crop."""
    dark = int((a < ACTIVE_MAX).all(axis=2).sum())
    grey = int(((a >= SOLD_LO) & (a <= SOLD_HI)).all(axis=2).sum())
    return dark, grey


def _line_mask(g: np.ndarray) -> np.ndarray:
    """A pixel is a "separator line" when it and the pixel below it are bright, and 2 or 3 px higher
    there is a dark stroke."""
    h = g.shape[0]
    bright = g >= SEP_BRIGHT
    dark = g <= SEP_DARK
    line = np.zeros_like(bright)
    line[3 : h - 1] = bright[3 : h - 1] & bright[4:h] & (dark[1 : h - 3] | dark[0 : h - 4])
    return line


def locate(a: np.ndarray) -> tuple[int, int] | None:
    """Searches for the shop list in the whole frame. Returns the offset (dx, dy) of the reference
    geometry, or None when the shop is not open. About 100 ms per 1920×1009 frame."""
    g = a.min(axis=2)
    h, w = g.shape
    span = (ROWS - 1) * ROW_PITCH + 2
    ew = SEP_EVAL[1] - SEP_EVAL[0]
    if h < span + 4 or w < ew + 1:
        return None
    line = _line_mask(g)
    cs = np.zeros((h, w + 1), dtype=np.int32)
    np.cumsum(line, axis=1, out=cs[:, 1:])
    frac = (cs[:, ew:] - cs[:, :-ew]) * (1.0 / ew)  # frac[y, x]: line fraction in columns x..x+ew
    tol = frac.copy()  # tolerance of ±1 px vertically for each separator separately
    np.maximum(tol[1:], frac[:-1], out=tol[1:])
    np.maximum(tol[:-1], frac[1:], out=tol[:-1])
    n = h - span
    hits = np.zeros((n, frac.shape[1]), dtype=np.int16)
    exact = np.zeros((n, frac.shape[1]), dtype=np.float32)
    for k in range(ROWS):
        sl = slice(k * ROW_PITCH, k * ROW_PITCH + n)
        hits += tol[sl] >= SEP_MIN_FRAC
        exact += frac[sl]
    score = hits + exact * 0.1  # the exact hit breaks ties on the plateau
    y, x = divmod(int(score.argmax()), score.shape[1])
    if hits[y, x] < SEP_MIN_HITS:
        return None
    # Left edge: the first column from which SEP_EDGE_RUN consecutive columns have a bright band
    # in at least SEP_MIN_HITS separators. The SEP_EVAL window starts 85 px past the edge,
    # and a fraction >= 0.6 lets the window lie up to 112 px either side of that spot, hence the range.
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
    ap.add_argument("files", nargs="+", help="PNG frames (glob allowed)")
    ap.add_argument("--dump", help="folder for the name/price/quantity crops to look at")
    args = ap.parse_args()
    files = [f for pat in args.files for f in sorted(glob.glob(pat))]
    out = Path(args.dump) if args.dump else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    for f in files:
        fr = parse(Image.open(f))
        stem = Path(f).stem
        if not fr.open:
            print(f"{stem}: shop closed")
            continue
        flags = "".join("." if r.empty else ("s" if r.sold else "a") for r in fr.rows)
        print(f"{stem}: open @{fr.origin}, rows [{flags}]  (a=active s=sold out .=empty)")
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
