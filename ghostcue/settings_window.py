"""The settings window: a sidebar of pages for the brief, knowledge, audio, assistant, overlay and history."""

import os
from pathlib import Path

import pyaudiowpatch as pyaudio
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSpinBox,
                               QSplitter, QStackedWidget, QTextBrowser, QVBoxLayout, QWidget)

from . import audio, config
from .audio import CLIENT, ME
from .overlay import HOTKEYS

CONTEXT_EXAMPLE = """\
Write whatever the assistant should know before the call. For example:

I am demoing FuelRoute, a route planner that picks the cheapest fuel stops for long-haul trucks.
The client is the operations team of a 200-truck carrier. They are not technical.
They care about fuel savings, driver adoption and how long rollout takes.
Pricing is $12 per truck per month; do not offer discounts.
Keep answers plain and confident. Never promise a feature that is not listed here."""

AUDIO_NOTE = ("Play some sound and speak: both bars should move. Speech is transcribed on this computer; "
              "a model is downloaded the first time it is used.")
SPEECH_NOTES = {"tiny": "fastest, least accurate", "base": "recommended for English",
                "small": "needed for Urdu; 2 to 3 seconds a sentence", "large-v3-turbo": "most accurate; needs an NVIDIA graphics card"}
CALL_NOTES = {"en": "English", "ur": "Urdu", "mix": "Mix (Urdu + English)"}
ANSWER_NOTES = {"english": "English", "client": "The client's language (Urdu in Roman letters)", "roman_urdu": "Roman Urdu, always"}
MODEL_NOTES = {"haiku": "fastest", "sonnet": "more thorough, slower", "opus": "most capable, slowest"}
TRIGGER_NOTES = {"questions": "When the client asks a question", "always": "Every time the client stops speaking",
                 "hotkey": "Only when I press Ctrl+Alt+A"}
LENGTH_NOTES = {"short": "Short: one sentence and up to three points",
                "standard": "Standard: two or three sentences and up to five points",
                "detailed": "Detailed: a short paragraph and up to eight explained points",
                "full": "Full: a complete explanation, as long as the subject needs"}


def heading(title, text):
    label = QLabel(f"<h2 style='margin:0'>{title}</h2><p style='color:#8b93a1'>{text}</p>")
    label.setWordWrap(True)
    return label


def choice(options, notes, current, named=True):
    """A drop-down of `options`, each shown with its note, or as its note alone."""
    box = QComboBox()
    for option in options:
        box.addItem(f"{option}  ({notes[option]})" if named else notes[option], option)
    box.setCurrentIndex(max(box.findData(current), 0))
    return box


class SettingsWindow(QWidget):
    changed = Signal()            # a setting was edited; self.settings holds the new values
    listen = Signal()             # the start/stop button was pressed
    reset_overlay = Signal()
    page_shown = Signal(str)      # the name of the page now in view, or "" when the window is hidden

    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.setWindowTitle("GhostCue")
        self.resize(860, 600)
        self.loading = True
        self.later = QTimer(self, singleShot=True, interval=300, timeout=self.changed.emit)

        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(170)
        self.sidebar.setObjectName("sidebar")
        self.pages = QStackedWidget()
        for name, build in (("Context", self.context_page), ("Knowledge", self.knowledge_page),
                            ("Audio", self.audio_page), ("Assistant", self.assistant_page),
                            ("Overlay", self.overlay_page), ("History", self.history_page)):
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(24, 20, 24, 12)
            layout.setSpacing(10)
            build(layout)
            self.sidebar.addItem(name)
            self.pages.addWidget(page)
        self.sidebar.currentRowChanged.connect(self.show_page)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.listen_button = QPushButton("Start listening")
        self.listen_button.setObjectName("primary")
        self.listen_button.clicked.connect(self.listen.emit)
        footer = QHBoxLayout()
        footer.setContentsMargins(24, 8, 24, 14)
        footer.addWidget(self.status, 1)
        footer.addWidget(self.listen_button)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(self.pages, 1)
        right.addLayout(footer)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.sidebar)
        layout.addLayout(right, 1)

        self.set_state("stopped", "")
        self.sidebar.setCurrentRow(0)
        self.loading = False

    # ---- pages ----

    def context_page(self, layout):
        layout.addWidget(heading("Context", "The brief the assistant works from: what you are showing, to whom, "
                                            "and how you want questions answered."))
        self.call_language = choice(config.CALL_LANGUAGES, CALL_NOTES, self.settings["call_language"], named=False)
        self.call_language.currentIndexChanged.connect(self.set_call_language)
        language = QFormLayout()
        language.addRow("Call language", self.call_language)
        layout.addLayout(language)
        self.context = QPlainTextEdit(self.settings["context"])
        self.context.setPlaceholderText(CONTEXT_EXAMPLE)
        self.context.textChanged.connect(lambda: self.set("context", self.context.toPlainText()))
        layout.addWidget(self.context, 1)
        self.terms = QLineEdit(self.settings["terms"])
        self.terms.setPlaceholderText("FuelRoute, OSRM, Acme Logistics")
        self.terms.textChanged.connect(lambda text: self.set("terms", text))
        form = QFormLayout()
        form.addRow("Names and jargon", self.terms)
        layout.addLayout(form)
        note = QLabel("Names and jargon are given to the speech recogniser, so that it spells them correctly.")
        note.setObjectName("note")
        layout.addWidget(note)

    def knowledge_page(self, layout):
        layout.addWidget(heading("Knowledge", "Documents the assistant can quote from: FAQs, pricing, specifications, "
                                              "a README. Plain text or Markdown. They are read in full at the start "
                                              "of each call."))
        self.files = QListWidget()
        self.files.addItems(self.settings["knowledge_files"])
        layout.addWidget(self.files, 1)
        self.files_size = QLabel()
        self.files_size.setObjectName("note")
        add = QPushButton("Add files…")
        remove = QPushButton("Remove")
        add.clicked.connect(self.add_files)
        remove.clicked.connect(self.remove_file)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addWidget(remove)
        row.addWidget(self.files_size, 1, Qt.AlignRight)
        layout.addLayout(row)

        layout.addSpacing(8)
        layout.addWidget(heading("Project folder", "Optional. With Ctrl+Alt+D the assistant searches this folder to "
                                                   "answer in more depth. It can only read files, and takes several "
                                                   "seconds longer than a normal answer."))
        self.folder = QLineEdit(self.settings["project_dir"])
        self.folder.setPlaceholderText("No folder")
        self.folder.textChanged.connect(lambda text: self.set("project_dir", text.strip()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.pick_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        self.count_files()

    def audio_page(self, layout):
        layout.addWidget(heading("Audio", "The client is whatever your speakers or headphones play. You are the "
                                          "microphone. Wear headphones, so the microphone does not pick the client up."))
        self.speaker = QComboBox()
        self.mic = QComboBox()
        self.meters = {}
        form = QFormLayout()
        form.setVerticalSpacing(8)
        for speaker, box, label in ((CLIENT, self.speaker, "Client (speakers)"), (ME, self.mic, "You (microphone)")):
            meter = QProgressBar()
            meter.setRange(0, 100)
            meter.setTextVisible(False)
            meter.setFixedHeight(8)
            self.meters[speaker] = meter
            form.addRow(label, box)
            form.addRow("", meter)
        self.capture_mic = QCheckBox("Listen to my microphone too, so the assistant knows what I have already said")
        self.capture_mic.setChecked(self.settings["capture_mic"])
        self.capture_mic.toggled.connect(lambda on: self.set("capture_mic", on))
        form.addRow("", self.capture_mic)
        self.speech = choice(config.SPEECH_MODELS, SPEECH_NOTES, self.settings["speech_model"])
        self.speech.currentIndexChanged.connect(lambda: self.set("speech_model", self.speech.currentData()))
        form.addRow("Speech model", self.speech)
        layout.addLayout(form)
        self.audio_note = QLabel(AUDIO_NOTE)
        self.audio_note.setObjectName("note")
        self.audio_note.setWordWrap(True)
        layout.addWidget(self.audio_note)
        layout.addStretch(1)
        self.list_devices()
        self.speaker.currentIndexChanged.connect(lambda: self.set("speaker_device", self.speaker.currentData()))
        self.mic.currentIndexChanged.connect(lambda: self.set("mic_device", self.mic.currentData()))

    def assistant_page(self, layout):
        layout.addWidget(heading("Assistant", "Answers come from the Claude Code installed on this computer, "
                                              "signed in as you."))
        self.model = choice(config.CLAUDE_MODELS, MODEL_NOTES, self.settings["model"])
        self.deep_model = choice(config.CLAUDE_MODELS, MODEL_NOTES, self.settings["deep_model"])
        self.trigger = choice(config.TRIGGERS, TRIGGER_NOTES, self.settings["trigger"], named=False)
        self.length = choice(config.LENGTHS, LENGTH_NOTES, self.settings["length"], named=False)
        self.answer_language = choice(config.ANSWER_LANGUAGES, ANSWER_NOTES, self.settings["answer_language"], named=False)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        for label, key, box in (("Answer", "trigger", self.trigger), ("Answer length", "length", self.length),
                                ("Answer in", "answer_language", self.answer_language),
                                ("Model", "model", self.model), ("Model for digging deeper", "deep_model", self.deep_model)):
            box.currentIndexChanged.connect(lambda _index, key=key, box=box: self.set(key, box.currentData()))
            form.addRow(label, box)
        self.save_history = QCheckBox("Keep a transcript of each call, with the answers given")
        self.save_history.setChecked(self.settings["save_history"])
        self.save_history.toggled.connect(lambda on: self.set("save_history", on))
        form.addRow("", self.save_history)
        layout.addLayout(form)
        layout.addStretch(1)

    def overlay_page(self, layout):
        layout.addWidget(heading("Overlay", "The window that shows the answers. You see it on your monitor; "
                                            "a screen share, recording or screenshot does not."))
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 40)
        self.font_size.setSuffix(" pt")
        self.font_size.setValue(self.settings["font_size"])
        self.font_size.valueChanged.connect(lambda value: self.set("font_size", value))
        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(20, 100)
        self.opacity.setValue(round(self.settings["opacity"] * 100))
        self.opacity.valueChanged.connect(lambda value: self.set("opacity", value / 100))
        reset = QPushButton("Move it back to the bottom of the screen")
        reset.clicked.connect(self.reset_overlay.emit)
        form = QFormLayout()
        form.setVerticalSpacing(12)
        form.addRow("Text size", self.font_size)
        form.addRow("Opacity", self.opacity)
        form.addRow("Position", reset)
        keys = "".join(f"<tr><td style='padding-right:18px'><b>Ctrl+Alt+{key}</b></td><td>{what}</td></tr>"
                       for _, key, _, what in HOTKEYS)
        form.addRow("Hotkeys", QLabel(f"<table>{keys}</table>"))
        layout.addLayout(form)
        note = QLabel("Drag the overlay to move it and drag its bottom-right corner to resize it. The hotkeys work "
                      "while any app has focus. Your mouse pointer is still shared, so prefer the hotkeys to the "
                      "overlay's buttons during a call.")
        note.setObjectName("note")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

    def history_page(self, layout):
        layout.addWidget(heading("History", "Transcripts of past calls, with the answers the assistant gave."))
        self.calls = QListWidget()
        self.calls.setFixedWidth(190)
        self.call = QTextBrowser()
        self.calls.currentTextChanged.connect(self.show_call)
        split = QSplitter()
        split.addWidget(self.calls)
        split.addWidget(self.call)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        folder = QPushButton("Open folder")
        delete = QPushButton("Delete")
        folder.clicked.connect(self.open_history)
        delete.clicked.connect(self.delete_call)
        row = QHBoxLayout()
        row.addWidget(folder)
        row.addWidget(delete)
        row.addStretch(1)
        layout.addLayout(row)

    # ---- behaviour ----

    def set(self, key, value):
        if self.loading or self.settings.get(key) == value:
            return
        self.settings[key] = value
        self.later.start()

    def choose_call_language(self, language):
        self.call_language.setCurrentIndex(self.call_language.findData(language))

    def set_call_language(self):
        """Take the call's language, and with it the choices that only make sense for that language."""
        language = self.call_language.currentData()
        if language == self.settings["call_language"]:
            return
        self.set("call_language", language)
        if language != "en":
            if self.speech.currentData() in ("tiny", "base"):  # too small to follow Urdu
                self.speech.setCurrentIndex(self.speech.findData("small"))
            if self.answer_language.currentData() == "english":
                self.answer_language.setCurrentIndex(self.answer_language.findData("client"))

    def show_page(self, row):
        self.pages.setCurrentIndex(row)
        name = self.sidebar.item(row).text()
        if name == "History":
            self.list_calls()
        if self.isVisible():
            self.page_shown.emit(name)

    def showEvent(self, event):
        super().showEvent(event)
        self.page_shown.emit(self.sidebar.currentItem().text())

    def hideEvent(self, event):
        super().hideEvent(event)
        self.page_shown.emit("")

    def set_state(self, state, detail):
        running = state in ("starting", "listening")
        self.listen_button.setText("Stop listening" if running else "Start listening")
        if state == "stopped":
            detail = "Changes take effect the next time you start listening."
        self.status.setText(detail)
        self.status.setStyleSheet("color: #ff6b6b" if state == "error" else "color: #8b93a1")

    def current_page(self):
        return self.sidebar.currentItem().text()

    def set_audio_problem(self, problem):
        self.audio_note.setText(problem or AUDIO_NOTE)
        self.audio_note.setStyleSheet("color: #ff6b6b" if problem else "")

    def set_level(self, speaker, level):
        # Speech sits around 0.02 to 0.2; show it on a scale the eye can follow.
        self.meters[speaker].setValue(min(100, round((level ** 0.5) * 220)))

    def list_devices(self):
        ports = pyaudio.PyAudio()
        try:
            speakers, microphones = audio.devices(ports)
        finally:
            ports.terminate()
        for box, names, key in ((self.speaker, speakers, "speaker_device"), (self.mic, microphones, "mic_device")):
            box.addItem("Windows default", "")
            for name in names:
                box.addItem(name, name)
            current = self.settings[key]
            if current and box.findData(current) < 0:
                box.addItem(f"{current} (not connected)", current)
            box.setCurrentIndex(max(box.findData(current), 0))

    def add_files(self):
        names, _ = QFileDialog.getOpenFileNames(self, "Add knowledge files", "",
                                                "Text (*.txt *.md *.markdown *.csv *.json *.rst);;All files (*)")
        files = self.settings["knowledge_files"]
        added = [str(Path(name)) for name in names if str(Path(name)) not in files]
        if added:
            self.files.addItems(added)
            self.set("knowledge_files", files + added)
            self.count_files()

    def remove_file(self):
        row = self.files.currentRow()
        if row >= 0:
            self.files.takeItem(row)
            files = list(self.settings["knowledge_files"])
            del files[row]
            self.set("knowledge_files", files)
            self.count_files()

    def count_files(self):
        size = missing = 0
        for name in self.settings["knowledge_files"]:
            try:
                size += os.path.getsize(name)
            except OSError:
                missing += 1
        text = f"about {size // 4:,} tokens" if size else ""
        if missing:
            text += f"   {missing} missing"
        self.files_size.setText(text)

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Project folder", self.settings["project_dir"])
        if folder:
            self.folder.setText(str(Path(folder)))

    def list_calls(self):
        current = self.calls.currentItem().text() if self.calls.currentItem() else None
        self.calls.clear()
        names = sorted((path.stem for path in config.HISTORY_DIR.glob("*.md")), reverse=True)
        self.calls.addItems(names)
        if names:
            self.calls.setCurrentRow(names.index(current) if current in names else 0)
        else:
            self.call.setMarkdown("No calls yet.")

    def show_call(self, name):
        if not name:
            return
        try:
            self.call.setMarkdown((config.HISTORY_DIR / f"{name}.md").read_text(encoding="utf-8"))
        except OSError as error:
            self.call.setPlainText(str(error))

    def delete_call(self):
        item = self.calls.currentItem()
        if item:
            try:
                (config.HISTORY_DIR / f"{item.text()}.md").unlink(missing_ok=True)
            except OSError:  # the call still in progress
                return
            self.call.clear()
            self.list_calls()

    def open_history(self):
        config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(config.HISTORY_DIR)
