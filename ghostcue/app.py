"""The tray app: ties the tray icon, the settings window, the overlay and the call together."""

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import config
from .overlay import AMBER, BG, DIM, GREEN, HOTKEYS, RED, TRACK, WHITE, AskBar, Overlay
from .session import Meter, Session
from .settings_window import CALL_NOTES, SettingsWindow
from .win32 import Hotkeys, set_app_id, single_instance

STATE_COLOURS = {"starting": AMBER, "listening": GREEN, "stopped": DIM, "error": RED}
GHOST = Path(__file__).with_name("ghost.png")  # the app's icon

STYLE = f"""
QWidget {{ font: 10pt "Segoe UI"; }}
QListWidget#sidebar {{ background: {BG}; border: none; padding-top: 14px; outline: 0; font-size: 11pt; }}
QListWidget#sidebar::item {{ padding: 9px 20px; color: {DIM}; }}
QListWidget#sidebar::item:selected {{ color: {WHITE}; background: {TRACK}; }}
QLabel#note {{ color: {DIM}; font-size: 9pt; }}
QPushButton {{ padding: 6px 14px; }}
QPushButton#primary {{ background: #2f6fed; color: white; border: none; border-radius: 4px; padding: 7px 18px; }}
QPushButton#primary:hover {{ background: #4a83f5; }}
QProgressBar {{ background: {TRACK}; border: none; border-radius: 4px; }}
QProgressBar::chunk {{ background: {GREEN}; border-radius: 4px; }}
QPlainTextEdit, QLineEdit, QListWidget, QTextBrowser {{ border: 1px solid {TRACK}; border-radius: 4px; padding: 4px; }}
"""


def dark_palette():
    palette = QPalette()
    for role, colour in ((QPalette.Window, "#171a21"), (QPalette.WindowText, WHITE), (QPalette.Base, BG),
                         (QPalette.AlternateBase, "#171a21"), (QPalette.Text, WHITE), (QPalette.Button, "#232833"),
                         (QPalette.ButtonText, WHITE), (QPalette.Highlight, "#2f6fed"),
                         (QPalette.HighlightedText, "#ffffff"), (QPalette.ToolTipBase, BG),
                         (QPalette.ToolTipText, WHITE), (QPalette.PlaceholderText, "#5d6573")):
        palette.setColor(role, QColor(colour))
    return palette


def icon(colour):
    """The tray icon: the ghost, with a dot on its corner that shows the state."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    painter.drawPixmap(0, 0, 64, 64, QPixmap(str(GHOST)))
    painter.setPen(QPen(QColor(BG), 4))  # a dark ring keeps the dot apart from the ghost
    painter.setBrush(QColor(colour))
    painter.drawEllipse(38, 38, 24, 24)
    painter.end()
    return QIcon(pixmap)


class Bridge(QObject):
    """Carries hotkey presses from the hotkey thread to the Qt thread."""
    pressed = Signal(str)


class App:
    def __init__(self, app):
        self.app = app
        self.settings = config.load()
        self.session = Session()
        self.meter = Meter()
        self.bridge = Bridge()
        self.hotkeys = Hotkeys(HOTKEYS, on_press=self.bridge.pressed.emit)
        self.hotkeys.start()
        self.hotkeys.ready.wait(2)
        self.overlay = Overlay(self.settings, self.hotkeys.failed)
        self.ask_bar = AskBar()
        self.window = SettingsWindow(self.settings)
        self.save_later = QTimer(singleShot=True, interval=500, timeout=lambda: config.save(self.settings))

        self.tray = QSystemTrayIcon(icon(DIM))
        menu = self.menu = QMenu()
        self.listen_item = menu.addAction("Start listening", self.toggle_listening)
        self.overlay_item = menu.addAction("Show overlay", self.toggle_overlay)
        languages = menu.addMenu("Call language")
        self.language_items = QActionGroup(languages)
        for code in config.CALL_LANGUAGES:
            item = languages.addAction(CALL_NOTES[code], lambda code=code: self.window.choose_call_language(code))
            item.setCheckable(True)
            item.setData(code)
            self.language_items.addAction(item)
        menu.addSeparator()
        menu.addAction("Settings…", self.open_settings)
        menu.addAction("Teleprompter…", self.open_teleprompter)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_clicked)
        self.tray.show()

        self.bridge.pressed.connect(self.act)
        self.overlay.action.connect(self.act)
        self.ask_bar.asked.connect(lambda text: self.session.ask("typed", text))
        self.overlay.moved.connect(self.overlay_moved)
        self.window.listen.connect(self.toggle_listening)
        self.window.changed.connect(self.settings_changed)
        self.window.reset_overlay.connect(self.reset_overlay)
        self.window.page_shown.connect(self.page_shown)
        self.session.state.connect(self.on_state)
        self.session.heard.connect(self.overlay.show_heard)
        self.session.thinking.connect(self.overlay.set_thinking)
        self.session.answer.connect(self.overlay.show_answer)
        self.session.level.connect(self.window.set_level)
        self.meter.level.connect(self.window.set_level)
        self.meter.problem.connect(self.window.set_audio_problem)
        self.on_state("stopped", "")

        if self.settings["context"].strip():
            self.tray.showMessage("GhostCue", "Running in the tray. Press Ctrl+Alt+L to start listening.",
                                  icon(GREEN), 4000)
        else:
            self.open_settings()  # first run: there is no brief yet

    # ---- actions ----

    def act(self, action):
        if action == "listen":
            self.toggle_listening()
        elif action == "show":
            self.toggle_overlay()
        elif action == "type":
            self.overlay.reveal()
            if self.session.running:
                self.ask_bar.open(self.overlay.geometry())
            else:
                self.overlay.show_heard("", "Start listening first (Ctrl+Alt+L); then you can type a question.")
            self.update_menu()
        elif action == "window" and not self.session.running:
            self.overlay.reveal()
            self.overlay.show_heard("", "Start listening first (Ctrl+Alt+L); then you can capture a window.")
            self.update_menu()
        elif action in ("back", "forward"):
            if not self.overlay.isVisible():
                self.overlay.reveal()
            self.overlay.step(-1 if action == "back" else 1)
            self.update_menu()
        else:
            self.session.ask(action)

    def toggle_listening(self):
        if self.session.running:
            self.session.stop()
            return
        config.save(self.settings)
        self.meter.stop()
        self.overlay.clear()
        self.overlay.show_heard("", "")
        self.overlay.reveal()
        self.session.start(self.settings)
        self.update_menu()

    def toggle_overlay(self):
        if self.overlay.isVisible():
            self.overlay.conceal()
        else:
            self.overlay.reveal()
        self.update_menu()

    def open_settings(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def open_teleprompter(self):
        command = [sys.executable, "prompter"] if getattr(sys, "frozen", False) else [sys.executable, "-m", "ghostcue", "prompter"]
        subprocess.Popen(command, cwd=Path(__file__).resolve().parent.parent)

    def tray_clicked(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_settings()

    def quit(self):
        self.ask_bar.hide()
        self.session.stop()
        self.meter.stop()
        self.hotkeys.stop()
        config.save(self.settings)
        self.tray.hide()
        self.app.quit()

    # ---- reactions ----

    def on_state(self, state, detail):
        self.overlay.set_state(state, detail)
        self.window.set_state(state, detail)
        self.tray.setIcon(icon(STATE_COLOURS[state]))
        self.tray.setToolTip("GhostCue: " + {"starting": "starting", "listening": "listening",
                                            "stopped": "not listening", "error": "stopped by an error"}[state])
        if state == "error":
            self.tray.showMessage("GhostCue stopped", detail, QSystemTrayIcon.Warning, 8000)
        if state in ("stopped", "error"):
            for speaker in self.window.meters:
                self.window.set_level(speaker, 0.0)
            self.page_shown(self.window.current_page() if self.window.isVisible() else "")
        self.update_menu()

    def update_menu(self):
        self.listen_item.setText("Stop listening" if self.session.running else "Start listening")
        self.overlay_item.setText("Hide overlay" if self.overlay.isVisible() else "Show overlay")
        for item in self.language_items.actions():
            item.setChecked(item.data() == self.settings["call_language"])

    def settings_changed(self):
        self.save_later.start()
        self.overlay.apply(self.settings)
        self.update_menu()
        if self.meter.sources:  # the devices may be the setting that changed
            self.meter.start(self.settings)

    def overlay_moved(self):
        self.settings["geometry"] = self.overlay.geometry_list()
        self.save_later.start()

    def reset_overlay(self):
        self.overlay.place_default()
        self.overlay.reveal()
        self.overlay_moved()
        self.update_menu()

    def page_shown(self, name):
        """Run the level meters while the Audio page is in view and no call is using the devices."""
        if name == "Audio" and not self.session.running:
            self.meter.start(self.settings)
        else:
            self.meter.stop()


def main():
    if sys.stderr is None:  # started without a console: keep what libraries print, for troubleshooting
        config.APP_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(config.APP_DIR / "ghostcue.log", "w", encoding="utf-8", buffering=1)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    set_app_id("GhostCue.Assistant")
    app = QApplication(sys.argv)
    app.setApplicationName("GhostCue")
    app.setWindowIcon(QIcon(str(GHOST)))
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE)
    if not single_instance("GhostCue.Assistant"):
        QMessageBox.information(None, "GhostCue", "GhostCue is already running. Look for its icon in the system tray.")
        return
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "GhostCue", "This desktop has no system tray.")
        return
    ghostcue = App(app)  # noqa: F841 - keeps the app's objects alive for the event loop
    sys.exit(app.exec())
