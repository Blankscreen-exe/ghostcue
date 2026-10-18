# Contributing to GhostCue

Bug reports, fixes and new features are all welcome. This page covers how to get
a change from your computer into a release with as little back and forth as
possible.

## Before you start

- **Bugs**: open an [issue](https://github.com/Blankscreen-exe/ghostcue/issues)
  first, unless the fix is a few lines. Search the existing ones before you do.
- **Features**: open an issue to talk about it before writing much code, so
  that a change that does not fit GhostCue is found out early, not after the
  work is done.
- **Small things** (a typo, a broken link, a wrong sentence in the README) need
  no issue: send the pull request.

## Reporting a bug

Say:

- the GhostCue version (in the name of the file you downloaded, under
  **Installed apps** in Windows settings, or `__version__` in
  `ghostcue/__init__.py`), and whether it is installed, the zip, or from source;
- the Windows version (`winver`), and your Claude Code version
  (`claude --version`) if the assistant is involved;
- what you did, what you expected, and what happened instead;
- any error. From source, run `.venv\Scripts\python -m ghostcue` in a terminal
  to see it. For a problem with the audio, add what `python -m ghostcue listen`
  prints.

Leave out anything private: call transcripts, your brief, knowledge files, and
screenshots of a real call. If the overlay ever shows up in a screen share,
recording or screenshot, say so in the title. That is the bug that matters most.

## Setting up

Follow [From source](README.md#from-source) in the README: 64-bit Windows 10
version 2004 or later, Python 3.10 or later, and a `.venv` with
`requirements.txt` installed. To build the exe as well, also install
`packaging\requirements.txt` and see [Making a release](README.md#making-a-release).

## Making a change

1. Fork the repository and branch off `main`, with a name that says what the
   branch does, such as `fix-mic-level-bar` or `teleprompter-loop-mode`.
2. Keep each pull request to one change. A fix and a refactor are two pull
   requests.
3. Write it to read like the code around it:
   - the same naming, layout and idioms as the rest of the file;
   - comments that say why, not what the next line does, and as few as the
     surrounding code has;
   - no new dependency unless it is needed. If one is, add it to
     `requirements.txt`, or to `packaging\requirements.txt` if only the build
     uses it, and say why in the pull request;
   - the teleprompter (`ghostcue/teleprompter.py`) uses only the Python
     standard library. Keep it that way.
4. Do not change `__version__` in `ghostcue/__init__.py`. Every push to `main`
   that brings a new version publishes a release, so the maintainer raises it
   when releasing.
5. Keep the README up to date with what you change: a new hotkey, setting or
   command goes in its table or section. A change to the script format goes in
   [SCRIPT_FORMAT.md](SCRIPT_FORMAT.md), and `example_script.txt` must still be
   read without errors. The site in `docs/` covers the same ground as the
   README, so change it too when a feature it describes changes.

## Checking it works

There is no automated test suite yet, so check your change by hand, on
Windows, before you send it:

- `python -m ghostcue prompter example_script.txt --check` still reads the
  sample script, and the teleprompter opens and advances.
- `python -m ghostcue` starts the tray app, and the settings window opens.
- For a change to the audio or transcription, `python -m ghostcue listen`
  prints what both sides say.
- For a change to window capture, `python -m ghostcue capture` saves what would
  be sent.
- For any change to an overlay window, share your screen in a test meeting and
  check on a second device that the overlay is not in it.
- For a change under `packaging/`, run `packaging\build.ps1` and start the
  built `dist\GhostCue\GhostCue.exe`.

Say in the pull request what you checked, and on which Windows version.

## Privacy

GhostCue is trusted with live calls, so a change must not quietly send more
than it does now. Nothing goes to Anthropic or anywhere else except what the
README says goes, when the README says it goes. If your change sends something
new, say so in the README's [How it works](README.md#how-it-works) and in the
pull request. Never commit settings, transcripts, captures or keys from your own
`%APPDATA%\GhostCue`.

## Commits

- One commit for each change that stands on its own. Do not fix up an earlier
  commit with an unrelated one.
- The subject says what changes for the person using GhostCue, in the
  imperative, in 72 characters or fewer, with no full stop: "Keep the overlay on
  the monitor it was last on", not "overlay.py changes".
- Leave a blank line after the subject, then explain the change and why, if the
  subject does not say it all. Wrap at 72 characters.

## Pull requests

- Open it against `main`, and fill in what it changes, why, and how you checked
  it. Link the issue it closes (`Closes #12`).
- Keep it up to date with `main` by rebasing, not by merging `main` into it.
- Expect questions and some changes to be asked for. Push them as new commits
  while the review is going on; they can be squashed before it is merged.

## Conduct

Be kind and assume good faith. Criticise the code, not the person who wrote it.

## License

GhostCue is under the [MIT License](LICENSE). By contributing, you agree that
your contribution is licensed under it too.
