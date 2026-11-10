"""Smoke test: the `pygym` main menu in a real pseudo-terminal (preset mode, no LLM).

builtins -> map -> beginner opens the editor; :q! goes back to the menu; q leaves the program.
"""
import json
import os
import re
import select
import sys
import time

import pytest

if sys.platform.startswith("win"):
    pytest.skip("the pseudo-terminal test is Linux/macOS only", allow_module_level=True)

import fcntl  # noqa: E402
import pty  # noqa: E402
import signal  # noqa: E402
import struct  # noqa: E402
import termios  # noqa: E402

ROWS, COLS = 30, 100
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")  # colours, cursor moves, screen switches


class Reader:
    def __init__(self, fd):
        self.fd, self.out, self.eof = fd, b"", False

    def pump(self, timeout):
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return
        try:
            data = os.read(self.fd, 65536)
        except OSError:  # EIO: the child closed the terminal
            data = b""
        if not data:
            self.eof = True
        self.out += data

    def until(self, needle, start, deadline):
        """Wait until `needle` is printed after `start` (terminal codes between letters ignored)."""
        while needle not in _ANSI.sub(b"", self.out[start:]):
            if self.eof or time.monotonic() > deadline:
                raise AssertionError(f"{needle!r} not seen; output so far: {self.out[-600:]!r}")
            self.pump(0.1)
        return len(self.out)


def test_menu_to_editor_and_back_in_a_real_terminal(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # Saved settings: preset mode, the editor window. PYGYM_NO_AI makes sure no model is asked.
    (home / "config.json").write_text(json.dumps({"mode": "preset", "level": 2, "ui": "editor"}))
    env = dict(os.environ, PYGYM_HOME=str(home), PYGYM_NO_AI="1", PYTHONDONTWRITEBYTECODE="1",
               TERM="xterm")
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    pid, fd = pty.fork()
    if pid == 0:  # the child: fix the window size, then run `python -m pygym`
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            os.execve(sys.executable, [sys.executable, "-m", "pygym"], env)
        finally:
            os._exit(127)
    deadline = time.monotonic() + 20
    reader = Reader(fd)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        mark = reader.until(b"library [builtins]", 0, deadline)    # the home screen
        os.write(fd, b"builtins\r")
        mark = reader.until(b"function", mark, deadline)
        os.write(fd, b"map\r")
        mark = reader.until(b"level [intermediate]", mark, deadline)  # the saved level is offered
        os.write(fd, b"b\r")                                        # b = beginner here
        reader.until(b"NORMAL", mark, deadline)                     # the editor's first frame
        mark = reader.until(b"map-01", mark, deadline)              # level 1 preset, id in the bar
        time.sleep(0.1)
        os.write(fd, b":q!")
        mark = reader.until(b":q!", mark, deadline)
        os.write(fd, b"\r")
        mark = reader.until(b"library [builtins]", mark, deadline)  # back on the home screen
        os.write(fd, b"q\r")
        reader.until(b"Bye!", mark, deadline)
        while not reader.eof and time.monotonic() < deadline:
            reader.pump(0.1)
        status = None
        while time.monotonic() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.05)
        assert status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
            reader.out[-600:]
        pid = 0  # reaped
        out = reader.out
        assert b"\x1b[?1049h" in out and b"\x1b[?1049l" in out  # entered and left the editor
        assert b"Traceback" not in out
        text = _ANSI.sub(b"", out)
        assert b"Starting map \xc2\xb7 beginner \xc2\xb7 preset" in text
        assert b"AI-generated" not in text  # preset mode, preset exercise: no warning, no badge
        saved = json.loads((home / "config.json").read_text())
        assert saved["level"] == 1 and saved["library"] == "builtins" and saved["mode"] == "preset"
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        os.close(fd)
