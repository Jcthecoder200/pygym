"""See what your keyboard actually sends to a terminal program (Linux/macOS, zero dependencies).

    python experiments/keys_demo.py

Press keys (arrows, Enter, Backspace, letters). Each key's raw bytes are printed.
Press q to quit.

How it works: the terminal is switched from "cooked" (canonical) mode, where it collects a
whole line and only hands it over on Enter, to "raw" mode, where every key press is delivered
immediately. That is the first step every full-screen terminal app (vim, nano, less, htop,
IPython) takes. They then keep their own buffer of lines + a cursor, and redraw the screen
with escape sequences such as ESC[2J (clear) and ESC[<row>;<col>H (move cursor).
"""
import os
import sys
import termios
import tty

NAMES = {
    b"\x1b[A": "Up", b"\x1b[B": "Down", b"\x1b[C": "Right", b"\x1b[D": "Left",
    b"\r": "Enter", b"\x7f": "Backspace", b"\t": "Tab", b"\x1b": "Esc",
    b"\x1b[H": "Home", b"\x1b[F": "End", b"\x1b[3~": "Delete",
}


def main() -> None:
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)  # remember cooked-mode settings to restore them later
    print("Press keys to see their bytes; q quits.\r")
    try:
        tty.setraw(fd)  # raw mode: no line buffering, no echo
        while True:
            data = os.read(fd, 16)  # one key press (an escape sequence arrives as one chunk)
            if data in (b"q", b"\x03"):
                break
            name = NAMES.get(data, data.decode(errors="replace") if data.isascii() else "?")
            # In raw mode "\n" no longer returns to column 0, so we write "\r\n" ourselves.
            sys.stdout.write(f"{data!r:<14} -> {name}\r\n")
            sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)  # always give the terminal back
    print("Terminal restored.")


if __name__ == "__main__":
    main()
