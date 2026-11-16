"""Tips at your error: when the student's own code crashes, name the built-ins on the failing line.

    Traceback (most recent call last):
      ...
    ValueError: zip() argument 2 is shorter than argument 1
    pygym: this line uses zip(); 30-second exercise? exercise('zip')

No model, no network: the tip comes from the traceback, the source line and the bundled
exercises only, so it is instant and the same every time. docs/ERROR_TIPS.md has the details.

How it is turned on
  * `import pygym` (and `from pygym import exercise`): for this process, unless the saved setting
    `tips` is off. IPython (terminal or kernel): `ip.set_custom_exc`; elsewhere: `sys.excepthook`.
  * `%load_ext pygym`, `pygym.tips(True)` / `pygym.tips(False)`: on / off for this process.
  * `pygym --tips on`: always on WITHOUT any import: a `.pth` line in site-packages (a tiny
    excepthook that imports this module only when an exception reaches it) and an IPython startup
    file. `pygym --tips off` removes both and saves the setting off.
  * The environment variable PYGYM_NO_TIPS=1 turns every tip off (pygym's sandbox sets it for the
    exercise processes).

This module is imported by `import pygym` and by the global hook, so at import time it uses the
standard library only (no rich, no IPython, no pygym.ai). Every hook shows the normal traceback
FIRST and the tip after it; any failure while making the tip is swallowed.

`pygym.tips` is this module AND a function: `pygym.tips(False)` calls the module (see the end of
the file), so `import pygym.tips` and `pygym.tips(...)` never shadow each other.
"""
from __future__ import annotations

import builtins
import linecache
import os
import sys
import types
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["make_tip", "find", "command_for", "install", "uninstall", "enabled",
           "enable_global", "disable_global", "global_status", "Tip"]

ENV_OFF = "PYGYM_NO_TIPS"            # set (to anything but "") = no tips at all in this process
ENV_SITE_DIR = "PYGYM_TIPS_SITE_DIR"  # where `pygym --tips on` puts the .pth (tests, unusual setups)
PTH_NAME = "pygym_tips.pth"
STARTUP_NAME = "50-pygym-tips.py"
MAX_NAMES = 3
OFF_NOTE = "(turn off: pygym --tips off)"   # only on the first tip of a process
_MARK = "_pygym_tips"                 # attribute on our excepthooks: "import" or "global"

# Process state. Kept across importlib.reload / %autoreload (the module dict is reused), so a
# re-import never installs a second hook or forgets the on/off choice.
_S: Dict[str, Any] = globals().get("_S") or {
    "override": None,     # None = follow the saved setting; True / False = pygym.tips(...)
    "noted": False,       # the "turn off" note was shown
    "last": None,         # (exception, traceback) of the last tip: one tip per error
    "presets": None,      # targets with hand-written exercises (from the dataset)
    "ai_ok": {},          # AI target -> accepted by AI mode (quick check, cached)
}


# ----- which frame, which source ------------------------------------------------------------


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _system_dirs() -> Tuple[str, ...]:
    dirs = _S.get("dirs")
    if dirs is None:
        found = []
        for base in (os.path.dirname(os.__file__), os.path.dirname(__file__)):
            for p in (base, os.path.realpath(base)):
                p = _norm(p)
                if p not in found:
                    found.append(p)
        dirs = _S["dirs"] = tuple(found)
    return dirs


def is_user_file(filename: str) -> bool:
    """Code the student wrote: not the standard library, not an installed package, not pygym,
    not frozen. "<stdin>", "<python-input-3>", "<pyshell#2>", "<ipython-input-...>" are user code."""
    if not filename:
        return False
    if filename.startswith("<"):
        return not filename.startswith("<frozen")
    path = _norm(filename)
    parts = path.replace("\\", "/").split("/")
    if "site-packages" in parts or "dist-packages" in parts:
        return False
    for d in _system_dirs():
        if path == d or path.startswith(d.rstrip(os.sep) + os.sep):
            return False
    return True


def _is_user_module(module) -> bool:
    name = getattr(module, "__name__", "")
    if not isinstance(name, str) or name == "__main__" or name.split(".")[0] == "pygym":
        return True
    filename = getattr(module, "__file__", None)
    return bool(filename) and is_user_file(filename)


def _last_user_tb(tb):
    found = None
    while tb is not None:
        if is_user_file(tb.tb_frame.f_code.co_filename):
            found = tb
        tb = tb.tb_next
    return found


def _readline_lines(lineno: int) -> List[str]:
    """The classic REPL (<stdin>) keeps no source; its readline history does. The statement is
    the shortest run of last history items that starts unindented and compiles."""
    rl = sys.modules.get("readline")
    if rl is None:
        return []
    try:
        n = rl.get_current_history_length()
        items = [rl.get_history_item(i) or "" for i in range(max(1, n - 19), n + 1)]
    except Exception:
        return []
    import ast
    for k in range(1, len(items) + 1):
        chunk = items[-k:]
        if len(chunk) < lineno or not chunk[0].strip() or chunk[0][:1].isspace():
            continue
        text = "\n".join(chunk) + "\n"
        try:
            ast.parse(text)
        except SyntaxError:
            continue
        return text.splitlines(True)
    return []


def _idle_lines(filename: str) -> List[str]:
    """IDLE's shell keeps "<pyshell#N>" in the GUI process; idlelib/run.py cleanup_traceback()
    fetches it the same way (rpchandler.remotecall('linecache', 'getline', ...))."""
    run = sys.modules.get("idlelib.run")
    if run is None:
        return []
    try:
        handler = run.rpc.objecttable["exec"].rpchandler
        lines = handler.remotecall("linecache", "getlines", (filename,), {})
        return [str(x) for x in lines] if isinstance(lines, list) else []
    except Exception:
        return []


def source_lines(filename: str, frame, lineno: int) -> List[str]:
    if filename == "<string>":
        return []   # python -c, exec(text): no tip
    lines = linecache.getlines(filename, frame.f_globals)
    if lines:
        return lines
    # Python 3.13+: both REPLs register what was typed by code object, not by file name
    # (linecache._register_code; "<python-input-3>", and "<stdin-1>" shown as "<stdin>").
    by_code = getattr(linecache, "_getlines_from_code", None)
    if by_code is not None:
        try:
            lines = list(by_code(frame.f_code))
        except Exception:
            lines = []
        if lines:
            return lines
    if filename.startswith("<pyshell#"):
        return _idle_lines(filename)
    if filename.startswith("<stdin"):
        return _readline_lines(lineno)
    return []


# ----- the calls on the failing line ---------------------------------------------------------


class _Call:
    __slots__ = ("chain", "span")

    def __init__(self, chain: Tuple[str, ...], span: Tuple[int, int, int, int]):
        self.chain = chain      # ("zip",) or ("np", "mean") or ("statistics", "median")
        self.span = span        # (lineno, col, end_lineno, end_col)


def _chain(node) -> Optional[Tuple[str, ...]]:
    import ast
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return tuple(reversed(parts))
    return None    # a method of a value: "".join(...), df.mean(), f()(...)


def _calls_from_tree(tree, line: int) -> List[_Call]:
    """Calls whose span covers `line`. Compound statements (for/if/while/with/def/class/try/
    match) count with their header only; their bodies are statements of their own."""
    import ast
    found: List[_Call] = []

    def visit(node) -> None:
        for _field, value in ast.iter_fields(node):
            for item in (value if isinstance(value, list) else [value]):
                if not isinstance(item, ast.AST):
                    continue
                if isinstance(getattr(item, "body", None), list):     # a statement-like node
                    start = getattr(item, "lineno", None)
                    end = getattr(item, "end_lineno", None)
                    if start is None or end is None or start <= line <= end:
                        visit(item)
                    continue
                for sub in ast.walk(item):
                    if (isinstance(sub, ast.Call) and sub.lineno <= line <= (sub.end_lineno or sub.lineno)):
                        chain = _chain(sub.func)
                        if chain:
                            found.append(_Call(chain, (sub.lineno, sub.col_offset,
                                                       sub.end_lineno or sub.lineno,
                                                       sub.end_col_offset or 0)))

    visit(tree)
    return found


def _calls_from_tokens(text: str, line: int) -> List[_Call]:
    """The file does not parse: `name(` and `a.b(` on that one line, from the tokenizer."""
    import io
    import keyword
    import tokenize
    toks = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            toks.append(tok)
    except (tokenize.TokenError, SyntaxError, IndentationError):
        pass
    def is_name(t) -> bool:
        return t.type == tokenize.NAME and not keyword.iskeyword(t.string)

    found: List[_Call] = []
    for i, tok in enumerate(toks):
        if not (tok.type == tokenize.OP and tok.string == "(" and i > 0 and is_name(toks[i - 1])):
            continue
        j = i - 1
        names = [toks[j].string]
        method = False
        while j >= 1 and toks[j - 1].string == ".":
            if j >= 2 and is_name(toks[j - 2]):
                j -= 2
                names.append(toks[j].string)
            else:
                method = True       # x().foo(, "".join(, (a).b(
                break
        if method or (j >= 1 and toks[j - 1].string in ("def", "class")):
            continue
        found.append(_Call(tuple(reversed(names)), (line, toks[j].start[1], line, tok.end[1])))
    return found


def _imports_from(tree) -> Dict[str, Tuple[str, str]]:
    """{local name: (module, name)} from `from M import name [as local]`."""
    import ast
    out: Dict[str, Tuple[str, str]] = {}
    if tree is None:
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                if alias.name != "*":
                    out[alias.asname or alias.name] = (node.module, alias.name)
    return out


def _position(tb) -> Optional[Tuple[int, int, int, int]]:
    """(lineno, col, end_lineno, end_col) of the failing instruction (Python 3.11+), else None."""
    code = tb.tb_frame.f_code
    positions = getattr(code, "co_positions", None)
    if positions is None or tb.tb_lasti < 0:
        return None
    import itertools
    try:
        pos = next(itertools.islice(positions(), tb.tb_lasti // 2, None), None)
    except Exception:
        return None
    if not pos or any(p is None for p in pos):
        return None
    lineno, end_lineno, col, end_col = pos
    return lineno, col, end_lineno, end_col


# ----- resolving names in the failing frame --------------------------------------------------

_MISSING = object()
_SHADOWED = object()     # a local variable of that name that has no value yet


def _lookup(frame, name: str):
    try:
        local = frame.f_locals
        if name in local:
            return local[name]
    except Exception:
        pass
    if name in frame.f_globals:
        return frame.f_globals[name]
    code = frame.f_code
    if name in code.co_varnames or name in code.co_cellvars or name in code.co_freevars:
        return _SHADOWED
    return frame.f_builtins.get(name, _MISSING) if isinstance(frame.f_builtins, dict) else _MISSING


def _module_target(chain: Tuple[str, ...], frame) -> Optional[str]:
    """np.linalg.norm -> "numpy.linalg.norm"; st.median -> "statistics.median". The root must be
    a library module; every step but the last a module too (Counter.most_common is a method)."""
    root = _lookup(frame, chain[0])
    if not isinstance(root, types.ModuleType) or _is_user_module(root):
        return None
    obj: Any = root
    for i, attr in enumerate(chain[1:], 1):
        if i < len(chain) - 1 and not isinstance(obj, types.ModuleType):
            return None
        obj = getattr(obj, attr, _MISSING)
        if obj is _MISSING:
            return None
        if i < len(chain) - 1 and not isinstance(obj, types.ModuleType):
            return None
    if not callable(obj) or isinstance(obj, types.ModuleType):
        return None
    return ".".join((root.__name__,) + chain[1:])


def _object_target(name: str, value, imports: Dict[str, Tuple[str, str]]) -> Optional[str]:
    """`median` after `from statistics import median` -> "statistics.median"."""
    if not callable(value) or isinstance(value, types.ModuleType):
        return None
    if name in imports:
        mod_name, attr = imports[name]
        mod = sys.modules.get(mod_name)
        if mod is not None and getattr(mod, attr, None) is value and not _is_user_module(mod):
            return f"{mod_name}.{attr}"
    mod_name = getattr(value, "__module__", None)
    short = getattr(value, "__name__", None)
    qual = getattr(value, "__qualname__", short)
    if not isinstance(mod_name, str) or not isinstance(short, str) or "." in str(qual):
        return None
    for candidate in (mod_name, mod_name.lstrip("_")):     # heapq.heappush lives in _heapq
        mod = sys.modules.get(candidate)
        if mod is not None and getattr(mod, short, None) is value and not _is_user_module(mod):
            return f"{candidate}.{short}"
    return None


def _presets() -> frozenset:
    found = _S.get("presets")
    if found is None:
        from . import loader
        found = _S["presets"] = frozenset(loader.available_functions())
    return found


def _ai_accepts(target: str) -> bool:
    cache = _S["ai_ok"]
    if target not in cache:
        try:
            from .ai import targets
            cache[target] = bool(targets.quick_check(target))
        except Exception:
            cache[target] = False
    return cache[target]


def _saved_mode() -> str:
    from . import config
    return config.load().mode


# ----- the analysis --------------------------------------------------------------------------


def _skip(etype, value) -> bool:
    if not isinstance(etype, type) or not issubclass(etype, Exception):
        return True      # KeyboardInterrupt, SystemExit, GeneratorExit, ...
    if issubclass(etype, SyntaxError):
        return True      # also IndentationError: the line never ran
    loader = sys.modules.get("pygym.loader")
    return loader is not None and isinstance(value, loader.ExerciseUnavailable)


def _named_in_message(value) -> set:
    import re
    try:
        message = str(value)
    except Exception:
        return set()
    names = set(re.findall(r"([A-Za-z_][\w.]*)\(\)", message))
    first = re.match(r"([A-Za-z_][\w.]*) ", message)     # 3.10: "zip argument #2 must ..."
    if first:
        names.add(first.group(1))
    return names


def find(etype, value, tb, *, mode: Optional[str] = None,
         presets: Optional[Sequence[str]] = None) -> List[Tuple[str, str]]:
    """The functions to suggest for this error, best first: [(target, "preset" | "ai"), ...].

    mode: "preset" / "ai" (None = the saved setting; library functions only in "ai").
    presets: targets with hand-written exercises (None = the bundled dataset)."""
    if tb is None or _skip(etype, value):
        return []
    user_tb = _last_user_tb(tb)
    if user_tb is None:
        return []
    frame = user_tb.tb_frame
    filename = frame.f_code.co_filename
    line = user_tb.tb_lineno
    if line is None or line < 1:
        return []
    lines = source_lines(filename, frame, line)
    if len(lines) < line:
        return []
    import ast
    text = "".join(lines)
    try:
        tree = ast.parse(text)
        calls = _calls_from_tree(tree, line)
    except (SyntaxError, ValueError, RecursionError):
        tree = None
        calls = _calls_from_tokens(lines[line - 1], line)
    if not calls:
        return []
    preset_set = frozenset(presets) if presets is not None else _presets()
    ai = None if mode is None else (mode == "ai")
    imports = _imports_from(tree) if tree is not None else {}
    position = _position(user_tb)
    named = _named_in_message(value)

    scored = []
    for call in calls:
        target, kind = _target_of(call.chain, frame, imports, preset_set)
        if target is None:
            continue
        if kind == "ai":
            if ai is None:
                ai = _saved_mode() == "ai"
            if not ai or not _ai_accepts(target):
                continue
        l1, c1, l2, c2 = call.span
        holds = position is not None and (l1, c1) <= position[:2] and position[2:] <= (l2, c2)
        size = (l2 - l1, c2 - c1) if holds else (0, 0)
        mentioned = target in named or target.rpartition(".")[2] in named
        scored.append(((0 if holds else 1, size, 0 if mentioned else 1, l1, c1), target, kind))
    scored.sort(key=lambda s: s[0])
    out: List[Tuple[str, str]] = []
    for _key, target, kind in scored:
        if all(t != target for t, _k in out):
            out.append((target, kind))
    return out[:MAX_NAMES]


def _target_of(chain: Tuple[str, ...], frame, imports, presets) -> Tuple[Optional[str], str]:
    if len(chain) == 1:
        name = chain[0]
        value = _lookup(frame, name)
        if value is _MISSING or value is _SHADOWED:
            return None, ""
        real = getattr(builtins, name, _MISSING)
        if real is not _MISSING and value is real:
            return (name, "preset") if name in presets else (None, "")
        target = _object_target(name, value, imports)      # from statistics import median
    else:
        target = _module_target(chain, frame)
    if target is None:
        return None, ""
    return target, ("preset" if target in presets else "ai")


# ----- the command and the text ---------------------------------------------------------------


def _on_path(name: str) -> Optional[str]:
    exts = [""]
    if os.name == "nt":
        exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder:
            continue
        for ext in exts:
            path = os.path.join(folder, name + ext)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
    return None


def _python_name() -> str:
    """How to start THIS Python from a shell: its file name when that name on PATH is this
    interpreter, else the full path."""
    exe = sys.executable or ""
    if not exe:
        return "python"
    base = os.path.basename(exe)
    if os.name == "nt" and base.lower().endswith(".exe"):
        base = base[:-4]
    found = _on_path(base)
    try:
        same = found is not None and os.path.samefile(found, exe)
    except OSError:
        same = False
    if same:
        return base
    return f'"{exe}"' if " " in exe else exe


def command_for(target: str, *, interactive: bool, namespace: Optional[Dict[str, Any]] = None) -> str:
    """What the student types next to start the exercise, where they are now."""
    arg = repr(target)
    if not interactive:
        if _on_path("pygym"):
            return f"pygym {target}"
        return f"{_python_name()} -m pygym {target}"
    pg = sys.modules.get("pygym")
    ns = namespace or {}
    func = getattr(pg, "exercise", None) if pg is not None else None
    if func is not None:
        bound = sorted(k for k, v in list(ns.items())
                       if v is func and isinstance(k, str) and not k.startswith("_"))
        if bound:
            return f"{'exercise' if 'exercise' in bound else bound[0]}({arg})"
    if pg is not None:
        aliases = sorted(k for k, v in list(ns.items())
                         if v is pg and isinstance(k, str) and not k.startswith("_"))
        if aliases:
            return f"{'pygym' if 'pygym' in aliases else aliases[0]}.exercise({arg})"
    return f"from pygym import exercise; exercise({arg})"


def _names_text(names: Sequence[str]) -> str:
    calls = [f"{n}()" for n in names]
    if len(calls) == 1:
        return calls[0]
    return ", ".join(calls[:-1]) + " and " + calls[-1]


class Tip:
    """One tip: the functions (best first), the command for the first, and the line to show."""

    def __init__(self, found: List[Tuple[str, str]], command: str):
        self.targets = [t for t, _k in found]
        self.kind = found[0][1]
        self.command = command
        self.question = "AI exercise (local model)?" if self.kind == "ai" else "30-second exercise?"
        self.text = f"this line uses {_names_text(self.targets)}; {self.question} {command}"

    def line(self, note: bool = False) -> str:
        return "pygym: " + self.text + (f"  {OFF_NOTE}" if note else "")

    def ansi(self, note: bool = False) -> str:
        head = f"this line uses {_names_text(self.targets)}; {self.question} "
        return ("\x1b[2mpygym:\x1b[0m " + head + "\x1b[1m" + self.command + "\x1b[0m"
                + (f"  \x1b[2m{OFF_NOTE}\x1b[0m" if note else ""))

    def html(self, note: bool = False) -> str:
        import html
        names = " and ".join(f"<code>{html.escape(n)}()</code>" for n in self.targets) \
            if len(self.targets) < 3 else ", ".join(
                f"<code>{html.escape(n)}()</code>" for n in self.targets[:-1]) + \
            f" and <code>{html.escape(self.targets[-1])}()</code>"
        extra = (f' <span style="opacity:0.6">{html.escape(OFF_NOTE)}</span>' if note else "")
        # No colours or backgrounds of our own: it inherits the notebook theme (light or dark).
        return ('<div class="pygym-tip" style="margin:0.25em 0;line-height:1.5">'
                '<span style="opacity:0.6">pygym:</span> this line uses ' + names + "; "
                + html.escape(self.question) + ' <code style="font-weight:bold">'
                + html.escape(self.command) + "</code>" + extra + "</div>")


def _interactive(shell) -> bool:
    return (shell is not None or hasattr(sys, "ps1") or bool(sys.flags.interactive)
            or "idlelib.run" in sys.modules)


def _namespace(shell) -> Dict[str, Any]:
    if shell is not None:
        ns = getattr(shell, "user_ns", None)
        if isinstance(ns, dict):
            return ns
    main = sys.modules.get("__main__")
    return getattr(main, "__dict__", {}) if main is not None else {}


def make_tip(etype, value, tb, *, shell=None, interactive: Optional[bool] = None,
             namespace: Optional[Dict[str, Any]] = None, mode: Optional[str] = None,
             presets: Optional[Sequence[str]] = None) -> Optional[Tip]:
    found = find(etype, value, tb, mode=mode, presets=presets)
    if not found:
        return None
    if interactive is None:
        interactive = _interactive(shell)
    if namespace is None and interactive:
        namespace = _namespace(shell)
    return Tip(found, command_for(found[0][0], interactive=interactive, namespace=namespace))


# ----- showing it ----------------------------------------------------------------------------


def _write(stream, text: str) -> None:
    try:
        stream.write(text)
    except UnicodeEncodeError:    # a console that can not show a character (cp1252, cp950, ...)
        enc = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(enc, "replace").decode(enc, "replace"))
    try:
        stream.flush()
    except Exception:
        pass


def _colour(stream) -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if "idlelib.run" in sys.modules:    # IDLE's pseudo files say isatty() == True
        return False
    try:
        if not stream.isatty():
            return False
    except Exception:
        return False
    if os.name == "nt":
        try:
            import nt
            return bool(nt._supports_virtual_terminal())     # type: ignore[attr-defined]
        except Exception:
            return False
    return True


def _in_kernel(shell) -> bool:
    """A Jupyter kernel (also VS Code notebooks and Colab, whose shell subclasses it)."""
    cls = type(shell)
    return (any(c.__name__ == "ZMQInteractiveShell" for c in cls.__mro__)
            or "google.colab" in str(cls))


def show(tip: Tip, shell=None, stream=None) -> None:
    note = not _S["noted"]
    _S["noted"] = True
    if shell is not None and _in_kernel(shell):
        from IPython.display import display
        display({"text/plain": tip.line(note), "text/html": tip.html(note)}, raw=True)
        return
    if shell is not None:             # IPython in a terminal prints its tracebacks on stdout
        _write(stream or sys.stdout, tip.line(note) + "\n")
        return
    stream = stream or sys.stderr
    if stream is None:
        return
    _write(stream, (tip.ansi(note) if _colour(stream) else tip.line(note)) + "\n")


def enabled() -> bool:
    """Tips are shown in this process: PYGYM_NO_TIPS unset, and pygym.tips(...) or else the
    saved setting says on."""
    if os.environ.get(ENV_OFF):
        return False
    if _S["override"] is not None:
        return bool(_S["override"])
    from . import config
    return bool(config.load().tips)


def _after(etype, value, tb, shell=None, stream=None) -> None:
    """Called by every hook AFTER the traceback was shown. Never raises."""
    try:
        if not enabled():
            return
        last = _S["last"]
        if last is not None and last[0] is value and last[1] is tb:
            return        # two hooks saw the same error: one tip
        _S["last"] = (value, tb)
        tip = make_tip(etype, value, tb, shell=shell)
        if tip is not None:
            show(tip, shell, stream)
    except BaseException:  # noqa: BLE001  (the traceback is already on the screen)
        pass


# ----- hooks in this process -----------------------------------------------------------------


def _call_previous(previous, etype, value, tb) -> None:
    """The normal traceback. IDLE (idlelib/run.py, Executive.runcode) calls sys.excepthook only
    when it is not sys.__excepthook__; its own print_exception() hides IDLE's frames, so that one
    is used instead of the plain default when the hook below us is the default."""
    if previous is None:
        previous = sys.__excepthook__
    if previous is sys.__excepthook__:
        run = sys.modules.get("idlelib.run")
        if run is not None and sys.exc_info()[1] is value:
            run.print_exception()
            return
    previous(etype, value, tb)


def _make_sys_hook(previous):
    def pygym_excepthook(etype, value, tb):
        _call_previous(previous, etype, value, tb)
        _after(etype, value, tb)
    setattr(pygym_excepthook, _MARK, "import")
    pygym_excepthook._pygym_previous = previous   # type: ignore[attr-defined]
    return pygym_excepthook


def _from_global_hook(previous, etype, value, tb) -> None:
    """The .pth hook of `pygym --tips on` calls this when an exception reaches sys.excepthook."""
    _call_previous(previous, etype, value, tb)
    _after(etype, value, tb)


def _ipython_handler(shell, etype, value, tb, tb_offset=None):
    """IPython's custom exception handler (shell.set_custom_exc).

    IPython 9.18, IPython/core/interactiveshell.py, InteractiveShell.run_code():
        except self.custom_exceptions:
            etype, value, tb = sys.exc_info()
            if result is not None:
                result.error_in_exec = value
            self.CustomTB(etype, value, tb)
        except:
            ...
            self.showtraceback(running_compiled_code=True)
    The return value of CustomTB is ignored there, so the handler must show the traceback itself.
    It calls showtraceback() exactly like the default branch: same text, sys.last_* set (by
    _get_exc_info), the kernel's error message and _last_traceback (ipykernel's _showtraceback),
    %pdb; error_in_exec was already set by run_code. %tb and %debug read sys.last_*. Then the tip.
    Exceptions that a handler installed before us asked for still go to that handler."""
    prev = getattr(shell, "_pygym_tips_prev", None)
    if prev and prev[0] and prev[1] is not None and isinstance(value, prev[0]):
        prev[1](etype, value, tb, tb_offset=tb_offset)
    else:
        shell.showtraceback((etype, value, tb), tb_offset=tb_offset, running_compiled_code=True)
    if getattr(shell, "_pygym_tips_on", False):
        _after(etype, value, tb, shell=shell)
    return None


def _install_ipython(shell) -> None:
    if getattr(shell, "_pygym_tips_on", False):
        return
    prev = (tuple(getattr(shell, "custom_exceptions", ()) or ()), getattr(shell, "CustomTB", None))
    shell._pygym_tips_prev = prev
    exc = tuple(dict.fromkeys(prev[0] + (Exception,)))
    shell.set_custom_exc(exc, _ipython_handler)
    shell._pygym_tips_handler = shell.CustomTB
    shell._pygym_tips_on = True


def _uninstall_ipython(shell) -> None:
    if not getattr(shell, "_pygym_tips_on", False):
        return
    shell._pygym_tips_on = False
    if getattr(shell, "CustomTB", None) is getattr(shell, "_pygym_tips_handler", None):
        prev = shell._pygym_tips_prev
        shell.custom_exceptions, shell.CustomTB = prev[0], prev[1]
        shell._pygym_tips_prev = shell._pygym_tips_handler = None
    # else: someone installed a handler after us; ours stays in their chain but shows no tip


def _shell():
    if "IPython" not in sys.modules:
        return None
    try:
        from IPython import get_ipython
        return get_ipython()
    except Exception:
        return None


def install(shell=None) -> None:
    """Turn the hook on in this process (idempotent). Does not change the on/off choice."""
    shell = shell if shell is not None else _shell()
    if shell is not None:
        _install_ipython(shell)
        return
    if getattr(sys.excepthook, _MARK, None) is None:
        sys.excepthook = _make_sys_hook(sys.excepthook)


def uninstall(shell=None) -> None:
    """Remove the hook this module installed; what was there before comes back."""
    shell = shell if shell is not None else _shell()
    if shell is not None:
        _uninstall_ipython(shell)
    hook = sys.excepthook
    if getattr(hook, _MARK, None) == "import":
        sys.excepthook = hook._pygym_previous
    # the "global" hook (pygym --tips on) stays; enabled() keeps it quiet


def set_tips(on: bool = True, shell=None, quiet: bool = False) -> None:
    """pygym.tips(True) / pygym.tips(False): on or off for this Python process."""
    _S["override"] = bool(on)
    if on:
        install(shell)
    else:
        uninstall(shell)
    if not quiet:
        if on:
            print("pygym tips: on for this session.")
        else:
            print("pygym tips: off for this session (pygym --tips off turns them off for good).")


def _auto(shell=None) -> None:
    """`import pygym` and the IPython startup file: on unless the setting (or tips(False)) says off."""
    try:
        if os.environ.get(ENV_OFF) or _S["override"] is False:
            return
        if _S["override"] is None:
            from . import config
            if not config.load().tips:
                return
        install(shell)
    except Exception:
        pass


# ----- the global hook: pygym --tips on / off / status --------------------------------------

# One line in a .pth file runs at every start of this Python. It must stay tiny: it only puts a
# small excepthook in place; pygym is imported when an exception actually reaches it (and if
# pygym is gone, the previous hook runs as if nothing was there).
_PTH_HOOK = (
    "def _pygym_tips_hook(t, v, tb, _prev=sys.excepthook):\n"
    "    import os\n"
    "    if not os.environ.get('PYGYM_NO_TIPS'):\n"
    "        try:\n"
    "            from pygym.tips import _from_global_hook as hook\n"
    "        except BaseException:\n"
    "            hook = None\n"
    "        if hook is not None:\n"
    "            return hook(_prev, t, v, tb)\n"
    "    return _prev(t, v, tb)\n"
    "_pygym_tips_hook._pygym_tips = 'global'\n"
    "_pygym_tips_hook._pygym_previous = sys.excepthook\n"
    "sys.excepthook = _pygym_tips_hook\n"
)
PTH_TEXT = ("# pygym: tips at your error (pygym --tips on). Remove with: pygym --tips off\n"
            "import sys; exec(" + repr(_PTH_HOOK) + ")\n")

STARTUP_TEXT = '''\
# pygym: tips at your error in IPython and Jupyter. Added by `pygym --tips on`,
# removed by `pygym --tips off`. Does nothing when pygym is not installed in this Python.
def _pygym_tips_startup():
    try:
        import pygym.tips
        pygym.tips._auto()
    except Exception:
        pass


_pygym_tips_startup()
del _pygym_tips_startup
'''


def _writable_dir(path: str) -> bool:
    probe = path
    while probe and not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return False
        probe = parent
    return bool(probe) and os.access(probe, os.W_OK)


def site_dirs() -> List[str]:
    """Where the .pth may go, best first: PYGYM_TIPS_SITE_DIR, else this Python's site-packages,
    else the user site-packages (only when Python reads it: not in a virtual environment)."""
    override = os.environ.get(ENV_SITE_DIR)
    if override:
        return [override]
    import site
    import sysconfig
    found = [sysconfig.get_paths()["purelib"]]
    if site.ENABLE_USER_SITE:
        found.append(site.getusersitepackages())
    return found


def ipython_dir() -> str:
    """IPython's folder as IPython finds it: $IPYTHONDIR, else ~/.ipython."""
    env = os.environ.get("IPYTHONDIR")
    return os.path.expanduser(env) if env else os.path.join(os.path.expanduser("~"), ".ipython")


def startup_path() -> Optional[str]:
    """<ipython_dir>/profile_default/startup/50-pygym-tips.py, or None without IPython."""
    import importlib.util
    try:
        if importlib.util.find_spec("IPython") is None:
            return None
    except Exception:
        return None
    return os.path.join(ipython_dir(), "profile_default", "startup", STARTUP_NAME)


def global_status() -> Dict[str, Any]:
    """{"setting": bool, "pth": path or None, "startup": path or None}."""
    from . import config
    pth = next((os.path.join(d, PTH_NAME) for d in site_dirs()
                if os.path.isfile(os.path.join(d, PTH_NAME))), None)
    start = startup_path()
    return {"setting": bool(config.load().tips), "pth": pth,
            "startup": start if start and os.path.isfile(start) else None}


def _save_setting(on: bool) -> None:
    from . import config
    settings = config.load()
    settings.tips = on
    config.save(settings)


def enable_global() -> Dict[str, Any]:
    """Write the .pth and the IPython startup file, save tips=on.
    Returns {"pth": path, "startup": path or None, "notes": [...]}; raises OSError when no
    site-packages folder can be written."""
    notes: List[str] = []
    dirs = site_dirs()
    target = next((d for d in dirs if _writable_dir(d)), None)
    if target is None:
        raise OSError("no writable site-packages folder for this Python (tried: "
                      + ", ".join(dirs) + ")")
    if target != dirs[0]:
        notes.append(f"{dirs[0]} is not writable, so the user site-packages is used")
    os.makedirs(target, exist_ok=True)
    pth = os.path.join(target, PTH_NAME)
    with open(pth, "w", encoding="utf-8") as f:
        f.write(PTH_TEXT)
    start = startup_path()
    if start is not None:
        os.makedirs(os.path.dirname(start), exist_ok=True)
        with open(start, "w", encoding="utf-8") as f:
            f.write(STARTUP_TEXT)
    else:
        notes.append("IPython is not installed in this Python: no IPython/Jupyter startup file")
    _save_setting(True)
    return {"pth": pth, "startup": start, "notes": notes}


def disable_global() -> List[str]:
    """Remove the .pth (every candidate folder) and the startup file, save tips=off.
    Returns the removed paths."""
    removed: List[str] = []
    candidates = [os.path.join(d, PTH_NAME) for d in site_dirs()]
    start = startup_path() or os.path.join(ipython_dir(), "profile_default", "startup",
                                           STARTUP_NAME)
    for path in candidates + [start]:
        if os.path.isfile(path):
            os.remove(path)
            removed.append(path)
    _save_setting(False)
    return removed


# ----- pygym.tips(...) -----------------------------------------------------------------------


class _CallableModule(types.ModuleType):
    """`pygym.tips(False)` / `pygym.tips(True)`: the module itself is callable, so the function
    and the module `pygym.tips` are one object (no shadowing whichever is imported first)."""

    def __call__(self, on: bool = True) -> None:
        set_tips(on)


sys.modules[__name__].__class__ = _CallableModule
