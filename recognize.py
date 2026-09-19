"""recognize.py: one game frame -> list of observations (item, price, sold out, shop, map).

Glues together `shopframe` (geometry, finds the shop window in the frame), `minimap` (where
the minimap is and in what state), winocr (names, shop title, minimap), `glyphs` (prices) and
`names` (canonical scroll name). Nothing here touches the game or the disk.

The game's UI scale depends on the window size: the game fits its layout to the largest 16:9
rectangle that fits inside the client area (measured on 4 sizes, see PLAN.md), so the frame is
first scaled so that this rectangle has the reference height of 1009 px, and only then is the
shop window searched for. The cursor position is scaled the same way.

The minimap is movable, can be collapsed to a single bar or hidden; without it the offer
goes without map and channel, that is not an error.

Game cursor: the game draws its own cursor at the system cursor position, and that one has to
lie on the shop window for scrolling to work. A row whose name or price field the cursor touches
is skipped in this frame; the next frame (the cursor moves) will pick it up.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from PIL import Image, ImageOps

import glyphs
import minimap
import names
import shopframe as sf

warnings.filterwarnings("ignore")
import winocr  # noqa: E402  (winocr warns about asyncio on import)

CURSOR_BOX = (-6, -6, 30, 34)  # cursor sprite rectangle relative to the system position
PRICE_MIN_CONF = 0.75
_GLYPHS: glyphs.Glyphs | None = None
_MINIMAP: minimap.Minimap | None = None


def _g() -> glyphs.Glyphs:
    global _GLYPHS
    if _GLYPHS is None:
        _GLYPHS = glyphs.Glyphs()
    return _GLYPHS


def _mm() -> minimap.Minimap:
    global _MINIMAP
    if _MINIMAP is None:
        _MINIMAP = minimap.Minimap()
    return _MINIMAP


OCR_INSTALL_CMD = 'Add-WindowsCapability -Online -Name "Language.OCR~~~en-US~0.0.1.0"'


def ocr_ready() -> bool:
    """Windows OCR needs the English recognizer, an optional Windows feature that is missing on
    many non-English installs. winocr checks it with a bare assert on every call, so the tracker
    asks once at startup and explains what to install instead."""
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    return bool(OcrEngine.is_language_supported(Language("en")))


def ocr(im: Image.Image, scale: int = 3, pad: int = 10, boost: bool = False) -> str:
    """boost=True for sold-out (grey) rows: stretches the contrast, OCR loses words on them."""
    if boost:
        im = ImageOps.autocontrast(im, cutoff=1)
    bg = im.getpixel((im.width - 2, 2))
    im = ImageOps.expand(im, border=pad, fill=bg)
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    return winocr.recognize_pil_sync(im, "en")["text"].strip()


_PCT_TOKEN = re.compile(r"\s\d{2,3}\S*$")


def ocr_name(im: Image.Image, sold: bool) -> str:
    """Name from a row. On grey (sold-out) text winocr loses the trailing percent token
    at random, depending on scale and contrast, so we try three variants and take the
    first one with a percent, and without a percent the longest reading. On 98 sold-out rows
    from the database: one variant ~52 hits, three variants 73."""
    if not sold:
        return ocr(im)
    best = ""
    for kw in ({"boost": True}, {}, {"scale": 2}):
        t = ocr(im, **kw)
        if _PCT_TOKEN.search(t):
            return t
        if len(t) > len(best):
            best = t
    return best


@dataclass
class Obs:
    row: int
    name: str  # canonical scroll name, or the raw reading for other items
    raw_name: str
    scroll: bool
    complete: bool  # scroll with full stat and percent
    dark: bool | None
    equip: str | None
    stat: str | None
    pct: int | None
    price: int
    sold: bool
    conf: float  # min(price confidence, name confidence)
    crop: Image.Image = field(repr=False)
    pct_from_icon: bool = False  # percent not from text but from the icon (sensitive to delayed icons)


@dataclass
class Result:
    open: bool
    owner: str | None = None
    title: str | None = None
    map: str | None = None
    channel: int | None = None
    obs: list[Obs] = field(default_factory=list)
    skipped_cursor: int = 0
    skipped_price: int = 0
    stale_icons: bool = False  # the frame had an icon inconsistent with the percent in the text
    skipped_stale: int = 0  # rows skipped because they would take the percent from such a frame
    map_raw: str | None = None  # minimap as read by OCR (for channel diagnostics)
    minimap: str | None = None  # 'open', 'collapsed', 'map' (expanded without the name block) or None when not visible
    minimap_box: tuple[int, int, int, int] | None = None  # where the map was read from
    origin: tuple[int, int] = (0, 0)  # offset of the shop window relative to the reference geometry
    scale: float = 1.0  # how many times the frame was enlarged before recognition (1.0 = 16:9 client of height 1009)
    timer_raw: str | None = None  # shop timer as read by OCR
    ttl_min: int | None = None  # minutes until the shop disappears, None when unreadable


def _cursor_hits(cursor: tuple[int, int] | None, box: tuple[int, int, int, int]) -> bool:
    if cursor is None:
        return False
    cx, cy = cursor
    x0, y0, x1, y1 = cx + CURSOR_BOX[0], cy + CURSOR_BOX[1], cx + CURSOR_BOX[2], cy + CURSOR_BOX[3]
    return not (x1 < box[0] or x0 > box[2] or y1 < box[1] or y0 > box[3])


def parse_title(title: str) -> str | None:
    m = re.match(r"^(.*?)'s Hired Merchant", title)
    return m.group(1).strip() if m else None


def parse_timer(text: str) -> int | None:
    """Timer in the top right corner of the shop, e.g. "40:55", returns minutes until the shop
    disappears. The format is hours:minutes: the same merchant showed 40:55 on screenshots taken
    a few minutes apart, so it is not seconds. OCR can lose the colon ("27215") or produce
    a dot ("42.01"), hence the loose matching."""
    d = re.sub(r"\D", "", text)
    if len(d) == 4:  # "40:55" -> 4055
        h, mi = int(d[:2]), int(d[2:])
    elif len(d) == 5:  # "27215": colon read as a digit
        h, mi = int(d[:2]), int(d[3:])
    elif len(d) == 3:  # "7:05" -> 705
        h, mi = int(d[0]), int(d[1:])
    else:
        return None
    if mi > 59:
        return None
    return h * 60 + mi


def parse_minimap(text: str) -> tuple[str | None, int | None]:
    """'Hidden Street;u Free Market<l>' -> ('Free Market', 1). The minimap icon produces garbage
    in OCR, and with a two-digit channel OCR joins the lines ("Hidden Streeti Free Market<12>"),
    so the name is taken from the last known word, and the channel from the last group of digits."""
    text = text.replace("\n", " ")
    low = text.lower()
    if "free" in low:
        name = "Free Market"
        tail = text[low.index("free") + 4 :]
    else:
        m = re.search(r"([A-Za-z][A-Za-z' .-]*?)\s*<", text)
        name = re.sub(r"^(?:\S{1,2}\s+)+", "", m.group(1).strip()) if m else (text.strip() or None)
        tail = text
    ch_txt = re.sub(r"[lI]", "1", re.sub(r"[oO]", "0", tail))
    m = re.search(r"<\s*(\d{1,2})|(\d{1,2})\D{0,3}$", ch_txt)  # "Markets9?" passes too
    ch = next((g for g in m.groups() if g), None) if m else None
    return name, (int(ch) if ch else None)


def ui_scale(size: tuple[int, int]) -> float:
    """How many times to enlarge a frame of this size so that the UI has the reference scale."""
    w, h = size
    return sf.REF_H / min(h, w * 9 / 16)


def normalize(frame: Image.Image) -> tuple[Image.Image, float]:
    """Frame at the reference scale and the factor used (1.0 = unchanged)."""
    s = ui_scale(frame.size)
    if abs(s - 1.0) < 0.002:
        return frame, 1.0
    return frame.resize((round(frame.width * s), round(frame.height * s)), Image.LANCZOS), s


def recognize(frame: Image.Image, cursor: tuple[int, int] | None = None) -> Result:
    """`cursor` is the cursor position in the coordinates of the game window's client area, or None."""
    frame, scale = normalize(frame)
    if cursor is not None and scale != 1.0:
        cursor = (round(cursor[0] * scale), round(cursor[1] * scale))
    fr = sf.parse(frame)
    if not fr.open:
        return Result(False, scale=scale)
    title = ocr(fr.title)
    res = Result(True, owner=parse_title(title), title=title, origin=fr.origin, scale=scale)
    res.timer_raw = ocr(frame.crop(sf.shift(sf.TIMER, fr.origin)))
    res.ttl_min = parse_timer(res.timer_raw)
    mm = _mm().find(frame)
    if mm is not None:
        res.minimap, res.minimap_box = mm
        res.map_raw = ocr(frame.crop(res.minimap_box), scale=2)
        res.map, res.channel = parse_minimap(res.map_raw)
        if res.channel is None and "free" not in res.map_raw.lower():
            # An expanded minimap shrunk with the "-" button shows only the map without the name block,
            # and OCR then reads garbage from the map image. The channel "<N>" is always in that block, so
            # without it the name is not reliable: the offer goes without a map, as with a hidden minimap.
            res.map = None
            res.minimap = "map"
    g = _g()
    for r in fr.rows:
        if r.empty:
            continue
        y0 = sf.ROW_TOP + r.index * sf.ROW_PITCH
        name_box = fr.box(y0, sf.NAME)
        price_box = fr.box(y0, sf.PRICE)
        if _cursor_hits(cursor, name_box) or _cursor_hits(cursor, price_box):
            res.skipped_cursor += 1
            continue
        price, pconf, _raw = g.read(r.price)
        if price is None or pconf < PRICE_MIN_CONF:
            res.skipped_price += 1
            continue
        raw = ocr_name(r.name, r.sold)
        if not raw:
            continue
        sc = names.parse(raw, r.icon, r.sold)
        crop = frame.crop(sf.shift((sf.ICON[0], y0, sf.NAME[2], y0 + sf.ROW_PITCH), fr.origin))
        if sc is not None:
            pct_from_text = bool(_PCT_TOKEN.search(raw))
            if pct_from_text and sc.pct is not None and not r.sold:
                # Safeguard against delayed icons: the percent from the text is certain, so an icon
                # that confidently says something else means the icons in this frame are still
                # from the previous state of the list. In the first session's database: 4.6% of frames.
                # Active rows only: the faded patterns have too few samples to be trusted.
                ip = names.pct_from_icon(r.icon, False)
                if ip is not None and ip != sc.pct:
                    res.stale_icons = True
            res.obs.append(
                Obs(r.index, sc.name, raw, True, sc.complete and sc.score >= 0.8, sc.dark, sc.equip, sc.stat, sc.pct,
                    price, r.sold, min(pconf, sc.score), crop, pct_from_icon=(sc.pct is not None and not pct_from_text))
            )
        else:
            res.obs.append(Obs(r.index, raw, raw, False, True, None, None, None, None, price, r.sold, pconf, crop))
    if res.stale_icons:
        keep = [o for o in res.obs if not o.pct_from_icon]
        res.skipped_stale = len(res.obs) - len(keep)
        res.obs = keep
    return res


if __name__ == "__main__":
    import glob
    import sys

    for pat in sys.argv[1:]:
        for f in sorted(glob.glob(pat)):
            r = recognize(Image.open(f))
            if not r.open:
                print(f"{f}: shop closed")
                continue
            print(f"{f}: {r.owner!r} @ {r.map}<{r.channel}> (minimap {r.minimap}, shop @{r.origin}, scale {r.scale:.3f}) disappears in {r.ttl_min} min ({r.timer_raw!r})  skipped: cursor {r.skipped_cursor}, price {r.skipped_price}, stale icons {r.skipped_stale}")
            for o in r.obs:
                flag = "SOLD" if o.sold else "    "
                kind = ("scroll" if o.complete else "scroll?") if o.scroll else "other"
                print(f"   r{o.row} {flag} {kind:7} {o.price:>14,}  {o.name}  (conf {o.conf:.2f})")
