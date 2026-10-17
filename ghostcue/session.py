"""A live call: listen to both sides, transcribe them, and ask Claude what to say."""

import collections
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path

import pyaudiowpatch as pyaudio
from PySide6.QtCore import QObject, Signal

from . import config
from .audio import CLIENT, ME, AudioError, Source
from .engine import Engine, build_prompt

# Speech that asks for something: a question mark, an opening question word, or a request to explain.
ASKING = re.compile(
    r"\?"
    r"|^\W*(?:(?:so|and|but|ok|okay|well|right|um|uh|now)\W+)*"
    r"(?:what|how|why|when|where|who|which|can|could|would|will|does|do|did|is|are|was|were|should|has|have)\b"
    r"|\b(?:tell me|tell us|explain|walk me through|walk us through|show me|show us|wondering|curious|clarify"
    r"|elaborate|what about|how about|help me understand|not sure (?:how|what|why|if)|i'd like to (?:know|see|understand))\b"
    # Urdu: its question mark, the question words (kya, kyun, kaise, kahan, kab, kaun, kitna) and "tell me", "explain".
    r"|؟|کیا|کیوں|کیسے|کیسا|کہاں|کب|کون|کتن|بتا|سمجھا|ایکسپلین",
    re.IGNORECASE)

REQUESTS = {
    "auto": "Answer the client's latest question.",
    "now": "The presenter has asked for help right now. Say what the presenter should say next in reply to the "
           "client's most recent point, whether or not it was put as a question. Do not reply with a hyphen.",
    "deep": "Look in the product's files and answer the client's latest question in more depth. "
            "Do not reply with a hyphen.",
    "more": "The presenter wants more on the question you last answered: «{}». Answer it again in full. "
            "Explain how it works and why, cover every type, step or option it involves with one explained "
            "point each, and take the length that needs. Do not reply with a hyphen.",
    "typed": "The client's question was misheard, so the presenter has typed it: «{}». "
             "Answer that question. Do not reply with a hyphen.",
}
LANGUAGES = {"en": "English", "ur": "Urdu", "mix": "Urdu + English"}
WARM = "The call is about to start. Reply with only the word: ready"
RECENT = 40  # transcript lines given to the file-reading engine, which is only sent the call when asked


class Session(QObject):
    """Runs one call. Signals are emitted from worker threads and delivered on the Qt thread."""

    state = Signal(str, str)       # "starting", "listening", "stopped" or "error", and a line of detail
    heard = Signal(str, str)       # speaker, what they said
    thinking = Signal(str)         # what is being worked on, or "" when nothing is
    answer = Signal(int, str, str, str)  # the request number, its kind, the question, and the reply so far
    level = Signal(str, float)     # speaker, sound level

    def __init__(self):
        super().__init__()
        self.running = False
        self.transcriber = self.transcriber_key = None
        self.audio = self.main = self.deep = self.history = None
        self.sources = []

    # ---- starting and stopping ----

    def start(self, settings):
        if self.running:
            return
        self.running = True
        self.settings = dict(settings)
        self.utterances = queue.SimpleQueue()
        self.lines = []                               # the call so far, as (speaker, text)
        self.sent = 0                                 # how many of them the main engine has been given
        self.client_spans = collections.deque(maxlen=50)
        self.questions, self.kinds, self.replies = {}, {}, {}  # by request number
        self.requests = 0
        self.last_question = ""
        self.warming = set()
        self.began = time.monotonic()
        self.state.emit("starting", "Loading the speech model…")
        threading.Thread(target=self.open, daemon=True).start()

    def open(self):
        settings = self.settings
        try:
            key = (settings["speech_model"], settings["call_language"], settings["terms"])
            if key != self.transcriber_key:
                from .stt import Transcriber
                self.transcriber, self.transcriber_key = Transcriber(*key), key

            self.state.emit("starting", "Starting Claude…")
            self.main = Engine(build_prompt(settings), settings["model"], self.on_event)
            self.warm(self.main, "main")
            folder = settings["project_dir"]
            if folder and Path(folder).is_dir():
                self.deep = Engine(build_prompt(settings, deep=True), settings["deep_model"], self.on_event, folder)
                self.warm(self.deep, "deep")

            self.audio = pyaudio.PyAudio()
            wanted = [(CLIENT, settings["speaker_device"])]
            if settings["capture_mic"]:
                wanted.append((ME, settings["mic_device"]))
            for speaker, name in wanted:
                try:
                    self.sources.append(Source(self.audio, speaker, name, self.utterances.put, self.level.emit))
                except AudioError as error:
                    if speaker == CLIENT:
                        raise
                    self.heard.emit("", f"Microphone not captured: {error}")
            for source in self.sources:
                source.start()

            if settings["save_history"]:
                config.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
                name = datetime.now().strftime("%Y-%m-%d %H.%M.%S.md")
                self.history = open(config.HISTORY_DIR / name, "w", encoding="utf-8")
                self.history.write(f"# Call on {datetime.now():%d %B %Y at %H:%M}\n\n")
        except Exception as error:  # a session that cannot start must say why, whatever the reason
            self.close()
            self.state.emit("error", str(error))
            return
        if not self.running:  # stopped while it was starting
            self.close()
            return
        self.report()
        threading.Thread(target=self.transcribe, args=(self.utterances,), daemon=True).start()

    def warm(self, engine, name):
        """Have an engine answer once, so the wait for Claude Code to start is over before the call."""
        self.warming.add(name)
        engine.ask(WARM, ("warm", name))

    def report(self):
        listening = " and ".join(source.device_name for source in self.sources)
        note = "   Claude is still starting…" if self.warming else ""
        language = LANGUAGES[self.settings["call_language"]]
        self.state.emit("listening", f"{language}. Listening to {listening}.{note}")

    def stop(self):
        if not self.running:
            return
        self.running = False
        self.utterances.put(None)
        self.close()
        self.thinking.emit("")
        self.state.emit("stopped", "")

    def close(self):
        self.running = False
        for source in self.sources:
            source.stop()
        self.sources = []
        for engine in (self.main, self.deep):
            if engine:
                engine.close()
        self.main = self.deep = None
        if self.audio:
            self.audio.terminate()
            self.audio = None
        if self.history:
            self.history.close()
            self.history = None

    # ---- listening ----

    def transcribe(self, utterances):
        while True:
            utterance = utterances.get()
            if utterance is None or not self.running:
                return
            if utterance.speaker == CLIENT:
                self.client_spans.append((utterance.started, utterance.ended))
            elif self.is_echo(utterance):
                continue
            try:
                text = self.transcriber.transcribe(utterance.audio)
            except Exception as error:
                self.heard.emit("", f"Could not transcribe: {error}")
                continue
            if not text or not self.running:
                continue
            self.lines.append((utterance.speaker, text))
            self.record(f"**{utterance.speaker}** ({self.clock()}): {text}\n\n")
            self.heard.emit(utterance.speaker, text)
            if utterance.speaker == CLIENT and self.wants_answer(text):
                self.ask("auto")

    def is_echo(self, utterance):
        """True if the microphone was mostly picking up the client's voice from the speakers."""
        spans = list(self.client_spans)
        for source in self.sources:
            if source.speaker == CLIENT and source.speaking_since:
                spans.append((source.speaking_since, time.monotonic()))
        shared = sum(max(0.0, min(utterance.ended, end) - max(utterance.started, start)) for start, end in spans)
        return shared > 0.5 * (utterance.ended - utterance.started)

    def wants_answer(self, text):
        trigger = self.settings["trigger"]
        if trigger == "always":
            return True
        return trigger == "questions" and len(text.split()) >= 3 and bool(ASKING.search(text))

    # ---- answering ----

    def ask(self, kind, typed=""):
        """Ask Claude what to say.

        `kind` is "auto" after a client question, "now" on the hotkey, "deep" to read the
        files, "more" to answer the last question again at length, or "typed" to answer the
        question the presenter typed in `typed`.
        """
        if not self.running or not self.main:
            return
        question = typed or next((text for speaker, text in reversed(self.lines) if speaker == CLIENT), "")
        if kind == "more":
            question = typed = self.last_question or question
        if kind == "typed":
            self.record(f"**Typed** ({self.clock()}): {typed}\n\n")
        if kind == "deep":
            if not self.deep:
                self.heard.emit("", "To dig deeper, set a project folder in Settings › Knowledge.")
                return
            engine, lines = self.deep, self.lines[-RECENT:]
        else:
            engine, lines = self.main, self.lines[self.sent:]
            self.sent = len(self.lines)
        if not question and kind != "now":
            return
        transcript = "\n".join(f"{speaker}: {text}" for speaker, text in lines)
        self.requests += 1
        self.questions[self.requests] = self.last_question = question
        self.kinds[self.requests] = kind
        self.thinking.emit("Reading the files…" if kind == "deep" else "Thinking…")
        engine.ask(f"{transcript}\n\n{REQUESTS[kind].format(typed)}".strip(), self.requests)

    def on_event(self, tag, kind, text):
        if isinstance(tag, tuple):  # the reply to a warm-up
            if kind in ("done", "error"):
                self.warming.discard(tag[1])
                if kind == "error":
                    self.fail(text)
                elif self.running and self.sources:
                    self.report()
            return
        if kind == "error":
            self.fail(text)
            return
        if tag is None or not self.running:
            return
        if kind == "working":
            self.thinking.emit(f"Reading the files… ({text})")
            return
        reply = text if kind == "done" else self.replies.get(tag, "") + text
        self.replies[tag] = reply
        # A lone hyphen means "no reply needed": leave the last answer on screen.
        if reply.strip() not in ("", "-"):
            self.answer.emit(tag, self.kinds.get(tag, ""), self.questions.get(tag, ""), reply.strip())
        if kind == "done":
            self.thinking.emit("")
            if reply.strip() not in ("", "-"):
                self.record(f"> **Answer to:** {self.questions.get(tag, '')}\n>\n"
                            + "\n".join("> " + line for line in reply.strip().splitlines()) + "\n\n")
            self.questions.pop(tag, None)
            self.kinds.pop(tag, None)
            self.replies.pop(tag, None)

    def fail(self, reason):
        if self.running:
            self.running = False
            self.utterances.put(None)
            self.close()
            self.thinking.emit("")
            self.state.emit("error", reason)

    def clock(self):
        elapsed = int(time.monotonic() - self.began)
        return f"{elapsed // 60}:{elapsed % 60:02d}"

    def record(self, text):
        if self.history:
            try:
                self.history.write(text)
                self.history.flush()
            except (OSError, ValueError):
                pass


class Meter(QObject):
    """Shows the sound levels of the chosen devices while no call is running."""

    level = Signal(str, float)
    problem = Signal(str)

    def __init__(self):
        super().__init__()
        self.audio = None
        self.sources = []

    def start(self, settings):
        self.stop()
        self.audio = pyaudio.PyAudio()
        problems = []
        for speaker, name in ((CLIENT, settings["speaker_device"]), (ME, settings["mic_device"])):
            try:
                source = Source(self.audio, speaker, name, on_level=self.level.emit)
            except AudioError as error:
                problems.append(str(error))
                continue
            source.start()
            self.sources.append(source)
        self.problem.emit("  ".join(problems))

    def stop(self):
        for source in self.sources:
            source.stop()
        self.sources = []
        if self.audio:
            self.audio.terminate()
            self.audio = None
