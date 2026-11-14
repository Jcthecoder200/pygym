"""Where exercise code runs: a new temporary folder, inside an operating-system sandbox if there is
one. Used for the student's code and for the AI model's reference solutions alike.

Every run:
  * gets a NEW temporary folder <root> (deleted afterwards). <root>/work is the current
    directory, where the exercise's `files` are written. The student's own folder is never used:
    copying it could be huge (it is often the home folder) and tests must not depend on it;
  * runs `_runner.py` in a separate Python process with memory, file-size and time limits, and
    with only a few harmless environment variables (no API keys or proxies);
  * has the Python audit hook of `_runner.py` (writes outside the folder, programs, ctypes and
    network access except the local test server are blocked). Not a security boundary.

On top of that, the best layer this computer has (`level()`):
  "bwrap"         Linux with bubblewrap: the whole system is read-only, the home folder is
                  replaced by an empty one (only Python's own folders are visible again), no
                  network except inside the sandbox (127.0.0.1), its own process list.
  "sandbox-exec"  macOS: a sandbox profile that denies writes outside the folder, the network
                  (except localhost) and starting other programs. UNTESTED: written from Apple's
                  documentation, used only if a quick self-test passes.
  "audit-hook"    everywhere else (Windows): the audit hook and the limits only.
Set PYGYM_SANDBOX=audit-hook to switch the OS layer off (for example if bubblewrap misbehaves);
other values can't force a layer this computer doesn't have.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ._runner import (DENIED_MODULES, PROGRESS_MARK, SAFE_MODULES, SYSTEM_MODULES,
                      denied_reason, import_permitted, read_progress)

__all__ = ["LEVELS", "level", "label", "run", "refusal", "install_hint", "importable",
           "check_files", "check_fixtures", "DENIED_MODULES", "import_permitted"]

LEVELS = ("bwrap", "sandbox-exec", "audit-hook")
LABELS = {"bwrap": "bubblewrap", "sandbox-exec": "macOS sandbox-exec",
          "audit-hook": "basic (Python audit hook only)"}
ENV_VAR = "PYGYM_SANDBOX"
RUNNER = str(Path(__file__).with_name("_runner.py"))
INSIDE_ROOT = "/tmp/pygym"            # where <root> appears inside bubblewrap
MAX_FILES = 20
MAX_FILES_BYTES = 64 * 1024
MAX_FIXTURES = 20
MAX_FIXTURES_BYTES = 16 * 1024
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
# Environment variables passed to the sandbox (everything else, e.g. API keys, is dropped).
KEEP_ENV = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATHEXT", "COMSPEC", "LANG",
            "LANGUAGE", "LC_ALL", "LC_CTYPE", "TZ", "HOME", "USERPROFILE", "LD_LIBRARY_PATH")
# import name -> pip name, for "not installed" messages. Only well-known libraries.
KNOWN_PYPI = {
    "numpy": "numpy", "pandas": "pandas", "requests": "requests", "scipy": "scipy",
    "matplotlib": "matplotlib", "sympy": "sympy", "bs4": "beautifulsoup4", "yaml": "pyyaml",
    "PIL": "pillow", "sklearn": "scikit-learn", "cv2": "opencv-python", "httpx": "httpx",
    "attr": "attrs", "attrs": "attrs", "more_itertools": "more-itertools",
    "dateutil": "python-dateutil", "toolz": "toolz", "networkx": "networkx", "polars": "polars",
    "pydantic": "pydantic", "tabulate": "tabulate", "regex": "regex", "arrow": "arrow",
    "seaborn": "seaborn", "statsmodels": "statsmodels", "torch": "torch", "tqdm": "tqdm",
    "lxml": "lxml", "flask": "flask", "django": "django", "jinja2": "jinja2", "emoji": "emoji",
    "sortedcontainers": "sortedcontainers", "pytz": "pytz", "simplejson": "simplejson",
    "scrapy": "scrapy", "plotly": "plotly", "openpyxl": "openpyxl", "xlrd": "xlrd",
    "pyarrow": "pyarrow", "numba": "numba", "bitarray": "bitarray", "unidecode": "unidecode",
    "inflect": "inflect", "nltk": "nltk", "textblob": "textblob", "faker": "faker",
}

_best: Dict[str, str] = {}             # {"level": ...}: the probe result, once per process


# ----- which level ---------------------------------------------------------------------------


def level() -> str:
    """"bwrap" | "sandbox-exec" | "audit-hook": the protection the next run gets."""
    best = _best.get("level")
    if best is None:
        best = _best["level"] = _detect()
    if os.environ.get(ENV_VAR, "").strip().lower() == "audit-hook":
        return "audit-hook"
    return best


def label(lv: Optional[str] = None) -> str:
    """"bubblewrap", "macOS sandbox-exec" or "basic (Python audit hook only)"."""
    return LABELS.get(lv or level(), lv or "")


def reset() -> None:
    """Forget the detected level (tests)."""
    _best.clear()
    _python_paths.cache_clear()
    importable.cache_clear()


def _detect() -> str:
    if sys.platform.startswith("linux") and shutil.which("bwrap") and _self_test("bwrap"):
        return "bwrap"
    if sys.platform == "darwin" and shutil.which("sandbox-exec") and _self_test("sandbox-exec"):
        return "sandbox-exec"
    return "audit-hook"


def _self_test(lv: str) -> bool:
    """Run a tiny program in that sandbox once: some systems forbid user namespaces."""
    root = _new_root()
    try:
        cmd = command(lv, root, [python(), "-E", "-B", "-c",
                                 "open('ok.txt', 'w').write('ok'); print('ok')"])
        proc = subprocess.run(cmd, cwd=os.path.join(root, "work"), capture_output=True,
                              text=True, timeout=15, env=child_env())
        return proc.returncode == 0 and proc.stdout.strip() == "ok"
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        _remove_tree(root)


# ----- the command line for each level -----------------------------------------------------


def python() -> str:
    """This Python, as a clean absolute path ("venv/bin/../bin/python" breaks inside bwrap).
    Not resolved through symlinks: a virtual environment's python must keep its own path."""
    return os.path.normpath(os.path.abspath(sys.executable))


@lru_cache(maxsize=None)
def _python_paths() -> Tuple[str, ...]:
    """Folders the sandboxed Python needs to read: its prefixes and sys.path (as the runner
    sees it), plus pygym's own folder."""
    found = [sys.prefix, sys.base_prefix, sys.exec_prefix, getattr(sys, "base_exec_prefix", ""),
             os.path.dirname(python()), os.path.dirname(os.path.realpath(python())),
             os.path.dirname(RUNNER)]
    probe = ("import json, sys; print(json.dumps([sys.prefix, sys.base_prefix, sys.exec_prefix,"
             " sys.base_exec_prefix] + sys.path))")
    with tempfile.TemporaryDirectory(prefix="pygym-probe-") as cwd:
        try:
            proc = subprocess.run([python(), "-E", "-B", "-c", probe], cwd=cwd,
                                  capture_output=True, text=True, timeout=15, env=child_env())
            found += [p for p in json.loads(proc.stdout) if p and p != cwd]
        except (OSError, ValueError, subprocess.SubprocessError):
            found += [p for p in sys.path if p and os.path.isabs(p)]
    for p in os.environ.get("LD_LIBRARY_PATH", "").split(os.pathsep):
        if p:
            found.append(p)
    out: List[str] = []
    for p in found:
        for q in (os.path.abspath(p), os.path.realpath(p)):
            if q not in out and os.path.exists(q):
                out.append(q)
    return tuple(out)


def _under(path: str, folder: str) -> bool:
    folder = folder.rstrip(os.sep) or os.sep
    return path == folder or path.startswith(folder + os.sep)


def bwrap_command(root: str, program: List[str], home: Optional[str] = None,
                  read_only: Iterable[str] = ()) -> List[str]:
    """bubblewrap around `program`: read-only system, empty /tmp and home folder (Python's own
    folders under them are bound back read-only), <root> writable at /tmp/pygym, cwd
    /tmp/pygym/work, no network, own process list, killed with pygym."""
    cmd = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    hidden = ["/tmp"]
    if home and os.path.isabs(home) and home.rstrip(os.sep) and not _under(home, "/tmp"):
        cmd += ["--tmpfs", home]
        hidden.append(home)
    kept: List[str] = []
    for p in sorted(set(read_only), key=len):
        if any(_under(p, h) for h in hidden) and not any(_under(p, k) for k in kept):
            kept.append(p)
            cmd += ["--ro-bind", p, p]
    cmd += ["--bind", root, INSIDE_ROOT, "--chdir", INSIDE_ROOT + "/work",
            "--unshare-net", "--unshare-pid", "--unshare-ipc", "--die-with-parent",
            "--new-session"]
    return cmd + list(program)


def _sb(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_profile(root: str, python_dirs: Iterable[str],
                  executables: Iterable[str] = ()) -> str:
    """sandbox-exec profile: everything allowed except writes outside <root>, the network
    (localhost is fine) and starting programs other than Python itself. Untested on macOS.

    `executables` are the interpreter's own file paths (sys.executable and its real path). They
    are needed besides the folders in `python_dirs` when the interpreter lives outside its
    prefix: Apple's Python 3.9 (Xcode Command Line Tools) runs /Library/Developer/CommandLineTools
    /usr/bin/python3, while its prefix is .../Library/Frameworks/Python3.framework/Versions/3.9.
    A `literal` allows exactly that file, not the other programs next to it."""
    roots = sorted({root, os.path.realpath(root)})
    lines = [
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        '(allow network-bind network-inbound (local ip "localhost:*"))',
        '(allow network-outbound (remote ip "localhost:*"))',
        "(deny file-write*)",
        *[f"(allow file-write* (subpath {_sb(r)}))" for r in roots],
        '(allow file-write* (literal "/dev/null") (literal "/dev/dtracehelper"))',
        "(deny process-fork)",
        "(deny process-exec)",
        *[f"(allow process-exec (subpath {_sb(p)}))" for p in sorted(set(python_dirs))],
        *[f"(allow process-exec (literal {_sb(p)}))" for p in sorted(set(executables))],
    ]
    return "\n".join(lines) + "\n"


def command(lv: str, root: str, program: List[str]) -> List[str]:
    """The full command line that runs `program` at level `lv` in folder <root>."""
    if lv == "bwrap":
        return bwrap_command(root, program, home=os.path.expanduser("~"),
                             read_only=_python_paths())
    if lv == "sandbox-exec":
        dirs = {sys.prefix, sys.base_prefix, os.path.realpath(sys.prefix),
                os.path.realpath(sys.base_prefix)}
        exes = {python(), os.path.realpath(python())}
        return ["sandbox-exec", "-p", macos_profile(root, dirs, exes)] + list(program)
    return list(program)


def child_env() -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() in KEEP_ENV}
    # Exercises only ever talk to the local fixture server, never through a proxy. Saying so also stops
    # urllib/requests on Windows from looking for proxy settings in the registry: that lookup is
    # refused by the audit hook ("winreg.OpenKey"), harmless to the answer but it used to leave a
    # "(pygym blocked: ...)" note on the verdict of every HTTP exercise (seen on Windows 10/11).
    env["NO_PROXY"] = env["no_proxy"] = "*"
    return env


# ----- one run -----------------------------------------------------------------------------


def _new_root() -> str:
    root = tempfile.mkdtemp(prefix="pygym-run-")
    for name in ("work", "tmp", "cache"):
        os.mkdir(os.path.join(root, name))
    return root


def _remove_tree(root: str) -> None:
    def make_writable(func, path, _exc):
        try:
            os.chmod(os.path.dirname(path), stat.S_IRWXU)
            os.chmod(path, stat.S_IRWXU)
            func(path)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(root, onexc=make_writable)
    else:  # pragma: no cover
        shutil.rmtree(root, onerror=make_writable)


def run(job: dict, timeout: float = 5.0, lv: Optional[str] = None) -> dict:
    """Run one job (see _runner.py) in a new folder at level `lv` (default: level()).
    Returns the runner's JSON dict, or {"status": "error", ...}, or for a run that took too long
    {"status": "timeout", "running": test index (-1 = the setup, None = unknown), "outputs": the
    finished tests, "setup_stdout": str, "stdout": what the running test printed}."""
    lv = lv or level()
    root = _new_root()
    try:
        cmd = command(lv, root, [python(), "-E", "-B", RUNNER])
        try:
            proc = subprocess.run(cmd, input=json.dumps(job), capture_output=True, text=True,
                                  timeout=timeout, cwd=os.path.join(root, "work"),
                                  env=child_env())
        except subprocess.TimeoutExpired as e:
            # what the runner reported before it was stopped: which test, what it printed
            return dict(read_progress(e.stderr), status="timeout")
        except OSError as e:
            return {"status": "error", "message": f"The sandbox could not start ({e})."}
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            first = [ln for ln in (proc.stderr or "").strip().splitlines()
                     if not ln.startswith(PROGRESS_MARK)][:1]
            if first and first[0].startswith(("bwrap:", "sandbox-exec:")):
                return {"status": "error", "message": f"The sandbox could not start ({first[0]})."}
            return {"status": "error", "message": "Your code crashed or used too much memory."}
        return result if isinstance(result, dict) else {"status": "error",
                                                         "message": "Unexpected sandbox reply."}
    finally:
        _remove_tree(root)


# ----- which libraries ------------------------------------------------------------------------


def refusal(name: str, lv: Optional[str] = None) -> Tuple[str, str]:
    """Can exercises be about `name` (a library "numpy", "os.path" or a target "os.system")?
    Returns ("", "") if yes, else (what, why): ("subprocess", "it starts ...")."""
    name = (name or "").strip()
    parts = name.split(".")
    for i in range(1, len(parts) + 1):
        what = ".".join(parts[:i])
        if DENIED_MODULES.get(what):
            return what, DENIED_MODULES[what]
    if parts[0] in SYSTEM_MODULES and (lv or level()) == "audit-hook":
        return parts[0], ("it works with files, programs or the network, and this computer has "
                          "no operating-system sandbox for pygym (see `pygym --status`)")
    why = denied_reason(name)
    if why:
        return name, why
    return "", ""


def install_hint(name: str) -> str:
    """"'numpy' is not installed in this Python. Install it with: <python> -m pip install numpy"
    for well-known libraries; "" otherwise. pygym never installs anything itself."""
    top = (name or "").strip().split(".")[0]
    pip = KNOWN_PYPI.get(top) or {k.lower(): v for k, v in KNOWN_PYPI.items()}.get(top.lower())
    if not pip:
        return ""
    exe = subprocess.list2cmdline([python()]) if os.name == "nt" else shlex.quote(python())
    return f"'{top}' is not installed in this Python. Install it with: {exe} -m pip install {pip}"


def _is_stdlib(top: str) -> bool:
    names = getattr(sys, "stdlib_module_names", None)
    if names is not None:
        return top in names
    return top in SAFE_MODULES or top in sys.builtin_module_names


@lru_cache(maxsize=None)
def importable(top: str) -> Tuple[bool, str]:
    """Does `import top` work inside the sandbox? Standard modules are assumed to.
    (False, "ImportError: ...") otherwise. Remembered per process."""
    if _is_stdlib(top):
        return True, ""
    res = run({"probe_import": top}, timeout=30.0)
    if res.get("status") == "ok":
        return True, ""
    return False, str(res.get("message") or res.get("status") or "unknown problem")[:200]


# ----- the exercise's fixtures ---------------------------------------------------------------


def check_files(files) -> List[str]:
    """Problems with an exercise's `files` ({relative path: text}). [] means fine."""
    if not isinstance(files, dict):
        return ["files must be an object that maps file names to their text"]
    problems = []
    if len(files) > MAX_FILES:
        problems.append(f"files has more than {MAX_FILES} files")
    total = 0
    for path, text in files.items():
        if not isinstance(path, str) or not isinstance(text, str):
            problems.append("files must map file names (text) to file contents (text)")
            break
        parts = path.replace("\\", "/").split("/")
        if (not path.strip() or path.startswith(("/", "\\", "~")) or ":" in path
                or any(p in ("", ".", "..") for p in parts) or "\0" in path):
            problems.append(f"file name {path!r} must be a simple relative path "
                            "(no '/', '..', '~' or drive at the start)")
        total += len(text.encode("utf-8")) + len(path)
    if total > MAX_FILES_BYTES:
        problems.append(f"files are larger than {MAX_FILES_BYTES // 1024} KB in total")
    return problems


def check_fixtures(fixtures) -> List[str]:
    """Problems with an exercise's `http_fixtures` ({"/path": {"status", "json" | "text"}})."""
    if not isinstance(fixtures, dict):
        return ["http_fixtures must be an object that maps paths like \"/users/1\" to responses"]
    problems = []
    if len(fixtures) > MAX_FIXTURES:
        problems.append(f"http_fixtures has more than {MAX_FIXTURES} paths")
    try:
        size = len(json.dumps(fixtures))
    except (TypeError, ValueError):
        return ["http_fixtures must contain only JSON values"]
    if size > MAX_FIXTURES_BYTES:
        problems.append(f"http_fixtures is larger than {MAX_FIXTURES_BYTES // 1024} KB")
    for key, spec in fixtures.items():
        method, _, rest = key.partition(" ") if isinstance(key, str) else ("", "", key)
        path = rest if method in HTTP_METHODS else key
        if not isinstance(path, str) or not path.startswith("/"):
            problems.append(f"http_fixtures path {key!r} must start with '/'")
        if not isinstance(spec, dict) or not ({"json", "text"} & set(spec)):
            problems.append(f"http_fixtures[{key!r}] must be an object with \"json\" or \"text\"")
            continue
        status = spec.get("status", 200)
        if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
            problems.append(f"http_fixtures[{key!r}] status must be a number from 100 to 599")
        if "text" in spec and not isinstance(spec["text"], str):
            problems.append(f"http_fixtures[{key!r}] text must be a string")
        if "headers" in spec and not isinstance(spec["headers"], dict):
            problems.append(f"http_fixtures[{key!r}] headers must be an object")
    return problems
