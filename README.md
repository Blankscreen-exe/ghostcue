# GhostCue

A teleprompter overlay for recording demo videos. It shows what to show, what
to do and what to say, cue by cue, in an always-on-top window that you can see
on your monitor but that screen recorders, screenshots and screen shares cannot.

Needs Windows 10 version 2004 or later and Python 3.10+. No packages to install.

## Run

```
python ghostcue.py my_script.txt --check   # print how the script was read, no window
python ghostcue.py my_script.txt           # open the overlay
python ghostcue.py                         # pick the script in a file dialog
```

| Option | Effect |
|--------|--------|
| `--to M:SS` | use only the cues that start before this time |
| `--from M:SS` | use only the cues from this time on, re-timed so it becomes 0:00 |
| `--manual` | change cues only with the hotkeys, not by the clock |
| `--font-size PT` | text size (default 15) |
| `--opacity 0-1` | window opacity (default 0.92) |
| `--geometry WxH+X+Y` | window size and position in pixels |

## Script format

Scripts are plain text in one fixed format, specified in
[SCRIPT_FORMAT.md](SCRIPT_FORMAT.md). In short:

```
[0:12] Sign in
DO: Type the email and password, then click "Sign in".
SHOW: The dashboard, with the three summary cards.
SAY:
  I'll start by signing in with a demo account.
  This is the dashboard.
NOTE: If the login is slow, keep talking.
```

`example_script.txt` is a complete sample. A script that breaks the format is
rejected with the line number of each problem.

## The overlay

The left side shows the cue's directions: `SHOW` in blue, `DO` in amber, `NOTE`
in grey. The right side shows the words to say, one sentence per line. The
header has the clock, the cue number and title, and a countdown to the next cue;
the bottom line previews the next cue.

The header also has `‹ prev`, `start` / `pause` and `next ›` buttons that do the
same as the hotkeys. They are a fallback: the buttons are hidden from the
recording but your pointer is not, so prefer the hotkeys during a take.

Drag the window anywhere to move it; drag the bottom-right corner to resize.
It never takes keyboard focus from the app you are demoing. If a cue is too long
for the window it scrolls over the cue's duration; turning the mouse wheel over
either side scrolls it by hand instead, until the next cue.

## Hotkeys

They work while any app has focus. All use **Ctrl+Alt**. The three you need
during a take are under the left hand, so the other hand can stay on the mouse:

| Key | Action |
|-----|--------|
| Space | start / pause the clock |
| X / Z | next / previous cue |
| R | restart from the first cue |
| M | switch between auto and manual advance |
| Up / Down | bigger / smaller text |
| H | hide / show the overlay |
| Q | quit |

**Auto** (default): cues change at their timestamps. Next/Previous also move the
clock to that cue, so if you fall behind, one press puts you back in sync.

**Manual** (`--manual` or Ctrl+Alt+M): cues change only when you press
Next/Previous. The clock keeps running, and the countdown turns red and shows
"over by" when you are behind the script's timing.

## Before the real take

- Record five seconds with your actual recorder and play it back, to confirm the
  overlay is absent. The header shows `hidden from capture` while the exclusion
  is active and a red `VISIBLE TO RECORDERS` if it could not be applied.
- The overlay is hidden, but your mouse pointer is not: moving the pointer over
  the overlay shows up in the video as the pointer moving over whatever is behind it.
- Minimise the terminal you launched it from; that window is recorded like any other.
- The hotkeys are global, so other apps do not receive those key combinations
  while GhostCue is running.
