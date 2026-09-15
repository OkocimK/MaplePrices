"""grab.py: zrzut okna gry na skrót klawiszowy, do folderu samples/.

Etap M0 z PLAN.md. Nie dotyka gry: szuka okna po tytule, bierze jego obszar klienta
(bez ramki i paska tytułu) i kopiuje piksele z kompozytora Windows przez mss.

Uruchomienie (z folderu pricetrack, venv już założony):

    .venv\\Scripts\\python grab.py                # okno z "MapleStory" w tytule
    .venv\\Scripts\\python grab.py --title Worlds  # inny fragment tytułu
    .venv\\Scripts\\python grab.py --list          # wypisz widoczne okna i wyjdź

Skróty (działają globalnie, gra może mieć fokus):
    F8          zapisz zrzut
    Ctrl+F8     zakończ

Każdy zrzut wypisuje ścieżkę, rozmiar i średnią jasność. Jasność bliska zeru znaczy,
że mss dostał czarny prostokąt i trzeba przejść na Windows Graphics Capture.
"""

import argparse
import ctypes
import sys
from datetime import datetime
from pathlib import Path

import keyboard
import mss
import mss.tools
import numpy as np
import win32gui

SAMPLES = Path(__file__).resolve().parent / "samples"
GRAB_KEY = "f8"
QUIT_KEY = "ctrl+f8"


def set_dpi_aware() -> None:
    """Bez tego przy skalowaniu 125%/150% współrzędne okna są w innych pikselach niż zrzut."""
    user32 = ctypes.windll.user32
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def visible_windows() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []

    def cb(hwnd: int, _: object) -> None:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                out.append((hwnd, title))

    win32gui.EnumWindows(cb, None)
    return out


def find_window(fragment: str) -> int | None:
    frag = fragment.lower()
    hits = [(h, t) for h, t in visible_windows() if frag in t.lower()]
    if not hits:
        return None
    if len(hits) > 1:
        print(f"Several windows match '{fragment}', taking the first:")
        for h, t in hits:
            print(f"   {h:>8}  {t}")
    return hits[0][0]


def client_rect(hwnd: int) -> dict[str, int]:
    """Obszar klienta okna we współrzędnych ekranu, gotowy dla mss.grab."""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, (left, top))
    return {"left": sx, "top": sy, "width": right - left, "height": bottom - top}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", default="MapleStory", help="fragment tytułu okna gry (domyślnie: MapleStory)")
    ap.add_argument("--list", action="store_true", help="wypisz widoczne okna i wyjdź")
    args = ap.parse_args()

    set_dpi_aware()

    if args.list:
        for h, t in sorted(visible_windows(), key=lambda x: x[1].lower()):
            print(f"{h:>8}  {t}")
        return 0

    hwnd = find_window(args.title)
    if hwnd is None:
        print(f"Nie znalazłem widocznego okna z '{args.title}' w tytule.")
        print("Sprawdź tytuł przez: grab.py --list  i podaj go przez --title.")
        return 1

    SAMPLES.mkdir(exist_ok=True)
    print(f"Okno: {win32gui.GetWindowText(hwnd)!r}  hwnd={hwnd}")
    print(f"Obszar klienta: {client_rect(hwnd)}")
    print(f"Zrzuty lecą do: {SAMPLES}")
    print(f"[{GRAB_KEY.upper()}] zrzut   [{QUIT_KEY.upper()}] koniec")

    sct = (getattr(mss, "MSS", None) or mss.mss)()
    count = 0

    def grab() -> None:
        nonlocal count
        if not win32gui.IsWindow(hwnd):
            print("Okno gry zniknęło, kończę.")
            keyboard.press_and_release(QUIT_KEY)
            return
        rect = client_rect(hwnd)  # czytane za każdym razem, okno mogło się przesunąć
        if rect["width"] <= 0 or rect["height"] <= 0:
            print("Okno zminimalizowane albo puste, pomijam.")
            return
        shot = sct.grab(rect)
        count += 1
        name = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + f"-{count:03d}.png"
        path = SAMPLES / name
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
        mean = float(np.frombuffer(shot.rgb, dtype=np.uint8).mean())
        warn = "   ⚠ czarny prostokąt, mss nie widzi okna" if mean < 2 else ""
        print(f"{name}  {shot.size[0]}×{shot.size[1]}  jasność {mean:5.1f}{warn}")

    keyboard.add_hotkey(GRAB_KEY, grab, suppress=False)
    try:
        keyboard.wait(QUIT_KEY)
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.unhook_all()
        sct.close()
    print(f"Koniec, zapisano {count} zrzutów.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
