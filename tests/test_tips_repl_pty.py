"""Tips in the plain Python REPL, in a real pseudo-terminal: the new REPL (3.13+) and the classic
one (PYTHON_BASIC_REPL=1; its "<stdin>" has no source, the readline history is used).

    >>> import pygym
    >>> list(zip([1], 5))
    Traceback ...
    pygym: this line uses zip(); 30-second exercise? pygym.exercise('zip')
"""
import os
import re
import sys
import time

import pytest

if sys.platform.startswith("win"):
    pytest.skip("the pseudo-terminal test is Linux/macOS only", allow_module_level=True)

import fcntl  # noqa: E402
import pty  # noqa: E402
import struct  # noqa: E402
import termios  # noqa: E402

from test_menu_pty import Reader  # noqa: E402

ROWS, COLS = 30, 160
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[=>]")


def _has_readline():
    try:
        import readline  # noqa: F401
        return True
    except ImportError:
        return False


def _repl(tmp_path, basic):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYGYM_NO_TIPS", "PYTHONSTARTUP", "PYTHON_BASIC_REPL", "NO_COLOR")}
    env.update(PYGYM_HOME=str(tmp_path / "home"), PYGYM_NO_AI="1", TERM="xterm",
               PYTHONDONTWRITEBYTECODE="1", NO_COLOR="1",
               PYTHON_HISTORY=str(tmp_path / "history"))
    if basic:
        env["PYTHON_BASIC_REPL"] = "1"
    pid, fd = pty.fork()
    if pid == 0:
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
            os.chdir(str(tmp_path))
            os.execve(sys.executable, [sys.executable, "-q"], env)
        finally:
            os._exit(127)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
    return pid, fd


def _clean(data: bytes) -> str:
    return _ANSI.sub(b"", data).decode("utf-8", "replace").replace("\r", "")


def _session(tmp_path, basic):
    pid, fd = _repl(tmp_path, basic)
    reader = Reader(fd)
    deadline = time.monotonic() + 40

    def send(line, wait_for=b">>> "):
        mark = len(reader.out)
        os.write(fd, line.encode() + b"\r")
        time.sleep(0.05)
        return reader.until(wait_for, mark, deadline)

    try:
        reader.until(b">>> ", 0, deadline)
        send("import pygym")
        start = len(reader.out)
        send("list(zip([1], 5))", b"pygym.exercise('zip')")
        first = _clean(reader.out[start:])
        send("from pygym import exercise")
        start = len(reader.out)
        send("best = max([])", b"? exercise('max')")
        second = _clean(reader.out[start:])
        os.write(fd, b"exit()\r")
        end = time.monotonic() + 10
        while not reader.eof and time.monotonic() < end:
            reader.pump(0.1)
    finally:
        try:
            os.kill(pid, 9)
        except OSError:
            pass
        os.waitpid(pid, 0)
        os.close(fd)
    return first, second


def _check(first, second):
    assert first.index("Traceback (most recent call last):") < first.index("TypeError") \
        < first.index("pygym: this line uses zip(); 30-second exercise? pygym.exercise('zip')")
    assert "pygym: this line uses max(); 30-second exercise? exercise('max')" in second
    assert second.index("ValueError") < second.index("pygym: this line uses max()")


@pytest.mark.skipif(sys.version_info < (3, 13), reason="the new REPL is Python 3.13+")
def test_new_repl(tmp_path):
    _check(*_session(tmp_path, basic=False))


@pytest.mark.skipif(not _has_readline(), reason="the classic REPL keeps no source without readline")
def test_classic_repl(tmp_path):
    _check(*_session(tmp_path, basic=True))
