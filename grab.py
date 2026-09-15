"""grab.py: screenshot of the game window on a hotkey, into the samples/ folder.

Stage M0 from PLAN.md (design notes, kept outside the repo). Does not touch the game: finds the
window by title, takes its client area (without the frame and title bar) and copies the pixels
from the Windows compositor via mss.

Running (from the pricetrack folder, venv already set up):

    .venv\\Scripts\\python grab.py                # window with "MapleStory" in the title
    .venv\\Scripts\\python grab.py --title Worlds  # a different title fragment
    .venv\\Scripts\\python grab.py --list          # list visible windows and exit

Hotkeys (global, the game may keep focus):
    F8          save a screenshot
    Ctrl+F8     quit

Every screenshot prints its path, size and mean brightness. Brightness close to zero means
that mss got a black rectangle and we have to switch to Windows Graphics Capture.
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
    """Without this, at 125%/150% scaling the window coordinates are in different pixels than the screenshot."""
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
    """Client area of the window in screen coordinates, ready for mss.grab."""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, (left, top))
    return {"left": sx, "top": sy, "width": right - left, "height": bottom - top}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", default="MapleStory", help="fragment of the game window title (default: MapleStory)")
    ap.add_argument("--list", action="store_true", help="list visible windows and exit")
    args = ap.parse_args()

    set_dpi_aware()

    if args.list:
        for h, t in sorted(visible_windows(), key=lambda x: x[1].lower()):
            print(f"{h:>8}  {t}")
        return 0

    hwnd = find_window(args.title)
    if hwnd is None:
        print(f"No visible window with '{args.title}' in the title found.")
        print("Check the title with: grab.py --list  and pass it via --title.")
        return 1

    SAMPLES.mkdir(exist_ok=True)
    print(f"Window: {win32gui.GetWindowText(hwnd)!r}  hwnd={hwnd}")
    print(f"Client area: {client_rect(hwnd)}")
    print(f"Screenshots go to: {SAMPLES}")
    print(f"[{GRAB_KEY.upper()}] screenshot   [{QUIT_KEY.upper()}] quit")

    sct = (getattr(mss, "MSS", None) or mss.mss)()
    count = 0

    def grab() -> None:
        nonlocal count
        if not win32gui.IsWindow(hwnd):
            print("Game window is gone, quitting.")
            keyboard.press_and_release(QUIT_KEY)
            return
        rect = client_rect(hwnd)  # read every time, the window may have moved
        if rect["width"] <= 0 or rect["height"] <= 0:
            print("Window minimized or empty, skipping.")
            return
        shot = sct.grab(rect)
        count += 1
        name = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + f"-{count:03d}.png"
        path = SAMPLES / name
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
        mean = float(np.frombuffer(shot.rgb, dtype=np.uint8).mean())
        warn = "   ⚠ black rectangle, mss does not see the window" if mean < 2 else ""
        print(f"{name}  {shot.size[0]}×{shot.size[1]}  brightness {mean:5.1f}{warn}")

    keyboard.add_hotkey(GRAB_KEY, grab, suppress=False)
    try:
        keyboard.wait(QUIT_KEY)
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.unhook_all()
        sct.close()
    print(f"Done, saved {count} screenshots.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
