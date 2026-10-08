"""GhostCue: a teleprompter overlay that screen recorders cannot see.

Reads a timed script (what to show, what to do, what to say, and when) and
shows the current cue in an always-on-top window. The window is excluded from
screen capture, so it is visible on the monitor but absent from recordings,
screenshots and screen shares. Windows 10 version 2004 or later.

    python ghostcue.py script.txt

The script format is specified in SCRIPT_FORMAT.md.
"""

import argparse
import ctypes
import math
import queue
import re
import signal
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog
from tkinter import font as tkfont

# ---------------------------------------------------------------- script ----

TIMESTAMP = r"(?:\d{1,2}:)?\d{1,3}:[0-5]\d"
HEADER_RE = re.compile(rf"^\[({TIMESTAMP})\]\s*(.*)$")
FIELD_RE = re.compile(r"^(SHOW|DO|SAY|NOTE)\s*:\s*(.*)$", re.IGNORECASE)
STRAY_LABEL_RE = re.compile(r"^([A-Z]{2,})\s*:")
WORDS_PER_SECOND = 2.5  # speaking pace, used to guess the last cue's length when there is no END line
MAX_ERRORS = 10


class ScriptError(Exception):
    """The script breaks the format; str() lists the problems, one per line."""


@dataclass
class Cue:
    start: float
    title: str = ""
    end: float = 0.0
    directions: list = field(default_factory=list)  # (kind, lines) in script order; kind is show, do or note
    say: list = field(default_factory=list)         # one entry per line to speak


def to_seconds(timestamp):
    seconds = 0
    for part in timestamp.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def fmt(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_script(text):
    """Parse a script into (cues, warnings); raises ScriptError naming each line that breaks the format."""
    cues, errors, warnings = [], [], []
    target = None  # the list that unlabelled lines are added to
    end = None     # the time on the closing "[M:SS] END" line
    preamble_reported = False

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if end is not None:
            errors.append(f"line {number}: nothing may follow the END line")
            break

        header = HEADER_RE.match(line)
        if header:
            start, title = to_seconds(header[1]), header[2]
            if cues and start <= cues[-1].start:
                errors.append(f"line {number}: [{header[1]}] is not later than the cue before it")
            if title.upper() == "END":
                end = start
            else:
                cues.append(Cue(start, title))
                target = None
            continue
        if re.match(r"\[\d", line):
            errors.append(f"line {number}: a cue header is written [M:SS] Title, got: {line}")
            continue
        if not cues:
            if not preamble_reported:
                errors.append(f"line {number}: text before the first cue; "
                              "start a cue with [0:00] Title, or turn this line into a # comment")
                preamble_reported = True
            continue

        labelled = FIELD_RE.match(line)
        if labelled:
            kind, line = labelled[1].lower(), labelled[2]
            if kind == "say":
                target = cues[-1].say
            else:
                target = []
                cues[-1].directions.append((kind, target))
            if not line:
                continue
        elif target is None:
            errors.append(f"line {number}: expected SHOW:, DO:, SAY: or NOTE: after the cue header, got: {line}")
            continue
        else:
            stray = STRAY_LABEL_RE.match(line)
            if stray:
                warnings.append(f"line {number}: '{stray[1]}:' is not a label (SHOW, DO, SAY, NOTE), "
                                "so the line is shown as ordinary text")
        target.append(line)

    if not cues and not errors:
        errors.append("no cues found; a cue starts with a line like [0:00] Title")
    if errors:
        if len(errors) > MAX_ERRORS:
            errors[MAX_ERRORS:] = [f"... and {len(errors) - MAX_ERRORS} more"]
        raise ScriptError("\n".join(errors))

    for cue, following in zip(cues, cues[1:]):
        cue.end = following.start
    last = cues[-1]
    words = sum(len(line.split()) for line in last.say)
    last.end = end if end is not None else last.start + max(5, round(words / WORDS_PER_SECOND))
    for cue in cues:
        cue.directions = [(kind, lines) for kind, lines in cue.directions if lines]
        if not cue.directions and not cue.say:
            warnings.append(f"cue [{fmt(cue.start)}] {cue.title} has nothing to show, do or say")
    return cues, warnings


def select(cues, start, end):
    """The cues that begin in [start, end), with times shifted so that `start` becomes 0:00."""
    kept = [cue for cue in cues if cue.start >= start and (end is None or cue.start < end)]
    for cue in kept:
        cue.start -= start
        cue.end -= start
    return kept


def describe(name, cues, warnings):
    """Print how a script was parsed (--check)."""
    print(f"{name}: {len(cues)} cues, {fmt(cues[0].start)} to {fmt(cues[-1].end)}\n")
    for number, cue in enumerate(cues, start=1):
        print(f"{number:>3}  {fmt(cue.start)}-{fmt(cue.end)}  {cue.title}")
        for kind, lines in [*cue.directions, ("say", cue.say)]:
            for n, line in enumerate(lines):
                print(f"       {kind.upper() if n == 0 else '':<4}  {line}")
    for warning in warnings:
        print(f"\nWARNING: {warning}")


# ----------------------------------------------------------------- win32 ----

WDA_EXCLUDEFROMCAPTURE = 0x11  # Windows 10 2004+: on the monitor, absent from every capture
GA_ROOT = 2
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x80        # no taskbar button, not listed in Alt+Tab
WS_EX_NOACTIVATE = 0x08000000  # clicking the overlay never takes focus from the app being demoed
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4
WM_HOTKEY = 0x0312
MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x1, 0x2, 0x4000

# Global hotkeys, all pressed with Ctrl+Alt: (virtual-key code, key name, action, hint)
# Start/pause and next/previous sit under the left hand, so the mouse hand stays on the mouse.
HOTKEYS = (
    (0x20, "Space", "toggle", "start/pause"),
    (0x58, "X", "next", "next"),
    (0x5A, "Z", "prev", "previous"),
    (0x52, "R", "restart", "restart"),
    (0x4D, "M", "mode", "manual/auto"),
    (0x26, "Up", "bigger", "bigger"),
    (0x28, "Down", "smaller", "smaller"),
    (0x48, "H", "hide", "hide"),
    (0x51, "Q", "quit", "quit"),
)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
user32.SetWindowDisplayAffinity.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.GetWindowDisplayAffinity.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
get_window_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
set_window_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
get_window_long.restype = set_window_long.restype = ctypes.c_ssize_t
get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)


class Hotkeys(threading.Thread):
    """Listens for the global hotkeys on its own message loop and queues their actions."""

    def __init__(self):
        super().__init__(daemon=True)
        self.pressed = queue.SimpleQueue()
        self.failed = []  # key names another program already owns
        self.ready = threading.Event()

    def run(self):
        for i, (vk, name, _action, _hint) in enumerate(HOTKEYS):
            if not user32.RegisterHotKey(None, i, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
                self.failed.append(name)
        self.ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                self.pressed.put(HOTKEYS[msg.wParam][2])


# --------------------------------------------------------------- overlay ----

FONT = "Segoe UI"
BG, TRACK, DIM, WHITE = "#0f1115", "#2a2f3a", "#8b93a1", "#f3f5f8"
AMBER, GREEN, RED, BLUE = "#ffc857", "#4ade80", "#ff6b6b", "#7cc4ff"
DIRECTION_COLOURS = (("show", BLUE), ("do", AMBER), ("note", DIM))
DIRECTIONS_SHARE = 0.4  # part of the width given to SHOW/DO/NOTE when a cue also has words
TICK_MS = 100


class Prompter:
    def __init__(self, cues, args):
        self.cues = cues
        self.index = 0
        self.auto = not args.manual
        self.banked = 0.0       # seconds on the clock before the current run
        self.started_at = None  # monotonic time the clock last started; None while paused
        self.entered = 0.0      # clock reading when the current cue came up
        self.wheeled = set()    # panes scrolled by hand, which stop auto-scrolling until the cue changes
        self.next_tick = None   # id of the scheduled tick, cancelled on quit
        self.size = args.font_size
        self.capturable = args.show_in_capture
        self.hidden = False
        self.closed = False

        self.hotkeys = Hotkeys()
        self.hotkeys.start()
        self.hotkeys.ready.wait(2)
        signal.signal(signal.SIGINT, lambda *_: self.hotkeys.pressed.put("quit"))

        focused = user32.GetForegroundWindow()
        root = self.root = tk.Tk()
        root.title("GhostCue")
        root.overrideredirect(True)
        root.configure(bg=BG)
        # Start fully transparent: the window only becomes visible once it is excluded from capture.
        root.attributes("-topmost", True, "-alpha", 0.0)
        scale = root.winfo_fpixels("1i") / 96

        def px(n):
            return round(n * scale)

        self.min_size = (px(580), px(160))  # narrower than this and the header no longer fits
        width, height = px(900), px(360)
        x, y = (root.winfo_screenwidth() - width) // 2, root.winfo_screenheight() - height - px(80)
        root.geometry(args.geometry or f"{width}x{height}+{x}+{y}")

        self.say_font = tkfont.Font(family=FONT, size=self.size)
        self.direction_font = tkfont.Font(family=FONT, size=max(self.size - 4, 8))
        small = tkfont.Font(family=FONT, size=9)
        tag_font = tkfont.Font(family=FONT, size=8, weight="bold")

        def label(parent, **options):
            return tk.Label(parent, bg=BG, fg=DIM, font=small, **options)

        def button(parent, text, command, **options):
            """A clickable label. Pressing it does not start a window drag."""
            widget = label(parent, text=text, cursor="hand2", **options)
            widget.bind("<ButtonPress-1>", lambda event: "break")
            widget.bind("<B1-Motion>", lambda event: "break")
            widget.bind("<ButtonRelease-1>", lambda event: command())
            widget.bind("<Enter>", lambda event: widget.configure(fg=WHITE))
            widget.bind("<Leave>", lambda event: widget.configure(fg=DIM))
            return widget

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=px(12), pady=(px(8), px(5)))
        self.status = label(header)
        self.status.pack(side="left")
        button(header, "✕", self.on_quit).pack(side="right")
        button(header, "next ›", self.on_next).pack(side="right", padx=(px(6), px(16)))
        self.play = button(header, "start", self.on_toggle, width=5)
        self.play.pack(side="right", padx=px(6))
        button(header, "‹ prev", self.on_prev).pack(side="right", padx=px(6))
        self.capture = label(header)
        self.capture.pack(side="right", padx=px(14))
        self.countdown = label(header)
        self.countdown.pack(side="right")
        self.where = label(header, anchor="w")
        self.where.pack(side="left", fill="x", expand=True, padx=px(14))

        self.bar = tk.Canvas(root, height=px(3), bg=TRACK, highlightthickness=0)
        self.bar.pack(fill="x")
        self.fill = self.bar.create_rectangle(0, 0, 0, 0, width=0, fill=GREEN)

        hint = label(root, anchor="w", text="Ctrl+Alt +   " + "   ".join(f"{key} {what}" for _, key, _, what in HOTKEYS))
        if self.hotkeys.failed:
            taken = ", ".join(self.hotkeys.failed)
            hint.configure(fg=RED, text=f"Hotkeys already used by another app: Ctrl+Alt + {taken}")
        hint.pack(side="bottom", fill="x", padx=px(12), pady=(0, px(6)))
        self.next = label(root, anchor="w")
        self.next.pack(side="bottom", fill="x", padx=px(12), pady=(0, px(2)))

        # Directions (SHOW/DO/NOTE) on the left stay put while the words on the right are read.
        panes = tk.Frame(root, bg=BG)
        panes.pack(fill="both", expand=True)

        def pane():
            text = tk.Text(panes, bg=BG, bd=0, highlightthickness=0, wrap="word", cursor="arrow",
                           padx=px(12), pady=px(4), state="disabled", takefocus=False)
            # Drop the Text class bindings so dragging moves the window instead of selecting text.
            text.bindtags((str(text), str(root), "all"))
            return text

        self.directions, self.words = pane(), pane()
        self.divider = tk.Frame(panes, bg=TRACK)
        indent = tag_font.measure("SHOW") + px(10)
        self.directions.configure(tabs=(indent,))
        for kind, colour in DIRECTION_COLOURS:
            self.directions.tag_configure(kind, font=self.direction_font, foreground=colour, lmargin2=indent)
        self.directions.tag_configure("label", font=tag_font)
        self.directions.tag_configure("first", spacing1=px(7))
        self.directions.tag_configure("more", lmargin2=indent + px(14))  # a direction's 2nd and later lines
        self.words.tag_configure("say", font=self.say_font, foreground=WHITE, lmargin2=px(18), spacing3=px(6))

        grip = tk.Label(root, text="◢", bg=BG, fg=TRACK, font=small, cursor="size_nw_se")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<ButtonPress-1>", self.resize_start)
        grip.bind("<B1-Motion>", self.resize_move)
        root.bind_all("<ButtonPress-1>", self.drag_start)
        root.bind_all("<B1-Motion>", self.drag_move)
        root.bind_all("<MouseWheel>", self.wheel)

        self.show_cue()
        root.update()
        hwnd = self.hwnd()
        set_window_long(hwnd, GWL_EXSTYLE, get_window_long(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
        # Tk activates a window the first time it appears; give the focus straight back.
        if focused:
            user32.SetForegroundWindow(focused)
        self.refresh(0)
        root.attributes("-alpha", max(0.2, min(args.opacity, 1.0)))

    def run(self):
        self.tick()
        self.root.mainloop()

    # ---- clock and cues ----

    def elapsed(self):
        running = time.monotonic() - self.started_at if self.started_at is not None else 0
        return self.banked + running

    def seek(self, index):
        """Jump to a cue. In auto mode the clock jumps with it, so the timing stays in sync."""
        self.index = index
        if self.auto:
            self.banked = self.cues[index].start
            if self.started_at is not None:
                self.started_at = time.monotonic()
        self.show_cue()

    def tick(self):
        while not self.hotkeys.pressed.empty():
            getattr(self, "on_" + self.hotkeys.pressed.get())()
            if self.closed:
                return
        elapsed = self.elapsed()
        if self.auto:
            index = self.index
            while index + 1 < len(self.cues) and elapsed >= self.cues[index + 1].start:
                index += 1
            if index != self.index:
                self.index = index
                self.show_cue()
        self.refresh(elapsed)
        self.next_tick = self.root.after(TICK_MS, self.tick)

    # ---- hotkey actions ----

    def on_toggle(self):
        if self.started_at is None:
            self.started_at = time.monotonic()
        else:
            self.banked, self.started_at = self.elapsed(), None

    def on_next(self):
        if self.index + 1 < len(self.cues):
            self.seek(self.index + 1)

    def on_prev(self):
        self.seek(max(self.index - 1, 0))

    def on_restart(self):
        self.banked, self.started_at, self.index = 0.0, None, 0
        self.show_cue()

    def on_mode(self):
        self.auto = not self.auto

    def on_bigger(self):
        self.resize_text(1)

    def on_smaller(self):
        self.resize_text(-1)

    def on_hide(self):
        self.hidden = not self.hidden
        user32.ShowWindow(self.hwnd(), SW_HIDE if self.hidden else SW_SHOWNOACTIVATE)

    def on_quit(self):
        self.closed = True
        self.root.after_cancel(self.next_tick)
        self.root.destroy()

    # ---- drawing ----

    def show_cue(self):
        cue = self.cues[self.index]
        self.entered = self.elapsed()
        self.wheeled.clear()
        for pane in (self.directions, self.words):
            pane.configure(state="normal")
            pane.delete("1.0", "end")
        for kind, lines in cue.directions:
            for n, line in enumerate(lines):
                if n == 0:
                    self.directions.insert("end", kind.upper() + "\t", ("label", kind, "first"))
                    self.directions.insert("end", line + "\n", kind)
                else:
                    self.directions.insert("end", "\t" + line + "\n", (kind, "more"))
        for line in cue.say:
            self.words.insert("end", line + "\n", "say")
        for pane in (self.directions, self.words):
            pane.delete("end-2c")  # the last line's newline, which would read as an extra blank line
            pane.configure(state="disabled")
            pane.yview_moveto(0)

        # A cue that has only directions, or only words, gives that pane the full width.
        share = DIRECTIONS_SHARE if cue.directions and cue.say else float(bool(cue.directions))
        self.directions.place(relx=0, rely=0, relwidth=share, relheight=1)
        self.words.place(relx=share, rely=0, relwidth=1 - share, relheight=1)
        if 0 < share < 1:
            self.divider.place(relx=share, rely=0.06, relheight=0.88, width=1)
        else:
            self.divider.place_forget()

        if self.index + 1 < len(self.cues):
            following = self.cues[self.index + 1]
            opening = following.directions[0][1][0] if following.directions else "".join(following.say[:1])
            preview = " — ".join(filter(None, [following.title, opening]))
            self.next.configure(text=f"NEXT  {fmt(following.start)}   {preview}")
        else:
            self.next.configure(text="NEXT  end of script")

    def refresh(self, elapsed):
        cue = self.cues[self.index]
        length = max(cue.end - cue.start, 0.001)
        remaining = cue.end - elapsed
        over = remaining < 0  # only happens on the last cue, or in manual mode when running behind
        progress = min(max((elapsed - cue.start) / length, 0), 1)

        if self.started_at is not None:
            state, colour = "LIVE", GREEN
        elif elapsed:
            state, colour = "PAUSED", AMBER
        else:
            state, colour = "READY", DIM
        self.status.configure(text=f"{state}  {fmt(elapsed)} / {fmt(self.cues[-1].end)}", fg=colour)
        self.play.configure(text="start" if self.started_at is None else "pause")
        mode = "" if self.auto else "   [manual]"
        self.where.configure(text=f"{self.index + 1}/{len(self.cues)}  {cue.title}{mode}")
        if over:
            self.countdown.configure(text=f"over by {fmt(-remaining)}", fg=RED)
        else:
            ending = "ends" if self.index == len(self.cues) - 1 else "next"
            self.countdown.configure(text=f"{ending} in {fmt(math.ceil(remaining))}", fg=DIM)
        self.bar.coords(self.fill, 0, 0, self.bar.winfo_width() * progress, self.bar.winfo_height())
        self.bar.itemconfigure(self.fill, fill=RED if over else GREEN)

        # A pane too long for the window scrolls through its text over the cue's planned length,
        # counted from when the cue came up so it also works in manual mode.
        scroll = min(max((elapsed - self.entered) / length, 0), 1)
        for pane in (self.directions, self.words):
            top, bottom = pane.yview()
            if bottom - top < 1 and pane not in self.wheeled:
                pane.yview_moveto(scroll * (1 - (bottom - top)))

        if not self.capturable and self.guard():
            self.capture.configure(text="hidden from capture", fg=DIM)
        else:
            self.capture.configure(text="VISIBLE TO RECORDERS", fg=RED)

    def resize_text(self, delta):
        self.size = max(8, min(self.size + delta, 40))
        self.say_font.configure(size=self.size)
        self.direction_font.configure(size=max(self.size - 4, 8))

    # ---- window ----

    def hwnd(self):
        return user32.GetAncestor(self.root.winfo_id(), GA_ROOT)

    def guard(self):
        """Keep the window excluded from screen capture; True while it is.

        Checked on every tick, so the exclusion is put back if Tk ever rebuilds the window.
        """
        hwnd = self.hwnd()
        affinity = wintypes.DWORD()
        user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity))
        if affinity.value == WDA_EXCLUDEFROMCAPTURE:
            return True
        return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))

    def wheel(self, event):
        """Scroll the pane under the pointer by hand; it stops auto-scrolling until the cue changes."""
        pane = event.widget if event.widget in (self.directions, self.words) else self.words
        self.wheeled.add(pane)
        pane.yview_scroll(-event.delta // 120 * 2, "units")

    def drag_start(self, event):
        self.grab = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def drag_move(self, event):
        self.root.geometry(f"+{event.x_root - self.grab[0]}+{event.y_root - self.grab[1]}")

    def resize_start(self, event):
        self.grab = (self.root.winfo_width() - event.x_root, self.root.winfo_height() - event.y_root)
        return "break"

    def resize_move(self, event):
        width = max(event.x_root + self.grab[0], self.min_size[0])
        height = max(event.y_root + self.grab[1], self.min_size[1])
        self.root.geometry(f"{width}x{height}")
        return "break"


def timestamp(value):
    if not re.fullmatch(TIMESTAMP, value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a time such as 5:00")
    return to_seconds(value)


def main():
    parser = argparse.ArgumentParser(description="Teleprompter overlay that screen recorders cannot see.")
    parser.add_argument("script", nargs="?", help="script file (see SCRIPT_FORMAT.md); omit to pick one in a dialog")
    parser.add_argument("--check", action="store_true", help="print how the script was parsed, then exit")
    parser.add_argument("--from", dest="start", type=timestamp, default=0, metavar="M:SS",
                        help="use only the cues from this time on, re-timed so it becomes 0:00")
    parser.add_argument("--to", dest="end", type=timestamp, metavar="M:SS",
                        help="use only the cues that start before this time")
    parser.add_argument("--manual", action="store_true", help="change cues only with the hotkeys, not by the clock")
    parser.add_argument("--font-size", type=int, default=15, metavar="PT", help="text size (default 15)")
    parser.add_argument("--opacity", type=float, default=0.92, metavar="0-1", help="window opacity (default 0.92)")
    parser.add_argument("--geometry", metavar="WxH+X+Y", help="window size and position in pixels")
    parser.add_argument("--show-in-capture", action="store_true",
                        help="do NOT hide the overlay from recorders (for screenshots of the overlay itself)")
    args = parser.parse_args()

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
    except (AttributeError, OSError):
        pass
    if sys.stdout:
        sys.stdout.reconfigure(errors="replace")

    if not args.script:
        picker = tk.Tk()
        picker.withdraw()
        args.script = filedialog.askopenfilename(
            title="Choose a script", filetypes=[("Scripts", "*.txt *.md"), ("All files", "*.*")]
        )
        picker.destroy()
        if not args.script:
            return

    try:
        text = Path(args.script).read_text(encoding="utf-8-sig", errors="replace")
    except OSError as error:
        sys.exit(f"Cannot read script: {error}")
    try:
        cues, warnings = parse_script(text)
    except ScriptError as error:
        sys.exit(f"{args.script} is not a valid GhostCue script (see SCRIPT_FORMAT.md):\n{error}")
    if args.start or args.end is not None:
        cues = select(cues, args.start, args.end)
        if not cues:
            sys.exit("No cues start inside the --from/--to range.")
    if args.check:
        describe(Path(args.script).name, cues, warnings)
        return
    for warning in warnings:
        print(f"WARNING: {warning}")
    Prompter(cues, args).run()


if __name__ == "__main__":
    main()
