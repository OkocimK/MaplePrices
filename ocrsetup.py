"""ocrsetup.py: makes sure Windows has the English OCR recognizer, installs it when missing.

The recognizer (`Language.OCR~~~en-US~0.0.1.0`) is an optional Windows feature, absent on many
non-English installs. It cannot be shipped inside the exe: it is a system component that DISM
pulls from Windows Update, and that needs administrator rights. So the tracker asks, runs
`dism.exe` elevated (one UAC prompt) and waits for it.

dism.exe rather than PowerShell's Add-WindowsCapability: same operation, but no quoting of the
capability name and no dependence on the PowerShell execution policy.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

import recognize

CAPABILITY = "Language.OCR~~~en-US~0.0.1.0"
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SW_SHOWNORMAL = 1
ERROR_CANCELLED = 1223  # the user said No in the UAC prompt
REBOOT_REQUIRED = 3010


class _ShellExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    ]


def run_and_wait(file: str, params: str, verb: str = "runas") -> int | None:
    """Starts a program through ShellExecuteEx (verb 'runas' = elevated, shows the UAC prompt),
    waits for it and returns its exit code. None when the user refused the UAC prompt."""
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    sei = _ShellExecuteInfo()
    sei.cbSize = ctypes.sizeof(sei)
    sei.fMask = SEE_MASK_NOCLOSEPROCESS
    sei.lpVerb = verb
    sei.lpFile = file
    sei.lpParameters = params
    sei.nShow = SW_SHOWNORMAL
    if not shell32.ShellExecuteExW(ctypes.byref(sei)):
        err = ctypes.get_last_error()
        if err == ERROR_CANCELLED:
            return None
        raise ctypes.WinError(err)
    try:
        kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(code))
        return code.value
    finally:
        kernel32.CloseHandle(sei.hProcess)


def _manual() -> str:
    return ("To install it by hand: right-click Start, open 'Terminal (Admin)' or 'Windows PowerShell (Admin)',\n"
            "paste this line and press Enter, then start the tracker again:\n\n"
            f"    {recognize.OCR_INSTALL_CMD}\n\n"
            "Or in Settings: Time & language, Language & region, Add a language, English (United States).")


def ensure() -> bool:
    """True when the tracker can read text. Otherwise offers the install, and returns False
    when it did not happen or needs a restart first; the reason is already printed."""
    if recognize.ocr_ready():
        return True
    print("Windows is missing the English text recognition (OCR) feature, the tracker cannot read shops without it.\n"
          "It is a small official Windows component (a few MB from Windows Update).")
    try:
        answer = input("Install it now? Windows will ask for permission. [Y/n] ").strip().lower()
    except EOFError:  # no console to answer from
        answer = "n"
    if answer not in ("", "y", "yes"):
        print(_manual())
        return False
    dism = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "dism.exe")
    print("Installing, this can take a minute or two. A second window shows the progress...", flush=True)
    try:
        code = run_and_wait(dism, f"/Online /Add-Capability /CapabilityName:{CAPABILITY}")
    except OSError as e:
        print(f"Could not start the installer: {e}\n{_manual()}")
        return False
    if code is None:
        print(f"Permission was not given, nothing was installed.\n{_manual()}")
        return False
    if code == REBOOT_REQUIRED:
        print("Installed, but Windows wants a restart first. Restart the computer and start the tracker again.")
        return False
    if code != 0:
        print(f"The installer failed (code 0x{code:08x}). Windows Update may be blocked or offline.\n{_manual()}")
        return False
    if not recognize.ocr_ready():
        print("Installed. Start the tracker again to use it.")
        return False
    print("Installed.")
    return True


if __name__ == "__main__":
    sys.exit(0 if ensure() else 1)
