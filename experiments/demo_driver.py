"""Scripted demo of pygym's front-ends, for screen recordings (Linux/macOS).

    python experiments/demo_driver.py            # plays the demo in the current terminal

It runs the REAL editor / line CLI / IPython in a pseudo-terminal and types into them with
human-like delays, so what you see is the actual program. Nothing is graded for real: progress
goes to a temporary PYGYM_HOME. Make the terminal big (full screen) before starting.
Set DEMO_GO_FILE=<path> to wait until that file exists before starting (used by recorders).
"""
from __future__ import annotations

import fcntl
import os
import pty
import select
import struct
import sys
import tempfile
import termios
import time

from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text

PY = sys.executable
HOME = tempfile.mkdtemp(prefix="pygym-demo-")
console = Console()
TYPE_DELAY = 0.055


def card(title: str, lines, seconds: float, colour: str = "cyan") -> None:
    body = Group(Text(title, style=f"bold {colour}", justify="center"), Text(""),
                 *[Text(ln, justify="center") for ln in lines])
    console.clear()
    height = console.size.height
    pad = max(0, (height - len(lines) - 6) // 2)
    console.print("\n" * pad, end="")
    console.print(Align.center(Panel(body, border_style=colour, padding=(1, 6), expand=False)))
    sys.stdout.flush()
    time.sleep(seconds)


class Child:
    """A program in a pseudo-terminal whose screen output is mirrored to ours."""

    def __init__(self, argv, extra_env=None):
        cols, rows = os.get_terminal_size()
        env = dict(os.environ, PYGYM_HOME=HOME, PYTHONDONTWRITEBYTECODE="1", **(extra_env or {}))
        pid, fd = pty.fork()
        if pid == 0:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            os.execvpe(argv[0], argv, env)
        self.pid, self.fd, self.alive = pid, fd, True

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while True:
            left = end - time.time()
            if left <= 0 or not self.alive:
                return
            r, _, _ = select.select([self.fd], [], [], min(left, 0.02))
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    self.alive = False
                    return
                os.write(sys.stdout.fileno(), data)

    def key(self, raw: str, pause: float = 0.35) -> None:
        os.write(self.fd, raw.encode())
        self.pump(pause)

    def type(self, text: str, pause: float = 0.3) -> None:
        for ch in text:
            os.write(self.fd, ch.encode())
            self.pump(TYPE_DELAY)
        self.pump(pause)

    def finish(self, timeout: float = 4.0) -> None:
        self.pump(timeout)
        try:
            os.waitpid(self.pid, 0 if not self.alive else os.WNOHANG)
        except ChildProcessError:
            pass
        if self.alive:
            os.kill(self.pid, 9)
            os.waitpid(self.pid, 0)


ESC, ENTER, BS = "\x1b", "\r", "\x7f"
RUN_CELL = "\x1b\r"  # Esc+Enter runs a multi-line cell in IPython


def editor_demo() -> None:
    c = Child([PY, "-c", "from pygym.session import Session; from pygym.editor import run_editor; "
                         "run_editor(Session('zip', 1))"])
    c.pump(5.0)                                   # the start screen: modes + every command
    c.key("i", 0.8)                               # INSERT
    c.type("result = {}"); c.key(ENTER, 0.2)
    c.type("for i in range(len(keys)):"); c.key(ENTER, 0.2)
    c.type("result[keys[i]] = values[i]"); c.key(ENTER, 0.2)
    for _ in range(4):
        c.key(BS, 0.08)
    c.type("return result", 0.8)
    c.key(ESC, 0.8)                               # back to NORMAL
    c.type(":w", 0.5); c.key(ENTER, 3.0)          # ✗ must actually use zip
    c.type(":q", 0.5); c.key(ENTER, 3.0)          # refused: last check failed -> reveal?
    c.key("n", 1.0)
    c.type(":h", 0.5); c.key(ENTER, 3.0)          # hint
    c.type(":help", 0.5); c.key(ENTER, 4.0)       # zip's own documentation
    c.key("q", 0.8)
    c.type(":intro", 0.5); c.key(ENTER, 4.0)      # the command list again
    c.key("q", 0.8)
    for _ in range(3):
        c.key("k", 0.25)                          # up to "result = {}"
    for _ in range(4):
        c.type("dd", 0.35)                        # delete the loop version
    c.key(ESC + "[F", 0.4)                        # End
    c.key("i", 0.4); c.key(ENTER, 0.3)
    c.type("return dict(zip(keys, values))", 1.0)
    c.key(ESC, 0.6)
    c.type(":wq", 0.6); c.key(ENTER, 0.2)         # ✓ passes and closes
    c.finish(3.0)


def cli_demo() -> None:
    c = Child([PY, "-c", "from pygym import exercise; exercise('zip', 1, ui='text')"])
    c.pump(3.5)
    c.type("def combine(keys, values):"); c.key(ENTER, 0.3)
    c.type(":h", 0.3); c.key(ENTER, 2.5)
    c.type("    return dict(zip(keys, values))"); c.key(ENTER, 0.3)
    c.type(":wq", 0.3); c.key(ENTER, 0.2)
    c.finish(3.0)


def jupyter_demo() -> None:
    # A fresh IPython profile with no history or autosuggestions, so nothing personal shows up.
    c = Child([PY, "-m", "IPython", "--no-banner", "--no-autoindent", "--colors=Linux",
               "--TerminalInteractiveShell.autosuggestions_provider=None",
               "--HistoryManager.enabled=False",
               "--TerminalInteractiveShell.confirm_exit=False"],
              {"IPYTHONDIR": tempfile.mkdtemp(prefix="pygym-demo-ipy-"), "PROMPT_TOOLKIT_NO_CPR": "1"})
    c.pump(2.0)
    c.type("from pygym import jupyter, Session"); c.key(ENTER, 0.6)
    c.type("jupyter.start(Session('zip', 1))"); c.key(ENTER, 3.5)
    c.type("%%pygym :w"); c.key(ENTER, 0.2)
    c.type("def combine(keys, values):"); c.key(ENTER, 0.2)
    c.type("    return {}"); c.key(ENTER, 0.2); c.key(RUN_CELL, 3.0)
    c.type("%pygym :h"); c.key(ENTER, 3.0)
    c.type("%%pygym :wq"); c.key(ENTER, 0.2)
    c.type("def combine(keys, values):"); c.key(ENTER, 0.2)
    c.type("    return dict(zip(keys, values))"); c.key(ENTER, 0.2); c.key(RUN_CELL, 3.5)
    c.type("exit"); c.key(ENTER, 0.5)
    c.finish(2.0)


def main() -> None:
    go = os.environ.get("DEMO_GO_FILE")
    while go and not os.path.exists(go):
        time.sleep(0.1)
    card("pygym: what's new", [
        "Learn a Python built-in without leaving your code:  exercise(\"zip\")",
        "",
        "• vim-style commands  :w  :wq  :q  :q!  :h  :help  :s  :solution  :intro",
        "• all rules live in Session.handle()  ->  every front-end behaves the same",
        "• a Neovim-style terminal editor (NORMAL / INSERT modes)",
        "• Jupyter: write code in a  %%pygym :w  cell",
        "• colours and panels with Rich",
    ], 7.0)
    card("1 / 3   The terminal editor  (Linux / macOS)", [
        "exercise(\"zip\") in a terminal opens it.",
        "Starts in NORMAL · i to type · Esc back · : for commands",
    ], 4.0, "green")
    editor_demo()
    card("2 / 3   The line CLI  (Windows / no real terminal)", [
        "Type the function line by line; a line that is exactly a command runs it.",
    ], 3.5, "yellow")
    cli_demo()
    card("3 / 3   Jupyter: the %%pygym cell magic", [
        "In a notebook, exercise(\"zip\") prints the task; each cell starting with",
        "%%pygym :w  is checked. Shown here in IPython, the engine Jupyter runs on.",
    ], 5.0, "magenta")
    jupyter_demo()
    card("Try it", [
        "pip install -e .   then   python -c \"from pygym import exercise; exercise('zip')\"",
        "",
        "Docs for front-end work: docs/FRONTEND_GUIDE.md",
        "Tests: python -m pytest -q",
    ], 6.0)
    console.clear()


if __name__ == "__main__":
    main()
