# GhostCue script format

A GhostCue script is a plain text file (`.txt`). It is a list of **cues**. Each
cue has a start time, an optional title, and lines that say what to show, what
to do, and what to say.

```
# Lines starting with # are comments. GhostCue ignores them.

[0:00] Introduction
SHOW: The app open on the login page.
SAY:
  Hi, this is a short walkthrough of the app.
  I'll sign in, create a record, and then show the code.

[0:12] Sign in
DO: Type the email and password, then click "Sign in".
SHOW: The dashboard, with the three summary cards.
SAY:
  I'll start by signing in with a demo account.
  This is the dashboard.
NOTE: If the login is slow, keep talking.

[0:30] END
```

## Rules

1. **A cue starts with `[M:SS] Title`.** The time is in square brackets, minutes
   and seconds (`[0:05]`, `[12:30]`, or `[1:02:03]` with hours). The title is
   optional. Times must increase from one cue to the next.

2. **A cue lasts until the next cue starts.** To say when the last cue finishes,
   end the file with `[M:SS] END`. Without it, GhostCue estimates the last cue's
   length from its word count.

3. **Inside a cue, a line starts with one of four labels:**

   | Label   | Meaning                                              | On the overlay      |
   |---------|------------------------------------------------------|---------------------|
   | `SHOW:` | what should be on screen, or where to scroll to      | left side, blue     |
   | `DO:`   | a click or keystroke to make                         | left side, amber    |
   | `SAY:`  | the words to speak                                   | right side, large   |
   | `NOTE:` | a private reminder, never read out                   | left side, grey     |

   A cue can use any of them, any number of times, in any order. The left side
   keeps the SHOW, DO and NOTE lines in the order they are written.

4. **A line with no label continues the label above it**, as a new line on the
   overlay. So write `SAY:` once and put one sentence on each line below it. The
   end of a line is where you breathe.

5. **One line in the file is one line on the overlay.** Do not wrap long lines
   by hand; the overlay wraps them to fit its width. Indentation and blank lines
   are ignored, so use them freely to keep the file readable.

6. **`#` at the start of a line makes it a comment.** Use comments for anything
   that is not a cue: a title, part dividers, a pre-recording checklist.

Nothing else is allowed. Text before the first cue, a line inside a cue before
any label, a time that does not increase, or anything after `END` is an error,
and GhostCue names the line. Run `python ghostcue.py script.txt --check` to see
exactly how a script was read.

## Writing a script that works well on the overlay

- **One step per cue.** If a section has several steps, each with its own click
  and its own words, give each step its own cue and timestamp. The overlay shows
  a cue's directions on the left and its words on the right, so a cue that mixes
  three clicks with three paragraphs no longer shows which words go with which
  click.
- **Keep a cue to what fits on screen**, roughly up to eight spoken lines. Longer
  cues still work (they scroll over the cue's duration), but short ones are easier
  to follow.
- **One sentence per `SAY` line**, written the way you talk.
- **Put the exact thing to click or type in `DO`**, and what you should be looking
  at in `SHOW`.
- **Time the cues at speaking pace**: about 2.5 words per second, plus the time
  the clicks and page loads take.

## Recording part of a script

A script can cover more than one video. `--from` and `--to` pick out a stretch
of it and re-time it to start at 0:00:

```
python ghostcue.py script.txt --to 5:00      # only the cues before 5:00
python ghostcue.py script.txt --from 5:00    # only the cues from 5:00 on, shown as 0:00 onward
```
