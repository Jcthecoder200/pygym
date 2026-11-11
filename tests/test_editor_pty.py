"""Smoke test: the editor in a real pseudo-terminal, typing like a student would."""
import os
import re
import select
import sys
import time

import pytest

if sys.platform.startswith("win"):
    pytest.skip("the terminal editor is Linux/macOS only", allow_module_level=True)

import fcntl  # noqa: E402
import pty  # noqa: E402
import signal  # noqa: E402
import struct  # noqa: E402
import termios  # noqa: E402

CHILD = ("from pygym.session import Session; from pygym.editor import run_editor; "
         "run_editor(Session('zip', 1))")
ROWS, COLS = 24, 80
_SGR = re.compile(rb"\x1b\[[0-9;]*m")  # colour / bold codes


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
        """Wait until `needle` is on screen after `start` (colour codes between letters ignored)."""
        while needle not in _SGR.sub(b"", self.out[start:]):
            if self.eof or time.monotonic() > deadline:
                raise AssertionError(f"{needle!r} not seen; output so far: {self.out[-400:]!r}")
            self.pump(0.1)
        return len(self.out)


def test_type_and_wq_in_a_real_terminal(tmp_path):
    env = dict(os.environ, PYGYM_HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1", TERM="xterm")
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    pid, fd = pty.fork()
    if pid == 0:  # the child: fix the window size, then run the editor
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            os.execve(sys.executable, [sys.executable, "-c", CHILD], env)
        finally:
            os._exit(127)
    deadline = time.monotonic() + 9
    reader = Reader(fd)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        mark = reader.until(b"NORMAL", 0, deadline)          # first frame drawn
        os.write(fd, b"i")
        mark = reader.until(b"-- INSERT --", mark, deadline)
        os.write(fd, b"return dict(zip(keys, values))")
        mark = reader.until(b"values))", mark, deadline)
        os.write(fd, b"\x1b")
        mark = reader.until(b"NORMAL", mark, deadline)       # ESC (after the 50 ms wait)
        time.sleep(0.05)
        os.write(fd, b":wq")
        mark = reader.until(b":wq", mark, deadline)
        os.write(fd, b"\r")
        while not reader.eof and time.monotonic() < deadline:
            reader.pump(0.1)
        status = None
        while time.monotonic() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.05)
        assert status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
            reader.out[-400:]
        pid = 0  # reaped
        out = reader.out
        assert b"\x1b[?1049h" in out and b"-- INSERT --" in out
        assert b"\x1b[?1049l" in out           # left the alternate screen
        assert b"Passed!" in out.split(b"\x1b[?1049l")[-1]  # result printed on the normal screen
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        os.close(fd)


def test_check_opens_the_results_window_in_a_real_terminal(tmp_path):
    env = dict(os.environ, PYGYM_HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1", TERM="xterm")
    env.pop("COLUMNS", None)
    env.pop("LINES", None)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            os.execve(sys.executable, [sys.executable, "-c", CHILD], env)
        finally:
            os._exit(127)
    deadline = time.monotonic() + 15
    reader = Reader(fd)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        mark = reader.until(b"NORMAL", 0, deadline)
        os.write(fd, b"i")
        mark = reader.until(b"-- INSERT --", mark, deadline)
        os.write(fd, b"print('pairs', keys)\rreturn dict(zip(keys, keys))")
        mark = reader.until(b"keys))", mark, deadline)
        os.write(fd, b"\x1b")
        mark = reader.until(b"NORMAL", mark, deadline)
        time.sleep(0.05)
        os.write(fd, b":w\r")
        mark = reader.until(b" results  1/3 passed ", mark, deadline)   # the window opened
        mark = reader.until(b"pairs ['x', 'y']", 0, deadline)            # with the print()s
        os.write(fd, b"\x17w")                                           # Ctrl-W w: focus it
        mark = reader.until(b"-- RESULTS --", mark, deadline)
        os.write(fd, b"q:q!\r")                                           # close it, quit
        while not reader.eof and time.monotonic() < deadline:
            reader.pump(0.1)
        status = None
        while time.monotonic() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.05)
        assert status is not None and os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, \
            reader.out[-400:]
        pid = 0
        assert b"Bye! (code not checked)" in reader.out.split(b"\x1b[?1049l")[-1]
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        os.close(fd)
