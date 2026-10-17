"""The answer overlay: always on top, never focused, and absent from screen shares."""

import html

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizeGrip, QSizePolicy, QTextBrowser,
                               QVBoxLayout, QWidget)

from .win32 import exclude_from_capture, take_keyboard, user32

FONT = "Segoe UI"
BG, TRACK, DIM, WHITE = "#0f1115", "#2a2f3a", "#8b93a1", "#f3f5f8"
AMBER, GREEN, RED, BLUE = "#ffc857", "#4ade80", "#ff6b6b", "#7cc4ff"
CODE, CODE_BG = "#d7e3f4", "#1b1f27"

# Global hotkeys, all pressed with Ctrl+Alt: (virtual-key code, key name, action, hint).
# None of them is a key the teleprompter uses, so both can run at once.
HOTKEYS = (
    (0x41, "A", "now", "answer"),
    (0x44, "D", "deep", "dig deeper"),
    (0x54, "T", "type", "type"),
    (0x45, "E", "more", "explain more"),
    (0x53, "S", "show", "show/hide"),
    (0x4C, "L", "listen", "start/stop"),
)

STYLE = f"""
QWidget#overlay {{ background: {BG}; }}
QLabel {{ color: {DIM}; font: 9pt "{FONT}"; }}
QPushButton {{ color: {DIM}; font: 9pt "{FONT}"; border: none; padding: 0 6px; background: transparent; }}
QPushButton:hover {{ color: {WHITE}; }}
QTextBrowser {{ background: {BG}; border: none; }}
QScrollBar:vertical {{ width: 6px; background: {BG}; }}
QScrollBar::handle:vertical {{ background: {TRACK}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""

QUESTION_SHOWN = 200  # characters of the client's question shown above the answer

STATES = {"starting": ("STARTING", AMBER), "listening": ("LISTENING", GREEN),
          "stopped": ("OFF", DIM), "error": ("ERROR", RED)}


def render(question, reply, size):
    """The reply as HTML: the sentence to say in white, its supporting points below in blue."""
    parts = []
    heading = "CLIENT"
    first, _, rest = reply.partition("\n")
    if first.startswith("Q:"):  # the question as the assistant understood it, which is the one it answered
        heading, question, reply = "ASKED", first[2:].strip(), rest
    if len(question) > QUESTION_SHOWN:  # a long question is known by how it ended
        question = "…" + question[-QUESTION_SHOWN:]
    if question:
        parts.append(f'<p style="color:{DIM}; font-size:{max(size - 5, 8)}pt; margin:0 0 6px 0">'
                     f'{heading}&nbsp;&nbsp;{html.escape(question)}</p>')
    code = None  # the lines of the code block being read, when inside one

    def block():
        return (f'<pre style="color:{CODE}; background-color:{CODE_BG}; font-family:Consolas; '
                f'font-size:{max(size - 3, 8)}pt; margin:4px 0 6px 0">{html.escape(chr(10).join(code))}</pre>')

    for line in reply.splitlines():
        if line.strip().startswith("```"):
            if code is not None:
                parts.append(block())
            code = [] if code is None else None
            continue
        if code is not None:
            code.append(line)
            continue
        line = line.strip()
        if not line:
            continue
        if line[:2] in ("- ", "• ", "* "):
            point = line[2:].strip()
            colour = AMBER if point.lower().startswith("not in brief") else BLUE
            parts.append(f'<p style="color:{colour}; font-size:{max(size - 3, 8)}pt; margin:3px 0 0 14px; '
                         f'text-indent:-12px">•&nbsp;{html.escape(point)}</p>')
        else:
            parts.append(f'<p style="color:{WHITE}; font-size:{size}pt; margin:0 0 4px 0">{html.escape(line)}</p>')
    if code:  # a block still being written
        parts.append(block())
    return "".join(parts)


class Overlay(QWidget):
    action = Signal(str)   # a header button was pressed: "now", "deep", "type", "more", "listen" or "show"
    moved = Signal()       # the window was moved or resized by hand

    def __init__(self, settings, hotkeys_failed=()):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setObjectName("overlay")
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.setMinimumSize(460, 150)
        self.size_pt = settings["font_size"]
        self.opacity = settings["opacity"]
        self.question = self.reply = ""
        self.held = None

        def button(text, action):
            widget = QPushButton(text)
            widget.setCursor(Qt.PointingHandCursor)
            widget.setFocusPolicy(Qt.NoFocus)
            widget.clicked.connect(lambda: self.action.emit(action))
            return widget

        header = QHBoxLayout()
        header.setContentsMargins(12, 8, 8, 4)
        self.status = QLabel()
        self.note = QLabel()
        self.note.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # cut short rather than widen the window
        self.capture = QLabel()
        self.toggle = button("start", "listen")
        header.addWidget(self.status)
        header.addSpacing(10)
        header.addWidget(self.note, 1)
        header.addSpacing(10)
        header.addWidget(self.capture)
        header.addSpacing(10)
        header.addWidget(button("answer", "now"))
        header.addWidget(button("deeper", "deep"))
        header.addWidget(button("type", "type"))
        header.addWidget(button("more", "more"))
        header.addWidget(self.toggle)
        header.addWidget(button("✕", "show"))

        self.body = QTextBrowser()
        self.body.setFocusPolicy(Qt.NoFocus)
        self.body.setTextInteractionFlags(Qt.NoTextInteraction)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.viewport().setCursor(Qt.ArrowCursor)
        self.body.document().setDocumentMargin(12)
        self.body.setFont(QFont(FONT, self.size_pt))
        self.body.viewport().installEventFilter(self)

        self.transcript = QLabel()
        self.transcript.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        hint = QLabel("Ctrl+Alt +   " + "   ".join(f"{key} {what}" for _, key, _, what in HOTKEYS))
        if hotkeys_failed:
            hint.setText("Hotkeys already used by another app: Ctrl+Alt + " + ", ".join(hotkeys_failed))
            hint.setStyleSheet(f"color: {RED}")
        footer = QHBoxLayout()
        footer.setContentsMargins(12, 0, 2, 2)
        footer.addWidget(hint, 1)
        footer.addWidget(QSizeGrip(self), 0, Qt.AlignBottom | Qt.AlignRight)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self.body, 1)
        self.transcript.setContentsMargins(12, 2, 12, 4)
        layout.addWidget(self.transcript)
        layout.addLayout(footer)

        geometry = settings["geometry"]
        if geometry and self.on_a_screen(geometry):
            self.setGeometry(*geometry)
        else:
            self.place_default()
        self.set_state("stopped", "")
        self.show_answer("", "")

        # Checked every second, so the exclusion is put back if Qt ever rebuilds the window.
        self.guard = QTimer(self, interval=1000, timeout=self.check_capture)

    def place_default(self):
        screen = self.screen().availableGeometry()
        width, height = 820, 300
        self.setGeometry(screen.x() + (screen.width() - width) // 2, screen.bottom() - height - 60, width, height)

    def on_a_screen(self, geometry):
        return any(screen.availableGeometry().intersects(QRect(*geometry))
                   for screen in self.screen().virtualSiblings())

    # ---- showing ----

    def reveal(self):
        """Show the window, making it visible only once it is excluded from capture."""
        self.setWindowOpacity(0.0)
        self.show()
        self.check_capture()
        self.setWindowOpacity(max(0.2, min(self.opacity, 1.0)))
        self.guard.start()

    def conceal(self):
        self.guard.stop()
        self.hide()

    def check_capture(self):
        if exclude_from_capture(int(self.winId())):
            self.capture.setText("hidden from capture")
            self.capture.setStyleSheet(f"color: {DIM}")
        else:
            self.capture.setText("VISIBLE TO SCREEN SHARE")
            self.capture.setStyleSheet(f"color: {RED}")

    def set_state(self, state, detail):
        text, colour = STATES[state]
        self.state = state
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour}; font-weight: 600")
        self.detail = detail
        self.note.setText(detail)
        self.note.setStyleSheet(f"color: {RED if state == 'error' else DIM}")
        self.toggle.setText("stop" if state in ("starting", "listening") else "start")
        if state == "error":
            self.question, self.reply = "", ""
            self.body.setHtml(f'<p style="color:{RED}; font-size:{self.size_pt - 2}pt">{html.escape(detail)}</p>')
            self.note.setText("")

    def set_thinking(self, what):
        self.note.setText(what or self.detail)
        self.note.setStyleSheet(f"color: {AMBER if what else DIM}")

    def show_heard(self, speaker, text):
        self.transcript.setText(f"{speaker.upper()}  {text}" if speaker else text)
        self.transcript.setStyleSheet(f"color: {DIM if speaker else AMBER}")

    def show_answer(self, question, reply):
        self.question, self.reply = question, reply
        if reply:
            self.body.setHtml(render(question, reply, self.size_pt))
        else:
            self.body.setHtml(f'<p style="color:{DIM}; font-size:{max(self.size_pt - 3, 8)}pt">'
                              "Answers appear here when the client asks something.<br>"
                              "Press Ctrl+Alt+A to get one at any moment.</p>")

    def apply(self, settings):
        """Take up changed overlay settings."""
        self.size_pt, self.opacity = settings["font_size"], settings["opacity"]
        if self.isVisible():
            self.setWindowOpacity(max(0.2, min(self.opacity, 1.0)))
        if self.state != "error":
            self.show_answer(self.question, self.reply)

    # ---- moving ----

    def eventFilter(self, watched, event):
        # The text area would swallow the drag; pass its mouse events to the window.
        if event.type() in (event.Type.MouseButtonPress, event.Type.MouseMove, event.Type.MouseButtonRelease):
            {event.Type.MouseButtonPress: self.mousePressEvent, event.Type.MouseMove: self.mouseMoveEvent,
             event.Type.MouseButtonRelease: self.mouseReleaseEvent}[event.type()](event)
            return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.held = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.held is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.held)

    def mouseReleaseEvent(self, event):
        if self.held is not None:
            self.held = None
            self.moved.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible():
            self.moved.emit()

    def geometry_list(self):
        box = self.geometry()
        return [box.x(), box.y(), box.width(), box.height()]


class AskBar(QWidget):
    """A line to type the client's question into, when it was misheard.

    Unlike the overlay it has to take the keyboard, so it appears only when asked for,
    under the overlay, and hands the keyboard back to the app being demoed when it closes.
    It too is absent from screen shares.
    """

    asked = Signal(str)

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setObjectName("overlay")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setStyleSheet(STYLE + f"""
            QLineEdit {{ color: {WHITE}; background: {CODE_BG}; border: 1px solid {BLUE}; border-radius: 4px;
                         padding: 6px 8px; font: 12pt "{FONT}"; selection-background-color: {TRACK}; }}""")
        self.line = QLineEdit()
        self.line.returnPressed.connect(self.submit)
        self.line.installEventFilter(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.line)
        self.previous = None

    def open(self, under):
        """Show the line below the rectangle `under` (above it, if there is no room below) and take the keyboard."""
        if self.isVisible():
            self.line.setFocus()
            return
        self.previous = user32.GetForegroundWindow()
        height = self.sizeHint().height()
        below = under.bottom() + 1 + height <= self.screen().availableGeometry().bottom()
        self.setGeometry(under.x(), under.bottom() + 1 if below else under.y() - height, under.width(), height)
        self.setWindowOpacity(0.0)  # invisible until it is excluded from capture
        self.show()
        hidden = exclude_from_capture(int(self.winId()))
        self.line.setPlaceholderText(("Type the client's question." if hidden else "VISIBLE TO SCREEN SHARE.")
                                     + "   Enter to ask, Esc to cancel")
        self.setWindowOpacity(0.97)
        take_keyboard(int(self.winId()))
        self.activateWindow()
        self.line.setFocus()

    def submit(self):
        text = self.line.text().strip()
        self.dismiss()
        if text:
            self.line.clear()
            self.asked.emit(text)

    def dismiss(self):
        """Close, and give the keyboard back to the app that had it."""
        if not self.isVisible():
            return
        self.hide()
        if self.previous:
            user32.SetForegroundWindow(self.previous)

    def eventFilter(self, watched, event):
        if event.type() == event.Type.KeyPress and event.key() == Qt.Key_Escape:
            self.dismiss()
            return True
        if event.type() == event.Type.FocusOut and self.isVisible():
            self.hide()  # the presenter clicked elsewhere: leave the keyboard where it went
        return super().eventFilter(watched, event)
