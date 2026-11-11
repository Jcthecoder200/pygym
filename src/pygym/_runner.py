"""Runs in a SEPARATE process. Reads a JSON job on stdin, writes a JSON result to stdout.

Job:    {code, function_name, target, inputs, allowed_imports?: [str], files?: {path: text},
         http_fixtures?: {path: response}, allow_open?: bool}
        target is the full name: "zip", "str.join", "statistics.median", "numpy.cumsum".
        Or {probe_import: "numpy"}: only check that the library imports here.
Result: {status: "ok", outputs: [...], target_calls: int, blocked: [str], setup_stdout?: str}
      | {status: "error", message: str, blocked: [str], setup_stdout?: str}
        outputs: one {"value": ...} or {"error": "TypeError: ..."} per test, plus "stdout" when
        that test printed something. Every test runs, also after one raised.
        setup_stdout: what the code printed while it was loaded (top-level code).
        Printed text is cut after STDOUT_LIMIT characters per test (and for the setup).
        target_calls is -1 when the call can't be counted (methods of built-in types, numpy ufuncs).
        blocked lists what the audit hook stopped (writes outside the folder, network, ...).

Progress (stderr): lines that start with PROGRESS_MARK, each followed by one JSON object:
        {"stage": "setup"} / {"stage": "test", "i": 0} when a stage starts, {"print": text} for
        printed text (the same capped text) and {"done": output} when a test finished. Only read
        when the run times out (the JSON result never comes then): read_progress() turns them
        into "which test was running and what had it printed".

pygym.sandbox starts this file in a NEW temporary folder <root>: the current directory is
<root>/work (the exercise's `files` are written there before every test), temporary files go to
<root>/tmp and caches to <root>/cache. Nothing outside these three folders may be changed.

Layers of protection (docs/AI_MODE.md, "The sandbox"):
  1. the operating system, set up by the parent: bubblewrap on Linux (read-only system, home
     folder hidden, no network), sandbox-exec on macOS. Not available on Windows;
  2. the Python audit hook below (every platform): blocks writing outside the folder, starting
     programs, ctypes and network access except the local test server (BASE_URL).
     AUDIT HOOKS ARE NOT A SECURITY BOUNDARY: C extensions and ctypes tricks can skip them.
     They stop accidents (a generated os.remove or a request to a real server), not attacks;
  3. restricted built-ins and imports for the exercise code (stops casual cheating), plus
     memory, file-size and time limits.

This file must not import pygym, so the lists the rest of pygym shares (module policy,
METHOD_OWNER_NAMES, ...) are defined here.
"""
import builtins
import importlib
import io
import json
import os
import sys
import types

# Modules any exercise may import.
ALLOWED_IMPORTS = {"math", "itertools", "functools", "collections", "operator", "string"}
# Pure-computation standard modules: no files, processes, network or interpreter state, nothing
# random or time-based. These were the only AI targets before the OS sandbox existed.
SAFE_MODULES = frozenset({
    "math", "cmath", "statistics", "itertools", "functools", "collections", "operator", "string",
    "re", "textwrap", "heapq", "bisect", "fractions", "decimal", "datetime", "calendar", "json",
    "difflib", "unicodedata", "copy", "array", "keyword",
})
# Never usable (not as a target, not as an import), with the reason the student sees.
# A dotted entry ("pygame.display") blocks that part of a library only.
_SCREEN = "it needs a screen (windows, sound or the keyboard)"
_PROGRAMS = "it starts or controls other programs"
_PYTHON = "it controls the running Python itself, so its results can't be graded"
_PACKAGES = "it installs or changes Python packages"
_CHANGES = "its results change on every run, so pygym can't know the right answer"
DENIED_MODULES = {
    "tkinter": _SCREEN, "turtle": _SCREEN, "turtledemo": _SCREEN, "idlelib": _SCREEN,
    "PyQt5": _SCREEN, "PyQt6": _SCREEN, "PySide2": _SCREEN, "PySide6": _SCREEN, "wx": _SCREEN,
    "kivy": _SCREEN, "pygame.display": _SCREEN, "pygame.mixer": _SCREEN, "pyautogui": _SCREEN,
    "pynput": _SCREEN, "curses": _SCREEN, "readline": _SCREEN, "rlcompleter": _SCREEN,
    "winsound": _SCREEN, "msvcrt": _SCREEN, "tty": _SCREEN, "termios": _SCREEN, "pty": _PROGRAMS,
    "antigravity": "importing it opens a web browser", "this": "importing it prints a poem",
    "__hello__": "importing it prints a message", "__phello__": "importing it prints a message",
    "__main__": "it is the running program, not a library",
    "webbrowser": "it opens a web browser", "pygym": "it is pygym itself",
    "pip": _PACKAGES, "setuptools": _PACKAGES, "ensurepip": _PACKAGES, "venv": _PACKAGES,
    "distutils": _PACKAGES, "pkg_resources": _PACKAGES, "site": _PACKAGES,
    "subprocess": _PROGRAMS, "multiprocessing": _PROGRAMS, "signal": _PROGRAMS,
    "_winapi": _PROGRAMS, "winreg": _PROGRAMS, "posix": _PROGRAMS, "nt": _PROGRAMS,
    "ctypes": "it can change memory directly", "resource": _PYTHON, "faulthandler": _PYTHON,
    "sys": _PYTHON, "builtins": _PYTHON, "importlib": _PYTHON, "imp": _PYTHON, "code": _PYTHON,
    "codeop": _PYTHON, "pdb": _PYTHON, "bdb": _PYTHON, "atexit": _PYTHON,
    "random": _CHANGES, "secrets": _CHANGES, "time": _CHANGES,
}
# os functions that start, stop or change programs (the rest of os is fine in the sandbox).
_OS_PROCESS_PREFIXES = ("system", "popen", "exec", "spawn", "fork", "kill", "posix_spawn",
                        "abort", "_exit", "startfile", "wait", "nice", "set", "plock", "sched_",
                        "pidfd", "register_at_fork", "chroot")
# Modules about files / the network. They are allowed as targets only with an OS sandbox
# (pygym.sandbox), and the exercise code may import one only when it is the target's library.
FILE_MODULES = frozenset({"os", "pathlib", "shutil", "glob", "tempfile", "filecmp", "fileinput",
                          "zipfile", "tarfile", "sqlite3", "dbm", "shelve", "mmap"})
NETWORK_MODULES = frozenset({"socket", "ssl", "select", "selectors", "socketserver", "urllib",
                             "http", "ftplib", "smtplib", "poplib", "imaplib", "nntplib",
                             "telnetlib", "xmlrpc", "requests", "httpx", "urllib3", "aiohttp"})
SYSTEM_MODULES = FILE_MODULES | NETWORK_MODULES
# "str.join" style targets: methods of these built-in types.
METHOD_OWNER_NAMES = ("str", "list", "dict", "set", "tuple", "int", "float", "bytes")
REMOVED_BUILTINS = ["open", "exec", "eval", "compile", "input", "breakpoint",
                    "exit", "quit", "help", "memoryview"]
MEMORY_LIMIT = 512 * 1024 * 1024
FILE_SIZE_LIMIT = 16 * 1024 * 1024
MAX_ITEMS = 100_000                    # longest iterator turned into a list
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"})
STDOUT_LIMIT = 4000                    # printed characters kept per test (and for the setup)
STDOUT_CUT = "… (output cut)"
PROGRESS_MARK = "\x1epygym "           # starts every progress line on stderr


def denied_reason(name: str) -> str:
    """Why a module ("tkinter", "pygame.display") or a target ("os.system") can never be used.
    "" when it can."""
    parts = (name or "").split(".")
    for i in range(len(parts), 0, -1):
        reason = DENIED_MODULES.get(".".join(parts[:i]))
        if reason:
            return reason
    if len(parts) >= 2 and parts[0] == "os" and parts[1].startswith(_OS_PROCESS_PREFIXES):
        return "it starts, stops or changes programs"
    return ""


def import_permitted(module: str, target: str) -> bool:
    """May an exercise about `target` list `module` in its allowed_imports?"""
    top = (module or "").split(".")[0]
    if not top.isidentifier() or denied_reason(module):
        return False
    if top in SYSTEM_MODULES:
        return top == (target or "").split(".")[0]
    return True


def _limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_LIMIT, FILE_SIZE_LIMIT))
    except Exception:
        pass  # Windows / unsupported: the parent's timeout still protects us


# ----- results -> JSON ----------------------------------------------------------------------


def _tagged_repr(x) -> str:
    """repr(x), plus the type name when the repr doesn't already say what x is."""
    text = repr(x)
    name = type(x).__name__
    if name in text or type(x).__module__ == "builtins":
        return text
    return f"<{name}> {text}"


def norm(x, _depth: int = 0):
    """Turn any result into a JSON-safe, order-stable structure. Used for the reference AND the
    student's results, so both are compared in the same form.

    tuples -> lists, sets -> sorted lists, dict keys -> str, iterators -> lists,
    numpy / pandas (duck typing, never imported here): .to_dict() / .tolist(), dataclasses ->
    dicts. Anything else (Decimal, Fraction, complex, datetime, ...) -> its repr string, with the
    type name added when the repr doesn't show it. NaN stays NaN (the grader treats NaN == NaN).
    """
    if _depth > 100:
        return _tagged_repr(x)
    d = _depth + 1
    if x is None or type(x) in (bool, int, float, str):
        return x
    if isinstance(x, bool):
        return bool(x)
    if isinstance(x, int):
        return int(x)
    if isinstance(x, float):
        return float(x)
    if isinstance(x, str):
        return str(x)
    if isinstance(x, dict):
        return {str(k): norm(v, d) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(i, d) for i in x]
    if isinstance(x, (set, frozenset)):
        return sorted((norm(i, d) for i in x), key=repr)
    if isinstance(x, type):
        return _tagged_repr(x)
    if hasattr(type(x), "__dataclass_fields__"):
        import dataclasses               # only when needed: it imports inspect (slow)
        return norm(dataclasses.asdict(x), d)
    for method in ("to_dict", "tolist", "item"):      # pandas, numpy and friends
        fn = getattr(x, method, None)
        if callable(fn):
            try:
                value = fn()
            except Exception:  # noqa: BLE001
                continue
            if value is not x:
                return norm(value, d)
    try:
        it = iter(x)                    # zip / map / filter / generators / bytes ...
    except TypeError:
        return _tagged_repr(x)
    out = []
    for item in it:
        if len(out) >= MAX_ITEMS:
            raise ValueError(f"the result has more than {MAX_ITEMS} items")
        out.append(norm(item, d))
    return out


def _replace_text(x, old: str, new: str):
    if isinstance(x, str):
        return x.replace(old, new)
    if isinstance(x, list):
        return [_replace_text(i, old, new) for i in x]
    if isinstance(x, dict):
        return {k: _replace_text(v, old, new) for k, v in x.items()}
    return x


# ----- "was the target really called?" ------------------------------------------------------


def _counting(fn, calls):
    def recorder(*a, **k):
        calls["n"] += 1
        return fn(*a, **k)
    return recorder


def _wrappable(obj) -> bool:
    """Functions, classes and function-like objects (numpy's array functions have __wrapped__).
    Callable objects with an API of their own (numpy ufuncs: np.add.reduce) are left alone:
    for those only the parent's static check applies."""
    return (isinstance(obj, (type, types.FunctionType, types.BuiltinFunctionType,
                             types.MethodType))
            or hasattr(obj, "__wrapped__"))


def _patch_module_target(target, allowed, calls):
    """Replace module.name by a counting wrapper. Returns True if calls can be counted.

    Works for `import statistics; statistics.median(...)`, `import numpy as np; np.cumsum(...)`
    and `from statistics import median` because all of them read the attribute from the (shared)
    module object after this patch. For a class (itertools.chain, collections.Counter) the
    wrapper is a function, so its public attributes are copied over (wrapped too:
    chain.from_iterable(...) counts as using chain).
    Known limit: isinstance(x, collections.Counter) fails while the class is wrapped.
    """
    module_name, _, name = target.rpartition(".")
    if module_name.split(".")[0] not in allowed:
        return False
    try:
        module = importlib.import_module(module_name)
        original = getattr(module, name)
    except Exception:  # noqa: BLE001
        return False
    if not callable(original) or not _wrappable(original):
        return False
    recorder = _counting(original, calls)
    if isinstance(original, type):
        for attr in dir(original):
            if attr.startswith("_"):
                continue
            try:
                value = getattr(original, attr)
                setattr(recorder, attr, _counting(value, calls) if callable(value) else value)
            except Exception:  # noqa: BLE001
                pass
    recorder.__name__ = getattr(original, "__name__", name)
    recorder.__doc__ = getattr(original, "__doc__", None)
    try:
        setattr(module, name, recorder)
    except Exception:  # noqa: BLE001
        return False
    return True


# ----- the folder, the test server and the audit hook ----------------------------------------


def write_files(folder: str, files: dict) -> None:
    """Empty `folder`, then write the exercise's fixture files into it (relative paths only)."""
    for entry in os.listdir(folder):
        path = os.path.join(folder, entry)
        if os.path.isdir(path) and not os.path.islink(path):
            import shutil
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass
    base = os.path.realpath(folder)
    for rel, text in (files or {}).items():
        path = os.path.realpath(os.path.join(base, rel))
        if not path.startswith(base + os.sep):
            raise ValueError(f"fixture file {rel!r} is outside the exercise folder")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)


def start_fixture_server(fixtures: dict):
    """A tiny HTTP server on 127.0.0.1 (random port) that answers with the exercise's fixtures.

    fixtures: {"/users/1": {"status": 200, "json": {...}}, "/hello": {"text": "hi"},
               "POST /orders": {"status": 201, "json": {...}}}  (headers: {...} optional).
    Unknown paths get 404 with {"error": "not found"}. Returns the server (server_address[1]
    is the port)."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length:
                self.rfile.read(min(length, 1 << 20))
            from urllib.parse import unquote_plus
            spec = None
            for path in (self.path, unquote_plus(self.path), self.path.split("?")[0]):
                for key in (f"{self.command} {path}", path):
                    if key in fixtures:
                        spec = fixtures[key]
                        break
                if spec is not None:
                    break
            headers = {}
            if not isinstance(spec, dict):
                status, body, ctype = 404, json.dumps({"error": "not found"}), "application/json"
            else:
                status = int(spec.get("status", 200))
                if "json" in spec:
                    body, ctype = json.dumps(spec["json"]), "application/json"
                else:
                    body, ctype = str(spec.get("text", "")), "text/plain; charset=utf-8"
                headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else {}
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", str(headers.pop("Content-Type", ctype)))
            self.send_header("Content-Length", str(len(data)))
            for k, v in headers.items():
                self.send_header(str(k), str(v))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _answer

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _fd_path(fd):
    """The path behind an open directory descriptor, or None (for os.remove(name, dir_fd=fd))."""
    try:
        if sys.platform.startswith("linux"):
            return os.readlink(f"/proc/self/fd/{int(fd)}")
        if sys.platform == "darwin":
            import fcntl
            raw = fcntl.fcntl(int(fd), fcntl.F_GETPATH, bytes(1024))
            return raw.split(b"\0", 1)[0].decode()
    except Exception:  # noqa: BLE001
        pass
    return None


_WRITE_FLAGS = (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC)
# event -> ((index of a path argument, index of its dir_fd argument or None), ...)
_PATH_EVENTS = {
    "os.remove": ((0, 1),), "os.rmdir": ((0, 1),), "os.mkdir": ((0, 2),),
    "os.rename": ((0, 2), (1, 3)), "os.link": ((0, 2), (1, 3)), "os.symlink": ((1, 2),),
    "os.truncate": ((0, None),), "os.chmod": ((0, 2),), "os.chown": ((0, 3),),
    "os.chflags": ((0, None),), "os.lchflags": ((0, None),), "os.utime": ((0, 3),),
    "os.setxattr": ((0, None),), "os.removexattr": ((0, None),),
    "shutil.rmtree": ((0, 1),), "shutil.copyfile": ((1, None),), "shutil.copymode": ((1, None),),
    "shutil.copystat": ((1, None),), "shutil.copytree": ((1, None),),
    "shutil.move": ((0, None), (1, None)), "shutil.chown": ((0, None),),
    "shutil.make_archive": ((0, None),), "shutil.unpack_archive": ((1, None),),
}
_PROGRAM_EVENTS = {"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn",
                   "os.fork", "os.forkpty", "os.startfile", "os.kill", "os.killpg",
                   "os.add_dll_directory", "webbrowser.open", "resource.setrlimit",
                   "resource.prlimit", "pty.spawn"}
_NETWORK_EVENTS = {"socket.connect": 1, "socket.sendto": 1, "socket.sendmsg": 1,
                   "http.client.connect": None}


class Guard:
    """The audit hook. See the module docstring: it stops accidents, it is not a security
    boundary. Every blocked action raises PermissionError("blocked: ...") and is recorded."""

    def __init__(self, write_roots, port=None):
        self.roots = [os.path.normcase(os.path.realpath(r)) for r in write_roots]
        self.port = port
        self.blocked = []
        self.on = True
        self._busy = False

    def inside(self, path, dir_fd=None) -> bool:
        if isinstance(path, int):
            return True                      # an open file descriptor was checked when opened
        try:
            p = os.fsdecode(path)
        except TypeError:
            return False
        if dir_fd is not None and not os.path.isabs(p):
            base = _fd_path(dir_fd)
            if base is None:
                return True                  # can't tell (fd-relative calls inside shutil.rmtree)
            p = os.path.join(base, p)
        real = os.path.normcase(os.path.realpath(p))
        if real == os.path.normcase(os.path.realpath(os.devnull)):
            return True
        return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in self.roots)

    def block(self, what: str):
        message = f"blocked: {what}"
        if message not in self.blocked and len(self.blocked) < 5:
            self.blocked.append(message)
        raise PermissionError(message)

    def _local(self, host) -> bool:
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        return host is None or str(host).strip("[]").lower() in LOCAL_HOSTS

    def __call__(self, event, args):
        if not self.on or self._busy:
            return
        self._busy = True
        try:
            self._check(event, args)
        finally:
            self._busy = False

    def _check(self, event, args):
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            if isinstance(mode, str):
                writing = any(c in mode for c in "wax+")
            else:
                writing = bool((flags or 0) & _WRITE_FLAGS)
            if writing and not self.inside(path):
                self.block(f"writing to {os.fsdecode(path)!r} (only the exercise folder "
                           "can be changed)")
            return
        spots = _PATH_EVENTS.get(event)
        if spots:
            for i, fd_i in spots:
                if i < len(args):
                    dir_fd = args[fd_i] if fd_i is not None and fd_i < len(args) else None
                    if not self.inside(args[i], dir_fd):
                        self.block(f"{event} on {os.fsdecode(args[i])!r} (only the exercise "
                                   "folder can be changed)")
            return
        if event in _PROGRAM_EVENTS:
            self.block(f"{event} (exercises can't start programs or open a browser)")
        if event.startswith(("ctypes.", "winreg.")):
            self.block(f"{event} (exercises can't use ctypes or the registry)")
        if event in _NETWORK_EVENTS:
            index = _NETWORK_EVENTS[event]
            if index is None:                       # http.client.connect(self, host, port)
                host, port = (tuple(args) + (None, None, None))[1:3]
            else:
                address = args[index] if index < len(args) else None
                if not isinstance(address, tuple) or len(address) < 2:
                    if event == "socket.sendmsg" and address is None:
                        return                       # sendmsg on a connected socket
                    self.block(f"network access to {address!r} (only BASE_URL works)")
                host, port = address[0], address[1]
            if not (self.port and self._local(host) and str(port) == str(self.port)):
                self.block(f"network access to {host}:{port} (only BASE_URL, the local test "
                           "server, can be reached)")
            return
        if event == "socket.getaddrinfo":
            if not self._local(args[0] if args else None):
                self.block(f"looking up {args[0]!r} on the network (only BASE_URL works)")
            return
        if event in ("socket.gethostbyname", "socket.gethostbyaddr"):
            if not self._local(args[0] if args else None):
                self.block(f"looking up {args[0]!r} on the network (only BASE_URL works)")


# ----- print() output and progress ----------------------------------------------------------


class Progress:
    """Writes progress lines to stderr (a file descriptor), straight away: the parent reads them
    when the run is killed for taking too long. Never raises."""

    def __init__(self, fd=None):
        self.fd = fd

    def send(self, **item):
        if self.fd is None:
            return
        try:
            os.write(self.fd, (PROGRESS_MARK + json.dumps(item) + "\n").encode("utf-8", "replace"))
        except Exception:  # noqa: BLE001  (a closed stderr must not change the result)
            self.fd = None


class Capture(io.TextIOBase):
    """sys.stdout while exercise code runs: keeps the first STDOUT_LIMIT characters of one stage
    (the setup or one test), streams them to the progress channel and drops the rest."""

    def __init__(self, progress=None):
        super().__init__()
        self.progress = progress or Progress()
        self.start()

    def start(self):
        self.parts, self.size, self.cut = [], 0, False

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return True

    def isatty(self):
        return False

    def write(self, s):
        if not isinstance(s, str):
            raise TypeError(f"write() argument must be str, not {type(s).__name__}")
        n = len(s)
        if self.cut or not n:
            return n
        room = STDOUT_LIMIT - self.size
        if n > room:
            s, self.cut = s[:room], True
        if s:
            self.parts.append(s)
            self.size += len(s)
            self.progress.send(print=s)
        return n

    def take(self) -> str:
        """The text of this stage (with STDOUT_CUT when it was cut); starts the next stage."""
        text = "".join(self.parts)
        if self.cut:
            text += ("" if text.endswith("\n") or not text else "\n") + STDOUT_CUT
        self.start()
        # lone surrogates ("\ud800") can't be shown or sent as UTF-8
        return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _joined(printed) -> str:
    text = "".join(printed)
    if len(text) >= STDOUT_LIMIT:
        text += ("" if text.endswith("\n") else "\n") + STDOUT_CUT
    return text


def read_progress(stderr) -> dict:
    """What a killed run had done, from its stderr (str or bytes; other lines are ignored):
    {"running": index of the test that was running (-1 = the setup, None = unknown),
     "outputs": the finished tests, "setup_stdout": str, "stdout": what the running test printed}.
    """
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    running, outputs, setup, printed = None, [], "", []
    for line in (stderr or "").split("\n"):
        if not line.startswith(PROGRESS_MARK):
            continue
        try:
            item = json.loads(line[len(PROGRESS_MARK):])
        except ValueError:
            continue                        # the last line may be cut off by the kill
        if not isinstance(item, dict):
            continue
        if item.get("stage") == "setup":
            running, printed = -1, []
        elif item.get("stage") == "test" and isinstance(item.get("i"), int):
            if running == -1:
                setup = _joined(printed)
            running, printed = item["i"], []
        elif isinstance(item.get("print"), str):
            printed.append(item["print"])
        elif isinstance(item.get("done"), dict) and isinstance(running, int) and running >= 0:
            if len(outputs) == running:
                outputs.append(item["done"])
            printed = []
    text = _joined(printed)
    if running == -1:
        setup, text = text, ""
    return {"running": running, "outputs": outputs, "setup_stdout": setup, "stdout": text}


# ----- main ---------------------------------------------------------------------------------


def _prepare_process():
    """Before any user code: own stdout, limits, environment for libraries, no .pyc files."""
    sys.dont_write_bytecode = True
    here = os.path.dirname(os.path.abspath(__file__))
    if sys.path and os.path.abspath(sys.path[0] or ".") == here:
        del sys.path[0]                     # pygym's own folder must not shadow libraries
    out_fd = os.dup(1)                      # the JSON result goes here ...
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)                     # ... and stray C-level output goes nowhere
    os.close(devnull)
    _limits()
    work = os.path.realpath(os.getcwd())
    root = os.path.dirname(work)
    roots = [work]
    for name in ("tmp", "cache"):
        extra = os.path.join(root, name)
        if os.path.isdir(extra):
            roots.append(extra)
    tmp = roots[1] if len(roots) > 1 else work
    cache = roots[2] if len(roots) > 2 else tmp
    for key, value in (("TMPDIR", tmp), ("TEMP", tmp), ("TMP", tmp), ("XDG_CACHE_HOME", cache),
                       ("MPLCONFIGDIR", cache), ("MPLBACKEND", "Agg"),
                       ("OPENBLAS_NUM_THREADS", "1"), ("OMP_NUM_THREADS", "1"),
                       ("MKL_NUM_THREADS", "1"), ("NUMEXPR_NUM_THREADS", "1")):
        os.environ[key] = value
    if "tempfile" in sys.modules:           # normally not imported yet; then it reads TMPDIR
        sys.modules["tempfile"].tempdir = None
    return out_fd, work, roots


def _write_result(out_fd, result):
    data = json.dumps(result)
    with os.fdopen(out_fd, "w", encoding="utf-8") as f:
        f.write(data)


def main():
    job = json.loads(sys.stdin.read())
    out_fd, work, roots = _prepare_process()

    if job.get("probe_import"):
        try:
            importlib.import_module(job["probe_import"])
            result = {"status": "ok"}
        except BaseException as e:  # noqa: BLE001
            result = {"status": "error", "message": f"{type(e).__name__}: {e}"}
        _write_result(out_fd, result)
        return

    target = job["target"]
    allowed = set(ALLOWED_IMPORTS)
    # The policy check is a second lock: a bad exercise file can't unlock "subprocess".
    allowed |= {m for m in (job.get("allowed_imports") or ()) if import_permitted(m, target)}

    safe = dict(vars(builtins))
    for name in REMOVED_BUILTINS:
        if name == "open" and job.get("allow_open"):
            continue
        safe.pop(name, None)
    real_import = builtins.__import__

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name.split(".")[0] not in allowed or denied_reason(name):
            raise ImportError(f"import of '{name}' is not allowed in pygym")
        return real_import(name, globals, locals, fromlist, level)

    safe["__import__"] = safe_import

    # Record real calls to the target (stops "dead code" cheating), by kind:
    #   built-in ("zip"):                 replace the name in the safe built-ins
    #   module function ("statistics.median", "numpy.cumsum"): replace the module attribute
    #   method of a built-in type ("str.join") or a numpy ufunc: can't be wrapped, so only the
    #                                     parent's static check applies (-1 here)
    # Importing the target's library here also means its import runs before the audit hook.
    calls = {"n": 0}
    owner = target.rpartition(".")[0]
    if not owner:
        checked = target in safe
        if checked:
            safe[target] = _counting(safe[target], calls)
    elif owner in METHOD_OWNER_NAMES:
        checked = False
    else:
        checked = _patch_module_target(target, allowed | SAFE_MODULES, calls)
    if allowed - SAFE_MODULES:
        try:  # libraries may import ctypes later; importing it loads a C library (blocked later)
            import ctypes  # noqa: F401
        except Exception:  # noqa: BLE001
            pass

    files = job.get("files") or {}
    fixtures = job.get("http_fixtures") or {}
    ns = {"__builtins__": safe}
    base_url = ""
    guard = Guard(roots)
    real_out = sys.stdout
    progress = Progress(2)
    captured = Capture(progress)
    sys.stdout = captured                 # the user's print()s, kept per test (the JSON result
    setup_stdout = None                   # goes to out_fd, never to sys.stdout)
    try:
        write_files(work, files)
        if fixtures:
            server = start_fixture_server(fixtures)
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            guard.port = server.server_address[1]
            ns["BASE_URL"] = base_url
            os.environ["PYGYM_BASE_URL"] = base_url
        if hasattr(sys, "addaudithook"):
            sys.addaudithook(guard)
        progress.send(stage="setup")
        exec(job["code"], ns)
        setup_stdout = captured.take()
        fn = ns.get(job["function_name"])
        if not callable(fn):
            raise NameError(f"Your code must define a function named '{job['function_name']}'.")
        outputs = []
        for i, args in enumerate(job["inputs"]):
            progress.send(stage="test", i=i)
            try:
                if i:                         # every test starts with the same folder
                    os.makedirs(work, exist_ok=True)
                    os.chdir(work)
                    write_files(work, files)
                value = norm(fn(*args))
                if base_url:
                    value = _replace_text(value, base_url, "BASE_URL")
                out = {"value": value}
            except BaseException as e:  # noqa: BLE001  (SystemExit too: the next test still runs)
                out = {"error": f"{type(e).__name__}: {e}"}
            printed = captured.take()
            if printed:
                out["stdout"] = printed
            outputs.append(out)
            progress.send(done=out)
        result = {"status": "ok", "outputs": outputs,
                  "target_calls": calls["n"] if checked else -1}
    except BaseException as e:  # noqa: BLE001
        result = {"status": "error", "message": f"{type(e).__name__}: {e}"}
        if setup_stdout is None:              # the top-level code raised: keep what it printed
            setup_stdout = captured.take()
    finally:
        sys.stdout = real_out
    guard.on = False
    if setup_stdout:
        result["setup_stdout"] = setup_stdout
    result["blocked"] = list(guard.blocked)
    _write_result(out_fd, result)


if __name__ == "__main__":
    main()
