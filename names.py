"""names.py: z odczytu OCR nazwy i z ikony robi kanoniczną nazwę scrolla.

Nie dopasowujemy całych nazw do słownika, bo słownik z osmlib (`scrolls.json`) nie pokrywa
tego świata (są tu np. Earring for LUK, Shield for LUK, Gun, Accuracy, „Overall" bez „Armor").
Zamiast tego nazwa scrolla ma sztywną gramatykę:

    (Dark )?scroll for <część> for <stat> <procent>%

i każdy człon dopasowuje się osobno do małego, zamkniętego słownika odległością Levenshteina.
To znosi dwa problemy naraz:
1. gra obcina nazwę po szerokości piksela (znika procent, czasem cały stat),
2. OCR myli pojedyncze litery (kursor gry, ogonki, „3C(k" zamiast „30%").

Procent bierze się z tekstu, a gdy go brak albo jest nieczytelny, z ikony (`icons.py`:
10% złoty, 30% fioletowy, 60% czerwono-pomarańczowy, 70% szarobrązowy, 100% stalowy;
wykupione wiersze mają osobne, wyblakłe wzorce). Gdy stat jest obcięty w całości, wynik
jest niejednoznaczny i taki zostaje: lepiej „Dark scroll for Overall Armor for ? 30%"
niż zgadywanie.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image

EQUIPS = [
    "Helmet", "Earring", "Eye Accessory", "Face Accessory", "Topwear", "Bottomwear",
    "Overall Armor", "Overall", "Shoes", "Gloves", "Shield", "Cape", "Pet Equip.",
    "One-Handed Sword", "One-Handed Axe", "One-Handed BW", "Dagger", "Wand", "Staff",
    "Two-handed Sword", "Two-handed Axe", "Two-handed BW", "Spear", "Pole Arm",
    "Bow", "Crossbow", "Claw", "Gun", "Knuckler",
]
STATS = [
    "DEF", "HP", "MP", "STR", "DEX", "INT", "LUK", "ATT", "Magic Att.", "Magic Def.",
    "Weapon Def.", "Accuracy", "Avoidability", "Speed", "Jump",
]
PCTS = [10, 30, 60, 70, 100]

_ICONS: "icons.Icons | None" = None


DARK_PCTS = {30, 70}  # dark scroll to zawsze 30% albo 70% (potwierdzone przez użytkownika)


def pct_from_icon(icon: Image.Image, sold: bool = False, dark: bool = False) -> int | None:
    """Procent z ikony przez `icons.Icons` (wzorce chromy, osobno aktywne i wyblakłe).
    Dla ciemnego scrolla ikona spoza 30/70 to ikona jeszcze nie doładowana, wynik None."""
    global _ICONS
    if _ICONS is None:
        import icons

        _ICONS = icons.Icons()
    return _ICONS.pct(icon, sold, DARK_PCTS if dark else None)
PCT_MAX_DIST = 45.0


@dataclass
class Scroll:
    dark: bool
    equip: str
    stat: str | None  # None = obcięty, nie do ustalenia z tej klatki
    pct: int | None
    score: float  # 0..1, jakość dopasowania członów
    raw: str

    @property
    def name(self) -> str:
        head = "Dark scroll" if self.dark else "Scroll"
        stat = self.stat or "?"
        pct = f"{self.pct}%" if self.pct else "?%"
        return f"{head} for {self.equip} for {stat} {pct}"

    @property
    def complete(self) -> bool:
        return self.stat is not None and self.pct is not None


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'")
    s = re.sub(r"[^a-z0-9%'. -]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def lev(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _best(q: str, vocab: list[str], prefix: bool) -> tuple[str | None, float]:
    """Najlepszy wpis słownika dla q. prefix=True: q może być obciętym początkiem wpisu."""
    q = q.strip()
    if not q:
        return None, 0.0
    best, score = None, -1.0
    for v in vocab:
        n = v.lower()
        target = n[: len(q)] if prefix and len(n) > len(q) else n
        d = lev(q, target)
        s = 1.0 - d / max(len(q), len(target))
        # przy prefiksie wolimy dłuższy wpis tylko jeśli q go naprawdę zaczyna
        if s > score:
            best, score = v, s
    return best, score


_HEAD = re.compile(r"^(dark\s+)?s?croll\s+for\s+(.*)$")
_PCT = re.compile(r"(\d{2,3})\s*[%/(]?\s*\w{0,2}$")


def parse(raw: str, icon: Image.Image | None = None, sold: bool = False) -> Scroll | None:
    """None, gdy tekst nie wygląda na scroll. Inaczej Scroll, być może niekompletny.
    `icon` rozstrzyga procent, gdy tekst go nie ma; `sold` wybiera wzorce wyblakłych ikon."""
    q = _norm(raw)
    m = _HEAD.match(q)
    if not m:
        # OCR mógł zgubić początek („croll for", „scrolI for"); wymagamy „for" w środku
        if " for " not in q or lev(q[:10], "scroll for") > 3:
            return None
        dark = q.startswith("d")
        rest = q.split(" for ", 1)[1]
    else:
        dark = bool(m.group(1))
        rest = m.group(2)

    # rest = "<equip> for <stat> <pct>%"  albo obcięte w dowolnym miejscu
    parts = re.split(r"\s+for(?:\s+|$)", rest, maxsplit=1)
    if len(parts) == 2:
        eq_txt, tail = parts
        eq_truncated = False
    else:
        eq_txt, tail = rest, ""
        eq_truncated = True
    equip, eq_score = _best(eq_txt, EQUIPS, prefix=eq_truncated)
    if equip is None or eq_score < 0.6:
        if eq_truncated or not eq_txt.strip():
            return None
        # część nieznana, ale gramatyka się zgadza („Scroll for Sword for"): zostaje surowa,
        # niska ocena zrobi z tego wpis do ręcznego przejrzenia
        equip, eq_score = eq_txt.strip().title(), 0.3

    pct: int | None = None
    stat: str | None = None
    st_score = 1.0
    tokens = tail.split()
    # procent: ostatni token zaczynający się cyfrą („60%", „3C(k", „6001", „100/")
    if tokens and tokens[-1][0].isdigit():
        tok = tokens.pop()
        digits = re.match(r"\d+", tok).group(0)
        pct = int(digits) if int(digits) in PCTS else None
        if pct is None and len(digits) == 1:
            pass  # obcięte do jednej cyfry („INT 1" to 10% albo 100%), niech rozstrzygnie ikona
        elif pct is None:
            # OCR podmienił cyfrę na literę („3C") albo dokleił śmieci („6001")
            cand = [p for p in PCTS if str(p)[0] == digits[0]]
            if len(cand) == 1 or (cand and len(tok) <= 3):
                pct = cand[0] if len(cand) == 1 else next((p for p in cand if len(str(p)) == 2), None)
    # stat: najdłuższy początek pozostałych tokenów, który pasuje do słownika; bez procentu
    # w tekście stat może być obcięty („DEX 6" -> „DEX", „Ac" -> „Accuracy")
    if tokens:
        st_truncated = pct is None
        best_stat, best_score = None, 0.0
        for n in range(len(tokens), 0, -1):
            cand, sc = _best(" ".join(tokens[:n]), STATS, prefix=st_truncated)
            sc -= 0.05 * (len(tokens) - n)  # kara za odrzucone śmieci
            if sc > best_score:
                best_stat, best_score = cand, sc
        if best_score >= 0.6:
            stat, st_score = best_stat, best_score
        else:
            stat, st_score = None, 0.0

    if dark and pct is not None and pct not in DARK_PCTS:
        pct = None  # OCR przekręcił cyfrę; dark scroll to 30/70
    if pct is None and icon is not None:
        pct = pct_from_icon(icon, sold, dark)
    return Scroll(dark, equip, stat, pct, min(eq_score, st_score), raw)
