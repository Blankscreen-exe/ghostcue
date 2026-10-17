"""Capture the window the presenter is looking at, for Claude to answer from.

Every window gives a screenshot. A web browser also gives the address and the
whole page's text, read the way a screen reader reads it, so that what is below
the visible part comes too without scrolling. Other apps, among them a meeting
app showing the client's shared screen, give the screenshot alone: Claude then
knows what the presenter can see, and no more.
"""

import ctypes
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

from .win32 import GA_ROOT, GA_ROOTOWNER, kernel32, user32

BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe"}
NOT_WINDOWS = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}  # the desktop and the taskbar
LONGEST = 1568        # pixels on the longer side; Claude scales anything larger down to this
PAGE_TEXT = 40_000    # characters of page text, about 10,000 tokens
PAGE_WAIT = 2.0       # seconds a browser may take to build its page text the first time it is asked

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
DWMWA_EXTENDED_FRAME_BOUNDS = 9  # the window as drawn, without the invisible resize border
SRCCOPY = 0x00CC0020
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

HANDLE = wintypes.HANDLE
user32.GetDC.restype = HANDLE
user32.GetDC.argtypes = (wintypes.HWND,)
user32.ReleaseDC.argtypes = (wintypes.HWND, HANDLE)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.IsIconic.argtypes = (wintypes.HWND,)
gdi32.CreateCompatibleDC.restype = HANDLE
gdi32.CreateCompatibleDC.argtypes = (HANDLE,)
gdi32.CreateCompatibleBitmap.restype = HANDLE
gdi32.CreateCompatibleBitmap.argtypes = (HANDLE, ctypes.c_int, ctypes.c_int)
gdi32.SelectObject.restype = HANDLE
gdi32.SelectObject.argtypes = (HANDLE, HANDLE)
gdi32.BitBlt.argtypes = (HANDLE, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, HANDLE, ctypes.c_int,
                         ctypes.c_int, wintypes.DWORD)
gdi32.GetDIBits.argtypes = (HANDLE, HANDLE, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p,
                            wintypes.UINT)
gdi32.DeleteObject.argtypes = (HANDLE,)
gdi32.DeleteDC.argtypes = (HANDLE,)
dwmapi.DwmGetWindowAttribute.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
kernel32.OpenProcess.restype = HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.QueryFullProcessImageNameW.argtypes = (HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                ctypes.POINTER(wintypes.DWORD))
kernel32.CloseHandle.argtypes = (HANDLE,)


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class CaptureError(Exception):
    """The window could not be captured; the message says why, for the presenter."""


@dataclass
class Capture:
    title: str
    app: str            # the program's file name, such as chrome.exe
    png: bytes          # the screenshot
    url: str = ""       # for a web page
    text: str = ""      # for a web page: its whole text
    cut: bool = False   # the page text was longer than PAGE_TEXT, and was cut there

    def describe(self):
        """How the capture is introduced to Claude, ahead of the screenshot."""
        where = f"{self.title} ({self.app})" + (f", {self.url}" if self.url else "")
        if not self.text:
            return f"[Captured window: {where}. The screenshot is attached.]"
        cut = f" It was cut after the first {PAGE_TEXT:,} characters." if self.cut else ""
        return (f"[Captured web page: {where}. The screenshot is attached and shows only the part on screen. "
                f"The page's whole text follows, including what is above and below that part.{cut}]\n"
                f"<page>\n{self.text}\n</page>")


def front_window():
    """The window in front, which is the one the presenter is working in: GhostCue never takes the keyboard."""
    hwnd = user32.GetForegroundWindow()
    hwnd = hwnd and user32.GetAncestor(hwnd, GA_ROOT)
    if not hwnd:
        raise CaptureError("No window is in front. Click the window to capture, then try again.")
    if not user32.GetWindowTextLengthW(hwnd):  # an untitled pop-up, such as a browser's: take the window it belongs to
        hwnd = user32.GetAncestor(hwnd, GA_ROOTOWNER) or hwnd
    name = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, name, 256)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if name.value in NOT_WINDOWS:
        raise CaptureError("No window is in front. Click the window to capture, then try again.")
    if pid.value == os.getpid():
        raise CaptureError("That is GhostCue's own window. Click the window to capture, then try again.")
    return hwnd


def take(hwnd):
    """Capture the window `hwnd`. Runs on the capture thread, where COM is set up."""
    if user32.IsIconic(hwnd):
        raise CaptureError("That window is minimised.")
    title = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, title, 512)
    app = program(hwnd)
    capture = Capture(title.value.strip() or app, app, screenshot(hwnd))
    if app in BROWSERS:
        capture.url, text = page(hwnd)
        lines = (" ".join(line.replace("￼", "").split()) for line in text.splitlines())  # ￼: an image
        capture.text = "\n".join(line for line in lines if line)
        if len(capture.text) > PAGE_TEXT:
            capture.text, capture.cut = capture.text[:PAGE_TEXT], True
    return capture


def program(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        path = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size))
        return Path(path.value).name.lower()
    finally:
        kernel32.CloseHandle(process)


def screenshot(hwnd):
    """The window as it is on screen, as PNG. GhostCue's own windows are left out of every capture, this one too."""
    box = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(box), ctypes.sizeof(box)):
        user32.GetWindowRect(hwnd, ctypes.byref(box))
    width, height = box.right - box.left, box.bottom - box.top
    if width <= 0 or height <= 0:
        raise CaptureError("That window has no size to capture.")
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    previous = gdi32.SelectObject(memory, bitmap)
    try:
        gdi32.BitBlt(memory, 0, 0, width, height, screen, box.left, box.top, SRCCOPY)
        header = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=width, biHeight=-height,
                                  biPlanes=1, biBitCount=32)  # negative height: rows from the top down
        pixels = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0):
            raise CaptureError("The window could not be captured.")
    finally:
        gdi32.SelectObject(memory, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)
    image = QImage(pixels, width, height, width * 4, QImage.Format_RGB32)
    if max(width, height) > LONGEST:
        image = image.scaled(LONGEST, LONGEST, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    data = QBuffer()
    data.open(QIODevice.WriteOnly)
    image.save(data, "PNG")
    return bytes(data.data())


def page(hwnd):
    """The address and the whole text of the web page showing in a browser window, through UI Automation.

    A browser builds the text only once something asks for it, so the first time it may
    take a moment: it is asked again until PAGE_WAIT has passed.
    """
    import comtypes.client
    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as UIA

    automation = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
    window = automation.ElementFromHandle(hwnd)
    is_document = automation.CreatePropertyCondition(UIA.UIA_ControlTypePropertyId, UIA.UIA_DocumentControlTypeId)
    deadline = time.monotonic() + PAGE_WAIT
    while True:
        # The page is the largest document on screen with a web address; others are developer tools and the like.
        best, best_url, best_area = None, "", 0
        documents = window.FindAll(UIA.TreeScope_Descendants, is_document)
        for i in range(documents.Length):
            document = documents.GetElement(i)
            try:
                url = str(document.GetCurrentPropertyValue(UIA.UIA_ValueValuePropertyId) or "")
                box = document.CurrentBoundingRectangle
                area = (box.right - box.left) * (box.bottom - box.top)
                if (url.startswith(("http://", "https://", "file:")) and not document.CurrentIsOffscreen
                        and area > best_area and document.GetCurrentPropertyValue(UIA.UIA_IsTextPatternAvailablePropertyId)):
                    best, best_url, best_area = document, url, area
            except comtypes.COMError:  # the page changed while it was being read
                continue
        if best is not None:
            try:
                pattern = best.GetCurrentPattern(UIA.UIA_TextPatternId).QueryInterface(UIA.IUIAutomationTextPattern)
                text = pattern.DocumentRange.GetText(-1)
            except comtypes.COMError:
                text = ""
            if text.strip():
                return best_url, text
        if time.monotonic() > deadline:
            return best_url, ""
        time.sleep(0.1)


class Capturer:
    """Takes captures on a thread of its own, which keeps COM set up between them."""

    def __init__(self):
        self.jobs = queue.SimpleQueue()
        threading.Thread(target=self.run, daemon=True).start()

    def capture(self, hwnd, done):
        """Capture the window `hwnd`, then call `done` on the capture thread with a Capture or a CaptureError."""
        self.jobs.put((hwnd, done))

    def run(self):
        import comtypes  # sets up COM on this thread if it is the first to import it
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except OSError:  # set up already, the same way
            pass
        while True:
            hwnd, done = self.jobs.get()
            try:
                result = take(hwnd)
            except CaptureError as error:
                result = error
            except Exception as error:  # an app that will not be read must not stop the capture thread
                result = CaptureError(f"The window could not be captured: {error}")
            done(result)


def main():
    """`python -m ghostcue capture`: show what Ctrl+Alt+W would send, without sending it."""
    from PySide6.QtGui import QGuiApplication

    from . import config

    app = QGuiApplication([])  # noqa: F841 - for the image functions
    if sys.stdout:
        sys.stdout.reconfigure(errors="replace")  # window titles hold characters the console may not
    for left in (3, 2, 1):
        print(f"Capturing the window in front in {left}… (click the window to capture)")
        time.sleep(1)
    done = threading.Event()
    result = []
    try:
        hwnd = front_window()
    except CaptureError as error:
        raise SystemExit(str(error))
    Capturer().capture(hwnd, lambda capture: (result.append(capture), done.set()))
    done.wait()
    capture = result[0]
    if isinstance(capture, CaptureError):
        raise SystemExit(str(capture))
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    picture, text = config.APP_DIR / "capture.png", config.APP_DIR / "capture.txt"
    picture.write_bytes(capture.png)
    text.write_text(capture.describe(), encoding="utf-8")
    print(f"\n{capture.title} ({capture.app})")
    print(f"Screenshot: {picture}")
    print(f"Text sent with it: {text}" + (f" ({len(capture.text):,} characters of page text)" if capture.text else ""))
