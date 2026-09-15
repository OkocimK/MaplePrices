"""recognize.py: jedna klatka gry -> lista obserwacji (przedmiot, cena, wykupiony, sklep, mapa).

Skleja `shopframe` (geometria, znajduje okno sklepu w klatce), `minimap` (gdzie jest
minimapa i w jakim stanie), winocr (nazwy, tytuł sklepu, minimapa), `glyphs` (ceny) i `names`
(kanoniczna nazwa scrolla). Nic tu nie dotyka gry ani dysku.

Minimapa jest przesuwalna, może być zwinięta do jednego paska albo schowana; bez niej oferta
idzie bez mapy i kanału, to nie jest błąd.

Kursor gry: gra rysuje własny kursor w miejscu kursora systemowego, a ten musi leżeć na
oknie sklepu, żeby dało się przewijać. Wiersz, którego pola nazwy albo ceny kursor dotyka,
jest w tej klatce pomijany; następna klatka (kursor się rusza) go dobierze.
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
import winocr  # noqa: E402  (winocr ostrzega o asyncio przy imporcie)

CURSOR_BOX = (-6, -6, 30, 34)  # prostokąt sprite'a kursora względem pozycji systemowej
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


def ocr(im: Image.Image, scale: int = 3, pad: int = 10, boost: bool = False) -> str:
    """boost=True dla wykupionych (szarych) wierszy: rozciąga kontrast, OCR gubi na nich słowa."""
    if boost:
        im = ImageOps.autocontrast(im, cutoff=1)
    bg = im.getpixel((im.width - 2, 2))
    im = ImageOps.expand(im, border=pad, fill=bg)
    im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    return winocr.recognize_pil_sync(im, "en")["text"].strip()


_PCT_TOKEN = re.compile(r"\s\d{2,3}\S*$")


def ocr_name(im: Image.Image, sold: bool) -> str:
    """Nazwa z wiersza. Na szarym (wykupionym) tekście winocr gubi końcowy token z procentem
    losowo, w zależności od skali i kontrastu, więc próbujemy trzech wariantów i bierzemy
    pierwszy z procentem, a bez procentu najdłuższy odczyt. Na 98 wykupionych wierszach
    z bazy: jeden wariant ~52 trafień, trzy warianty 73."""
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
    name: str  # kanoniczna nazwa scrolla albo surowy odczyt dla innych przedmiotów
    raw_name: str
    scroll: bool
    complete: bool  # scroll z pełnym statem i procentem
    dark: bool | None
    equip: str | None
    stat: str | None
    pct: int | None
    price: int
    sold: bool
    conf: float  # min(pewność ceny, pewność nazwy)
    crop: Image.Image = field(repr=False)
    pct_from_icon: bool = False  # procent nie z tekstu, tylko z ikony (wrażliwy na opóźnione ikony)


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
    stale_icons: bool = False  # w klatce była ikona niezgodna z procentem w tekście
    skipped_stale: int = 0  # wiersze pominięte, bo brałyby procent z takiej klatki
    map_raw: str | None = None  # minimapa jak ją przeczytał OCR (do diagnostyki kanału)
    minimap: str | None = None  # 'open', 'collapsed' albo None, gdy minimapy nie widać
    minimap_box: tuple[int, int, int, int] | None = None  # skąd czytana była mapa
    origin: tuple[int, int] = (0, 0)  # przesunięcie okna sklepu względem geometrii referencyjnej
    timer_raw: str | None = None  # licznik sklepu jak go przeczytał OCR
    ttl_min: int | None = None  # minuty do zniknięcia sklepu, None gdy nieczytelny


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
    """Licznik w prawym górnym rogu sklepu, np. „40:55", zwraca minuty do zniknięcia sklepu.
    Format to godziny:minuty: ten sam kupiec pokazywał 40:55 na zrzutach zrobionych kilka
    minut od siebie, więc to nie sekundy. OCR potrafi zgubić dwukropek („27215") albo dać
    kropkę („42.01"), stąd luźne dopasowanie."""
    d = re.sub(r"\D", "", text)
    if len(d) == 4:  # „40:55" -> 4055
        h, mi = int(d[:2]), int(d[2:])
    elif len(d) == 5:  # „27215": dwukropek przeczytany jako cyfra
        h, mi = int(d[:2]), int(d[3:])
    elif len(d) == 3:  # „7:05" -> 705
        h, mi = int(d[0]), int(d[1:])
    else:
        return None
    if mi > 59:
        return None
    return h * 60 + mi


def parse_minimap(text: str) -> tuple[str | None, int | None]:
    """'Hidden Street;u Free Market<l>' -> ('Free Market', 1). Ikona minimapy śmieci w OCR,
    a przy dwucyfrowym kanale OCR skleja linie („Hidden Streeti Free Market<12>"), więc
    nazwę bierzemy od ostatniego znanego słowa, a kanał z ostatniej grupy cyfr."""
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
    m = re.search(r"<\s*(\d{1,2})|(\d{1,2})\D{0,3}$", ch_txt)  # „Markets9?" też przechodzi
    ch = next((g for g in m.groups() if g), None) if m else None
    return name, (int(ch) if ch else None)


def recognize(frame: Image.Image, cursor: tuple[int, int] | None = None) -> Result:
    """`cursor` to pozycja kursora we współrzędnych obszaru klienta okna gry, albo None."""
    fr = sf.parse(frame)
    if not fr.open:
        return Result(False)
    title = ocr(fr.title)
    res = Result(True, owner=parse_title(title), title=title, origin=fr.origin)
    res.timer_raw = ocr(frame.crop(sf.shift(sf.TIMER, fr.origin)))
    res.ttl_min = parse_timer(res.timer_raw)
    mm = _mm().find(frame)
    if mm is not None:
        res.minimap, res.minimap_box = mm
        res.map_raw = ocr(frame.crop(res.minimap_box), scale=2)
        res.map, res.channel = parse_minimap(res.map_raw)
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
                # Bezpiecznik na opóźnione ikony: procent z tekstu jest pewny, więc ikona,
                # która z pewnością mówi co innego, znaczy, że ikony w tej klatce są jeszcze
                # z poprzedniego stanu listy. W bazie z pierwszej sesji: 4,6% klatek.
                # Tylko aktywne wiersze: wyblakłe wzorce mają za mało próbek, żeby im ufać.
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
                print(f"{f}: sklep zamknięty")
                continue
            print(f"{f}: {r.owner!r} @ {r.map}<{r.channel}> (minimapa {r.minimap}, sklep @{r.origin}) znika za {r.ttl_min} min ({r.timer_raw!r})  pominięte: kursor {r.skipped_cursor}, cena {r.skipped_price}, nieświeże ikony {r.skipped_stale}")
            for o in r.obs:
                flag = "SOLD" if o.sold else "    "
                kind = ("scroll" if o.complete else "scroll?") if o.scroll else "inny"
                print(f"   r{o.row} {flag} {kind:7} {o.price:>14,}  {o.name}  (conf {o.conf:.2f})")
