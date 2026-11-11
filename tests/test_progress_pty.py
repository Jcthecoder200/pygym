"""The real editor loop in a pseudo-terminal while a (fake, slow) AI writes the next exercise:
the progress line is redrawn about 10 times a second, keys are ignored, Ctrl-C cancels."""
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

# The child replaces ai.generate_exercise: the first exercise comes at once; later ones take
# SECONDS seconds and report a new try every 0.4 s.
CHILD = """
import dataclasses, sys, time
from pygym import ai, loader
from pygym.session import Session
from pygym.editor import run_editor
SECONDS = float(sys.argv[1])
base = loader.pick("zip", 1)
calls = []
def generate(target, difficulty, *, avoid_ids=None, settings=None, timeout=20.0, on_progress=None):
    calls.append(1)
    if on_progress is not None:
        start = time.monotonic()
        while time.monotonic() - start < SECONDS:
            n = int((time.monotonic() - start) / 0.4) + 1
            on_progress({"attempt": min(n, 4), "max_attempts": 4, "budget": 20.0,
                         "elapsed": time.monotonic() - start, "stage": "asking", "detail": ""})
            time.sleep(0.05)
    return dataclasses.replace(base, id="ai-zip-1-%08x" % len(calls), source="ai")
ai.generate_exercise = generate
run_editor(Session("zip", 1, mode="ai"))
"""
ROWS, COLS = 24, 80
_SGR = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")


class Reader:
    def __init__(self, fd):
        self.fd, self.out, self.eof = fd, b"", False

    def pump(self, timeout):
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            data = b""
        if not data:
            self.eof = True
        self.out += data

    def plain(self, start=0):
        return _SGR.sub(b"", self.out[start:])

    def until(self, needle, start, deadline):
        while needle not in self.plain(start):
            if self.eof or time.monotonic() > deadline:
                raise AssertionError(f"{needle!r} not seen; output so far: {self.out[-400:]!r}")
            self.pump(0.05)
        return len(self.out)


def spawn(tmp_path, seconds):
    env = dict(os.environ, PYGYM_HOME=str(tmp_path), PYTHONDONTWRITEBYTECODE="1", TERM="xterm")
    for name in ("COLUMNS", "LINES", "PYGYM_NO_AI"):
        env.pop(name, None)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            os.execve(sys.executable, [sys.executable, "-c", CHILD, str(seconds)], env)
        finally:
            os._exit(127)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    return pid, fd


def finish(pid, fd, reader, deadline):
    try:
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
    finally:
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
        os.close(fd)


def test_progress_line_is_redrawn_while_writing(tmp_path):
    pid, fd = spawn(tmp_path, 1.5)
    deadline = time.monotonic() + 20
    reader = Reader(fd)
    try:
        mark = reader.until(b"ai-zip-1-00000001", 0, deadline)      # first frame drawn
        os.write(fd, b":s\r")
        mark = reader.until(b"writing exercise \xc2\xb7 try 1 of 4", mark, deadline)
        started = time.monotonic()
        os.write(fd, b"ixyz")                                      # ignored while writing
        mark2 = reader.until(b"ai-zip-1-00000002", mark, deadline)   # the new exercise is shown
        took = time.monotonic() - started
        frames = reader.plain(mark)[:reader.plain(mark).find(b"ai-zip-1-00000002")]
        redraws = frames.count(b"writing exercise")
        spinners = set(re.findall("([⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]) writing".encode(), frames))
        assert b"try 3 of 4" in frames
        assert redraws >= 6 * took, (redraws, took)               # ~10 per second
        assert len(spinners) >= 5                                  # the spinner moves
        assert b"xyz" not in reader.plain(mark2 - 200)             # the keys did not reach the code
        os.write(fd, b":q!\r")
    except BaseException:
        os.kill(pid, signal.SIGKILL)
        raise
    finish(pid, fd, reader, deadline)


def test_ctrl_c_cancels_in_a_real_terminal(tmp_path):
    pid, fd = spawn(tmp_path, 30)
    deadline = time.monotonic() + 20
    reader = Reader(fd)
    try:
        mark = reader.until(b"ai-zip-1-00000001", 0, deadline)
        os.write(fd, b":s\r")
        mark = reader.until(b"writing exercise", mark, deadline)
        os.write(fd, b"\x03")                                      # Ctrl-C
        mark = reader.until(b"Cancelled. You are still on this exercise.", mark, deadline)
        assert b"ai-zip-1-00000002" not in reader.plain(mark - 2000)
        os.write(fd, b":q!\r")
    except BaseException:
        os.kill(pid, signal.SIGKILL)
        raise
    finish(pid, fd, reader, deadline)
