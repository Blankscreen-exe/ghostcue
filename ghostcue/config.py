"""Settings, stored as JSON in %APPDATA%\\GhostCue."""

import json
import os
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "GhostCue"
SETTINGS_FILE = APP_DIR / "settings.json"
HISTORY_DIR = APP_DIR / "history"

SPEECH_MODELS = ("tiny", "base", "small", "large-v3-turbo")
CALL_LANGUAGES = ("en", "ur", "mix")          # mix is Urdu and English, told apart sentence by sentence
ANSWER_LANGUAGES = ("english", "client", "roman_urdu")
CLAUDE_MODELS = ("haiku", "sonnet", "opus")
TRIGGERS = ("questions", "always", "hotkey")
LENGTHS = ("short", "standard", "detailed", "full")

DEFAULTS = {
    "context": "",            # the brief: what is being demoed, to whom, and how to answer
    "terms": "",              # product names and jargon, to help the speech recogniser spell them
    "knowledge_files": [],    # text files added to the brief as reference material
    "project_dir": "",        # folder Claude may read when asked to dig deeper
    "speaker_device": "",     # output device whose sound is the client; "" is the Windows default
    "mic_device": "",         # "" is the Windows default
    "capture_mic": True,
    "speech_model": "base",
    "call_language": "en",
    "answer_language": "english",
    "model": "haiku",
    "deep_model": "sonnet",
    "length": "short",
    "trigger": "questions",
    "font_size": 15,
    "opacity": 0.92,
    "geometry": None,         # overlay [x, y, width, height]
    "save_history": True,
}


def load():
    settings = dict(DEFAULTS)
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return settings
    settings.update({key: value for key, value in stored.items() if key in DEFAULTS})
    settings["speech_model"] = settings["speech_model"].removesuffix(".en")  # as stored by earlier versions
    return settings


def save(settings):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    temporary.replace(SETTINGS_FILE)
