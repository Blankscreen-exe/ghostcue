"""The Win32 calls GhostCue needs: capture exclusion, window styles and global hotkeys."""

import ctypes
import queue
import threading
from ctypes import wintypes

WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004+: on the monitor, absent from every capture
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80        # no taskbar button, not listed in Alt+Tab
WS_EX_NOACTIVATE = 0x08000000  # clicking the overlay never takes focus from the app being demoed
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000
ERROR_ALREADY_EXISTS = 183

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.GetWindowDisplayAffinity.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
get_window_long.restype = set_window_long.restype = ctypes.c_ssize_t
get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)


def exclude_from_capture(hwnd):
    """Keep a window out of screen captures; True while it is excluded."""
    affinity = wintypes.DWORD()
    user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
    if affinity.value == WDA_EXCLUDEFROMCAPTURE:
        return True
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))


def take_keyboard(hwnd):
    """Make a window the one that is typed into; True if it now is.

    Windows refuses this to a program that is not already in front, unless it shares the
    input state of the one that is, so that is borrowed for the moment of the switch.
    """
    ours = kernel32.GetCurrentThreadId()
    theirs = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    attached = theirs and theirs != ours and user32.AttachThreadInput(ours, theirs, True)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(ours, theirs, False)
    return user32.GetForegroundWindow() == hwnd


def single_instance(name):
    """True if this is the only running process that has claimed `name`."""
    single_instance.handle = kernel32.CreateMutexW(None, False, name)  # held until the process exits
    return ctypes.get_last_error() != ERROR_ALREADY_EXISTS


class Hotkeys(threading.Thread):
    """Listens for global Ctrl+Alt hotkeys on its own message loop and queues their actions.

    `table` is a sequence of (virtual-key code, key name, action, hint).
    """

    def __init__(self, table, on_press=None):
        super().__init__(daemon=True)
        self.table = table
        self.on_press = on_press  # called on this thread with the action; default is to queue it
        self.pressed = queue.SimpleQueue()
        self.failed = []  # key names another program already owns
        self.ready = threading.Event()

    def run(self):
        self.thread_id = kernel32.GetCurrentThreadId()
        for i, (vk, name, _action, _hint) in enumerate(self.table):
            if not user32.RegisterHotKey(None, i, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
                self.failed.append(name)
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                action = self.table[msg.wParam][2]
                (self.on_press or self.pressed.put)(action)

    def stop(self):
        """End the message loop, which releases the hotkeys."""
        if self.ready.is_set():
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
