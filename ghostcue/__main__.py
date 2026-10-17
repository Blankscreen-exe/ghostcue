"""python -m ghostcue            the tray app
python -m ghostcue listen     print what is heard on a call, to check the audio
python -m ghostcue prompter   the teleprompter (see SCRIPT_FORMAT.md)
python -m ghostcue capture    save what Ctrl+Alt+W would send about the window in front, to check it
"""

import sys


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in ("prompter", "listen", "capture"):
        del sys.argv[1]
        sys.argv[0] = f"ghostcue {command}"
    if command == "prompter":
        from .teleprompter import main as run
    elif command == "listen":
        from .listen import main as run
    elif command == "capture":
        from .capture import main as run
    else:
        from .app import main as run
    run()


if __name__ == "__main__":
    main()
