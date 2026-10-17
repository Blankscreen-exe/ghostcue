"""The answering side: a Claude Code process kept running for the length of a call.

Claude Code takes several seconds to start, so it is started once, with the
brief as its system prompt, and then fed questions over stdin. Each answer
streams back in well under a second, and the process remembers the call so far.
It uses the Claude Code login already on this machine.
"""

import collections
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import config

MAX_REFERENCE = 400_000  # characters of reference files; more than this would crowd out the call itself

ROLE = """\
You are helping a presenter during a live product demo call. Nobody on the call can see you. \
You are sent the call as a running transcript: lines marked "Client:" are what the people being \
shown the product said, and lines marked "Me:" are what the presenter said. The transcript comes \
from speech recognition, so words are sometimes misheard, product names above all; work out what \
was meant.

Each message ends with a request, usually to answer the client's latest question. Reply with what \
the presenter should say next. The presenter reads your reply off a small overlay while talking, so:
- Start with the answer itself: {answer} in the presenter's own voice, first person, plainly \
spoken, ready to be said aloud as written.
- Then {points}, each on its own line starting with "- ": the facts, figures or places on screen \
that back the answer up. {each}
- When the question is about a set of things (the types of something, the steps, the options, \
the strategies), give every member of the set, one point each: its name and what it is. Do this \
even when it takes more points than the number above; a list with members missing is a wrong answer.
- When the client or the presenter asks for something to be explained, or explained in detail, \
explain it: say how it works and why, at whatever length that takes, not just what it is called.
- When the client asks for syntax, code or a command, show it. After the opening sentence, put \
the code between two lines of three backticks, twelve lines at most, and then the points.
- Nothing else. No greeting, no heading, no "you could say", no bold or other formatting.
- What you say about the product, the company, its prices, dates and commitments must come from \
the brief, the reference material or the call itself. If they do not cover the question, do not \
guess. Give an honest holding answer the presenter can say, and add a point starting \
"Not in brief:" naming what is missing.
- General knowledge that any expert in the field has (how a technology works, the syntax of a \
language, common practice) you may answer from what you know. That is not a gap in the brief, and needs no "Not in brief:" point.
- If the client's last words need no reply (a greeting, an acknowledgement, small talk), reply \
with only a single hyphen.
"""

DEEP = """
You can read the product's own files in the current folder. Look there for anything the brief does \
not settle, and say which file a fact came from. Be quick: the presenter is waiting on the call. \
Read only what the question needs.
"""

LANGUAGES = {
    "english": "Always reply in English, whatever language the client speaks.",
    "client": "Reply in the language the client last spoke. When that is Urdu or Hindi, write Roman Urdu: Urdu in "
              "Latin letters, with English technical words kept in English, the way Pakistani professionals speak. "
              "Write the points the same way.",
    "roman_urdu": "Always reply in Roman Urdu: Urdu in Latin letters, with English technical words kept in English, "
                  "the way Pakistani professionals speak. Write the points the same way.",
}

URDU_CALL = """
The client may speak Urdu, or Urdu and English mixed. The speech recognition is at its weakest \
there: the transcript may be in Urdu or Hindi script, with English words spelled out by sound and \
some words wrong. Read it for its sound and its sense.

Because the recognition is rough, begin each reply with one line starting "Q: " that restates the \
client's question as you understood it, in at most twelve words, in the language of your reply. The \
presenter uses it to check that you heard the question right. Then give the answer and points as \
described above. When you reply with only a hyphen, leave this line out.
"""

# For each answer length: the opening, the number of points, and how much each point says.
LENGTHS = {
    "short": ("one sentence,", "up to three short points", "Keep each under 15 words."),
    "standard": ("two or three sentences,", "up to five points", "Keep each under 20 words."),
    "detailed": ("a short paragraph of three to five sentences,", "up to eight points",
                 "Make each a full sentence that explains its fact, not a bare label."),
    "full": ("a paragraph that explains the matter properly,", "as many points as the subject has",
             "Give each a sentence or two of explanation, so that the presenter could talk for a "
             "couple of minutes from them alone."),
}


def build_prompt(settings, deep=False):
    """The system prompt: how to answer, then the brief and the reference files."""
    answer, points, each = LENGTHS.get(settings["length"], LENGTHS["short"])
    parts = [ROLE.format(answer=answer, points=points, each=each)]
    if deep:
        parts.append(DEEP)
    parts.append(LANGUAGES.get(settings["answer_language"], LANGUAGES["english"]))
    if settings["call_language"] != "en":
        parts.append(URDU_CALL)
    if settings["context"].strip():
        parts.append("# Brief from the presenter\n\n" + settings["context"].strip())
    room = MAX_REFERENCE
    for name in settings["knowledge_files"]:
        try:
            text = Path(name).read_text(encoding="utf-8-sig", errors="replace")[:room]
        except OSError:
            continue
        room -= len(text)
        parts.append(f"# Reference material: {Path(name).name}\n\n{text}")
    return "\n\n".join(parts)


def find_claude():
    found = shutil.which("claude") or shutil.which("claude.exe")
    if found:
        return found
    # Where its installers put it, for when GhostCue was started before PATH included it.
    for place in (Path.home() / ".local" / "bin" / "claude.exe",
                  Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"):
        if place.is_file():
            return str(place)
    return None


class Engine:
    """One running Claude Code process.

    `ask(text, tag)` sends a message. `on_event(tag, kind, text)` is then called on the
    reader thread with kind "delta" (the next piece of the reply), "done" (the whole
    reply) or "error". Messages are answered in the order they were asked.
    """

    def __init__(self, system_prompt, model, on_event, folder=None):
        self.on_event = on_event
        self.waiting = collections.deque()  # tags of the messages not yet answered, oldest first
        self.lock = threading.Lock()
        self.closed = False
        claude = find_claude()
        if not claude:
            raise RuntimeError("Claude Code was not found. Install it from claude.com/claude-code and sign in, "
                               "then start again.")
        # The prompt goes in a file: with reference material it is far longer than a command line may be.
        config.APP_DIR.mkdir(parents=True, exist_ok=True)
        prompt_file = config.APP_DIR / ("prompt-deep.md" if folder else "prompt.md")
        prompt_file.write_text(system_prompt, encoding="utf-8")
        command = [claude, "-p", "--model", model, "--system-prompt-file", str(prompt_file),
                   "--strict-mcp-config", "--no-session-persistence",
                   "--input-format", "stream-json", "--output-format", "stream-json",
                   "--verbose", "--include-partial-messages"]
        if folder:  # may read the product's files, and nothing else
            command += ["--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob"]
        else:
            command += ["--tools", ""]
        self.process = subprocess.Popen(
            command, cwd=folder or config.APP_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.errors = collections.deque(maxlen=20)
        threading.Thread(target=self.read, daemon=True).start()
        threading.Thread(target=lambda: self.errors.extend(self.process.stderr), daemon=True).start()

    def ask(self, text, tag):
        message = {"type": "user", "message": {"role": "user", "content": text}}
        with self.lock:
            self.waiting.append(tag)
            try:
                self.process.stdin.write(json.dumps(message) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.waiting.pop()
                self.on_event(tag, "error", "Claude Code is not running.")

    def read(self):
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            tag = self.waiting[0] if self.waiting else None
            kind = message.get("type")
            if kind == "stream_event":
                event = message.get("event", {})
                if event.get("type") == "content_block_delta" and event.get("delta", {}).get("type") == "text_delta":
                    self.on_event(tag, "delta", event["delta"]["text"])
                elif event.get("type") == "content_block_start" and event.get("content_block", {}).get("type") == "tool_use":
                    self.on_event(tag, "working", event["content_block"].get("name", ""))
            elif kind == "result":
                if self.waiting:
                    self.waiting.popleft()
                if message.get("is_error"):
                    self.on_event(tag, "error", str(message.get("result") or message.get("subtype") or "failed"))
                else:
                    self.on_event(tag, "done", str(message.get("result") or "").strip())
        if not self.closed:
            self.process.wait()
            detail = "".join(self.errors).strip().splitlines()
            reason = detail[-1] if detail else f"exit code {self.process.returncode}"
            for tag in list(self.waiting) or [None]:
                self.on_event(tag, "error", f"Claude Code stopped: {reason}")
            self.waiting.clear()

    def close(self):
        self.closed = True
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
