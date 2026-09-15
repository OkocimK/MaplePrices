"""names.py: builds a canonical scroll name from the OCR reading of the name and from the icon.

We do not match whole names against a dictionary, because the osmlib scroll dictionary
does not cover this world (it has e.g. Earring for LUK, Shield for LUK, Gun, Accuracy, "Overall"
without "Armor"). Instead the scroll name has a rigid grammar:

    (Dark )?scroll for <part> for <stat> <percent>%

and each part is matched separately against a small, closed vocabulary by Levenshtein distance.
That removes two problems at once:
1. the game truncates the name by pixel width (the percent disappears, sometimes the whole stat),
2. OCR confuses single letters (the game cursor, diacritics, "3C(k" instead of "30%").

The percent is taken from the text, and when it is missing or unreadable, from the icon
(`icons.py`: 10% golden, 30% purple, 60% red-orange, 70% grey-brown, 100% steel; sold-out rows
have separate, faded templates). When the stat is truncated entirely, the result is ambiguous
and stays that way: better "Dark scroll for Overall Armor for ? 30%" than guessing.
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


DARK_PCTS = {30, 70}  # a dark scroll is always 30% or 70% (confirmed by the user)


def pct_from_icon(icon: Image.Image, sold: bool = False, dark: bool = False) -> int | None:
    """Percent from the icon via `icons.Icons` (chroma templates, active and faded separately).
    For a dark scroll an icon outside 30/70 is an icon not yet loaded, result None."""
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
    stat: str | None  # None = truncated, cannot be determined from this frame
    pct: int | None
    score: float  # 0..1, quality of the part matches
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
    """Best vocabulary entry for q. prefix=True: q may be a truncated beginning of an entry."""
    q = q.strip()
    if not q:
        return None, 0.0
    best, score = None, -1.0
    for v in vocab:
        n = v.lower()
        target = n[: len(q)] if prefix and len(n) > len(q) else n
        d = lev(q, target)
        s = 1.0 - d / max(len(q), len(target))
        # with a prefix we prefer a longer entry only if q really starts it
        if s > score:
            best, score = v, s
    return best, score


_HEAD = re.compile(r"^(dark\s+)?s?croll\s+for\s+(.*)$")
_PCT = re.compile(r"(\d{2,3})\s*[%/(]?\s*\w{0,2}$")


def parse(raw: str, icon: Image.Image | None = None, sold: bool = False) -> Scroll | None:
    """None when the text does not look like a scroll. Otherwise a Scroll, possibly incomplete.
    `icon` settles the percent when the text lacks it; `sold` selects the faded icon templates."""
    q = _norm(raw)
    m = _HEAD.match(q)
    if not m:
        # OCR may have lost the beginning ("croll for", "scrolI for"); we require "for" inside
        if " for " not in q or lev(q[:10], "scroll for") > 3:
            return None
        dark = q.startswith("d")
        rest = q.split(" for ", 1)[1]
    else:
        dark = bool(m.group(1))
        rest = m.group(2)

    # rest = "<equip> for <stat> <pct>%"  or truncated at any point
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
        # unknown part, but the grammar matches ("Scroll for Sword for"): it stays raw,
        # the low score turns it into an entry for manual review
        equip, eq_score = eq_txt.strip().title(), 0.3

    pct: int | None = None
    stat: str | None = None
    st_score = 1.0
    tokens = tail.split()
    # percent: the last token starting with a digit ("60%", "3C(k", "6001", "100/")
    if tokens and tokens[-1][0].isdigit():
        tok = tokens.pop()
        digits = re.match(r"\d+", tok).group(0)
        pct = int(digits) if int(digits) in PCTS else None
        if pct is None and len(digits) == 1:
            pass  # truncated to one digit ("INT 1" is 10% or 100%), let the icon settle it
        elif pct is None:
            # OCR swapped a digit for a letter ("3C") or appended junk ("6001")
            cand = [p for p in PCTS if str(p)[0] == digits[0]]
            if len(cand) == 1 or (cand and len(tok) <= 3):
                pct = cand[0] if len(cand) == 1 else next((p for p in cand if len(str(p)) == 2), None)
    # stat: the longest beginning of the remaining tokens that matches the vocabulary; without a
    # percent in the text the stat may be truncated ("DEX 6" -> "DEX", "Ac" -> "Accuracy")
    if tokens:
        st_truncated = pct is None
        best_stat, best_score = None, 0.0
        for n in range(len(tokens), 0, -1):
            cand, sc = _best(" ".join(tokens[:n]), STATS, prefix=st_truncated)
            sc -= 0.05 * (len(tokens) - n)  # penalty for discarded junk
            if sc > best_score:
                best_stat, best_score = cand, sc
        if best_score >= 0.6:
            stat, st_score = best_stat, best_score
        else:
            stat, st_score = None, 0.0

    if dark and pct is not None and pct not in DARK_PCTS:
        pct = None  # OCR garbled the digit; a dark scroll is 30/70
    if pct is None and icon is not None:
        pct = pct_from_icon(icon, sold, dark)
    return Scroll(dark, equip, stat, pct, min(eq_score, st_score), raw)
