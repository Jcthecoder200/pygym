"""The `pygym` command: a Rich main menu, plus flags for starting an exercise straight away.

    pygym                              open the menu (library -> function -> level)
    pygym zip --level 2 --mode ai      start zip at once
    pygym statistics.median            a function of another library
    pygym --mode ai                    no function given: save this as the default and exit
    pygym --list [LIBRARY]             libraries, or the functions in one (★ = hand-checked exercises)
    pygym --status                     current settings and whether AI mode can run
    pygym --tips on|off|status         tips under your tracebacks, always on / off (see pygym.tips)
    pygym --providers                  the AI providers, their keys and who receives the prompt
    pygym --set-provider NAME          choose one (ollama, openai, anthropic, gemini, ...)
    pygym --set-key NAME               save its API key (asked for, hidden; never an argument)

This module only chooses WHICH exercise to start. The rules inside an exercise are in `Session`,
the windows are in `ui`, and the AI engine is in `pygym.ai`.
"""
from __future__ import annotations

import argparse
import builtins
import contextlib
import difflib
import getpass
import importlib
import importlib.util
import io
import os
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from rich.console import Console, Group, RenderableType
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__, ai, config, loader, sandbox, style, tips, ui
from ._runner import denied_reason
from .ai import keys, registry
from .ai.providers import is_local, key_fix
from .loader import ExerciseUnavailable
# LEVELS and parse_level live in session (the :level command uses them too); kept importable here.
from .session import LEVELS, Job, Session, parse_level  # noqa: F401
from .session import BIGGER_PROMPT, MAX_LEVEL, bigger_answer, level_dots, simple_target

# Libraries shown (and numbered) on the home screen. Any importable library can still be typed.
LIBRARIES: Tuple[str, ...] = (
    "builtins", "str", "list", "dict", "set", "tuple", "math", "statistics", "itertools",
    "functools", "collections", "string", "re", "operator", "heapq", "bisect", "textwrap",
    "datetime",
)
# Built-in types whose methods are listed like a library: "str.join".
TYPE_LIBRARIES = {"str": str, "list": list, "dict": dict, "set": set, "tuple": tuple,
                  "int": int, "float": float, "bytes": bytes}
# Libraries that are never imported or offered (GUI toolkits, things that open a browser or start
# programs, pygym itself, ...) are in one list shared with the AI engine and the sandbox:
# _runner.DENIED_MODULES, checked through sandbox.refusal().
# Built-ins that make no sense as an exercise (they read input, print, run code, ...).
_NOT_EXERCISES = frozenset({
    "exit", "quit", "help", "copyright", "credits", "license", "input", "open", "exec", "eval",
    "compile", "breakpoint", "print", "globals", "locals", "vars", "dir", "setattr", "delattr",
    "id", "hash", "memoryview", "aiter", "anext", "super", "object", "type", "property",
    "staticmethod", "classmethod",
})
_PROMPT_STYLE = "bold yellow"
# AI providers that --set-provider accepts (their aliases too): the table in ai.registry.
PROVIDERS = registry.names()
OLLAMA_URL = registry.PROVIDERS["ollama"].base_url  # config.Settings().base_url
# --set-model default / --set-base-url default: back to the provider's own (saved as "").
DEFAULT_WORD = "default"


def _ai_timeout_text() -> str:
    return f"up to {config.load().ai_timeout} s"


class LibraryError(Exception):
    """A library name that can not be used (does not exist, unsafe to import, ...)."""


@dataclass
class Choice:
    target: str   # "zip", "str.join", "statistics.median"
    level: int    # 1, 2 or 3
    mode: str     # "preset" or "ai"
    ui: str       # one of config.UIS


# ----- small helpers -------------------------------------------------------------------------


def clean_target(text: str) -> str:
    """'zip()' and 'builtins.zip' both mean 'zip'."""
    t = (text or "").strip()
    if t.endswith("()"):
        t = t[:-2].strip()
    if t.startswith("builtins."):
        t = t[len("builtins."):]
    return t


def preset_levels() -> Dict[str, List[int]]:
    """The hand-written exercises: {"zip": [1, 2, 3], ...}."""
    found: Dict[str, set] = {}
    try:
        for ex in loader.load_all():
            found.setdefault(ex.target_function, set()).add(ex.difficulty)
    except Exception:  # a broken data file must not stop the menu
        return {}
    return {name: sorted(levels) for name, levels in sorted(found.items())}


def _is_exception_class(obj) -> bool:
    return isinstance(obj, type) and issubclass(obj, BaseException)


def _installed(top: str) -> bool:
    try:
        return importlib.util.find_spec(top) is not None
    except Exception:
        return False


def load_library(name: str):
    """The module (or built-in type) behind a library name. Raises LibraryError."""
    name = (name or "").strip()
    if name == "builtins":
        return builtins
    if name in TYPE_LIBRARIES:
        return TYPE_LIBRARIES[name]
    if not name or not all(part.isidentifier() for part in name.split(".")):
        raise LibraryError(f"'{name}' is not a library name.")
    what, why = sandbox.refusal(name)
    if what:
        raise LibraryError(f"'{name}' is not a library pygym can use: {why}.")
    try:
        spec = importlib.util.find_spec(name)
    except Exception:  # find_spec imports the parent package and may raise
        spec = None
    if spec is None:
        hint = sandbox.install_hint(name)
        if hint and not _installed(name.split(".")[0]):
            raise LibraryError(f"{hint} (pygym never installs anything itself.)")
        raise LibraryError(f"No library called '{name}'.")
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return importlib.import_module(name)
    except (Exception, SystemExit):
        raise LibraryError(f"The library '{name}' could not be imported.") from None


def functions_of(library: str) -> List[str]:
    """The public functions of a library, sorted. Raises LibraryError."""
    obj = load_library(library)
    if obj is builtins:
        names = {n for n in dir(builtins)
                 if not n.startswith("_") and n not in _NOT_EXERCISES
                 and callable(getattr(builtins, n)) and not _is_exception_class(getattr(builtins, n))}
        names.update(n for n in preset_levels() if "." not in n)
        return sorted(names)
    if isinstance(obj, type):
        return sorted(n for n in dir(obj) if not n.startswith("_") and callable(getattr(obj, n, None)))
    exported = getattr(obj, "__all__", None)
    names = exported if isinstance(exported, (list, tuple)) else dir(obj)
    result = []
    for n in names:
        if not isinstance(n, str) or n.startswith("_") or denied_reason(f"{library}.{n}"):
            continue                     # e.g. os.system: never an exercise
        value = getattr(obj, n, None)
        if callable(value) and not _is_exception_class(value):
            result.append(n)
    return sorted(set(result))


def _close(word: str, names: Sequence[str], n: int = 3) -> List[str]:
    return difflib.get_close_matches(word, list(names), n=n, cutoff=0.6)


def _library_suggestions(name: str, error: Optional[Exception] = None) -> List[str]:
    """Close library names, only for "No library called ..." (not after a refusal or a pip hint)."""
    if error is not None and "No library called" not in str(error):
        return []
    pool = {n for n in set(LIBRARIES) | set(getattr(sys, "stdlib_module_names", ()))
            if not n.startswith("_") and not sandbox.refusal(n, "bwrap")[0]}
    return _close(name, sorted(pool))


def check_target(text: str) -> Tuple[Optional[str], str, List[str]]:
    """Is this a function we can start? -> (target, error message, suggestions).

    target is None when it is not usable. Suggestions are full names (what to type instead)."""
    t = clean_target(text)
    if not t:
        return None, "Type a function name.", []
    if "." not in t:
        names = functions_of("builtins")
        if t in names:
            return t, "", []
        return None, f"No function '{t}' in builtins.", _close(t, names)
    library, _, func = t.rpartition(".")
    try:
        names = functions_of(library)
    except LibraryError as e:
        return None, str(e), _library_suggestions(library, e)
    if func in names:
        return t, "", []
    return None, f"No function '{func}' in {library}.", [f"{library}.{n}" for n in _close(func, names)]


def _full(library: str, func: str) -> str:
    return func if library == "builtins" else f"{library}.{func}"


# ----- screens -------------------------------------------------------------------------------


def _mode_cell(mode: str) -> Text:
    on, off = "bold", style.PALETTE["muted"]
    return Text.assemble(("● preset" if mode == "preset" else "○ preset", on if mode == "preset" else off),
                         "   ",
                         ("● AI" if mode == "ai" else "○ AI", on if mode == "ai" else off))


def _sandbox_level() -> str:
    try:
        return sandbox.level()
    except Exception:  # detection must never break the menu
        return "audit-hook"


def _sandbox_cell() -> Text:
    """'bubblewrap' (green) / 'basic (Python audit hook only)' (yellow)."""
    lv = _sandbox_level()
    good = lv != "audit-hook"
    return Text(sandbox.label(lv), style=style.PALETTE["pass"] if good else style.PALETTE["refused"])


def home_panel(settings: config.Settings, ai_ready: bool) -> RenderableType:
    """The header: current mode, AI state, level, window and the short instructions."""
    muted, brand = style.PALETTE["muted"], style.PALETTE["brand"]
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(no_wrap=True, width=7, style=muted)
    grid.add_column(ratio=1)
    grid.add_column(justify="right", no_wrap=True, style=style.PALETTE["command"])
    name, model, address = _ai_target(settings)
    if ai_ready:
        ai_cell = Text.assemble(("✓ ready", style.PALETTE["pass"]),
                                (f" · {name} · {model}", muted))
    else:
        ai_cell = Text.assemble(("✗ offline", style.PALETTE["fail"]),
                                (f" · no answer from {address or name}", muted))
    level_name = LEVELS.get(settings.level, "beginner")
    grid.add_row("Mode", _mode_cell(settings.mode), "m  switch mode")
    grid.add_row("AI", ai_cell, "")
    grid.add_row("Level", Text.assemble((style.dots(settings.level), style.PALETTE["dots"]),
                                        f" {level_name}"), "l  change level")
    grid.add_row("Window", Text(settings.ui), "u  change window")
    grid.add_row("Sandbox", _sandbox_cell(), "")
    body = Group(Text("Practice one Python function at a time."), Text(""), grid)
    panel = Panel(body, title="[bold]pygym[/]", subtitle=f"v{__version__}", border_style=brand,
                  padding=(1, 2))
    extra: List[RenderableType] = []
    if settings.mode == "ai":
        extra.append(Text("! " + ai.WARNING, style=style.PALETTE["refused"]))
        if not ai_ready:
            extra.append(Text("AI mode will use the preset exercises until the AI is ready.",
                              style=muted))
    return Group(panel, *extra) if extra else panel


def libraries_text(limit: Optional[int] = 12) -> RenderableType:
    shown = LIBRARIES if limit is None else LIBRARIES[:limit]
    parts = [Text("Libraries", style="bold"),
             Text("   type a number, a name, or a full name like statistics.median",
                  style=style.PALETTE["muted"])]
    rows = Table(show_header=False, box=None, padding=(0, 2, 0, 1), pad_edge=False)
    for _ in range(4):
        rows.add_column(no_wrap=True)
    cells = [Text.assemble((f"{i} ", style.PALETTE["command"]), name + (" ★" if name == "builtins" else ""))
             for i, name in enumerate(shown, 1)]
    for i in range(0, len(cells), 4):
        row = cells[i:i + 4]
        rows.add_row(*row, *[""] * (4 - len(row)))
    tail = Text("(? for help, any other installed library name also works)"
                if limit is not None and limit < len(LIBRARIES) else "",
                style=style.PALETTE["muted"])
    return Group(Text.assemble(*parts), rows, tail) if tail.plain else Group(Text.assemble(*parts), rows)


def functions_panel(library: str, presets: Dict[str, List[int]],
                    names: Optional[Sequence[str]] = None, limit: Optional[int] = 24) -> RenderableType:
    """The functions of a library. `presets` = {target: levels with hand-written exercises}."""
    if names is None:
        try:
            names = functions_of(library)
        except LibraryError:
            names = []
    muted = style.PALETTE["muted"]
    starred = [n for n in names if _full(library, n) in presets]
    others = [n for n in names if _full(library, n) not in presets]
    lines: List[RenderableType] = []
    if starred:
        lines.append(Text("★ hand-checked exercises (dots = levels available)", style="bold"))
        cells = []
        for n in starred:
            levels = presets[_full(library, n)]
            dots = "".join(style.DOT_FULL if lv in levels else style.DOT_EMPTY for lv in (1, 2, 3))
            cells.append(Text.assemble((n, style.PALETTE["target"]), " ", (dots, style.PALETTE["dots"])))
        lines.append(_flow(cells))
    if others:
        title = "AI only (needs AI mode and a running model)" if starred or library == "builtins" \
            else "Functions (AI mode writes the exercises)"
        lines.append(Text(title, style="bold"))
        shown = others if limit is None or len(others) <= limit else others[:limit]
        text = "  ".join(shown) + ("  …  (? shows all)" if len(shown) < len(others) else "")
        lines.append(Text(text, style=muted))
    if not lines:
        lines.append(Text(f"No public functions found in {library}.", style=muted))
    return Panel(Group(*lines), title=f"[bold]{escape(library)}[/]", border_style=style.PALETTE["exercise"],
                 padding=(0, 2))


def _flow(cells: List[Text]) -> Text:
    out = Text()
    for i, cell in enumerate(cells):
        if i:
            out.append("   ")
        out.append_text(cell)
    return out


def help_panel() -> RenderableType:
    rows = Table(show_header=False, box=None, padding=(0, 2, 0, 0), pad_edge=False)
    rows.add_column(style=style.PALETTE["command"], no_wrap=True)
    rows.add_column()
    for key, desc in [
        ("m", "switch between preset and AI mode (saved)"),
        ("l", "change the default level"),
        ("u", "change the window (auto, editor, text, tk)"),
        ("b", "go back one step (at the level prompt use: back)"),
        ("?", "show this help (and every function at the function prompt)"),
        ("q", "quit (Ctrl-C and Ctrl-D also quit)"),
        ("Enter", "use the value in [brackets]"),
    ]:
        rows.add_row(key, desc)
    body = Group(
        Text("preset: hand-written exercises. Functions without one use AI if it is ready.\n"
             "AI: a local model writes a new exercise each time. It may be wrong. "
             "If it is offline, pygym uses a preset instead.", style=style.PALETTE["muted"]),
        Text(""), rows)
    return Panel(body, title="help", border_style=style.PALETTE["help"], padding=(1, 2))


# ----- the interactive menu ----------------------------------------------------------------


class _Quit(Exception):
    """The student asked to leave (q, Ctrl-C or Ctrl-D)."""


def _resolved(settings: config.Settings) -> Optional[registry.Resolved]:
    """The provider, address and model the settings mean ("" = the provider's default), or
    None for a provider name pygym does not know."""
    try:
        return registry.resolve(settings)
    except registry.UnknownProvider:
        return None


def _ai_target(settings: config.Settings) -> Tuple[str, str, str]:
    """(provider name, model, address) for one-line displays. Never raises."""
    chosen = _resolved(settings)
    if chosen is None:
        return settings.provider, settings.model, settings.base_url
    model = chosen.model or "its first model"
    return chosen.spec.name, model, chosen.base_url


def _ai_ready() -> bool:
    try:
        return bool(ai.available())
    except Exception:
        return False


def _save(settings: config.Settings, console: Console) -> None:
    try:
        config.save(settings)
    except (OSError, ValueError) as e:
        console.print(Text(f"Could not save your settings: {e}", style=style.PALETTE["refused"]))


def _toggle_mode(settings: config.Settings, console: Console) -> None:
    settings.mode = "ai" if settings.mode == "preset" else "preset"
    _save(settings, console)
    if settings.mode == "ai":
        console.print(Text("Mode: AI.", style="bold"))  # the home panel shows the warning
        if not _ai_ready():
            console.print(Text("The AI is not reachable now, so pygym will fall back to the preset "
                               "exercises.", style=style.PALETTE["muted"]))
    else:
        console.print(Text("Mode: preset (hand-written exercises).", style=style.PALETTE["muted"]))


def _cycle_ui(settings: config.Settings, console: Console) -> None:
    uis = config.UIS
    settings.ui = uis[(uis.index(settings.ui) + 1) % len(uis)] if settings.ui in uis else "auto"
    _save(settings, console)
    console.print(Text(f"Window: {settings.ui}", style=style.PALETTE["muted"]))


def _say(console: Console, message: str, suggestions: Sequence[str] = (), kind: str = "unknown") -> None:
    console.print(Text(message, style=style.PALETTE[kind]))
    if suggestions:
        console.print(Text("Did you mean: " + ", ".join(suggestions) + "?", style=style.PALETTE["muted"]))


def run_menu(*, console: Optional[Console] = None, input_fn: Optional[Callable[[str], str]] = None,
             start_fn: Optional[Callable[[Choice, Console], int]] = None) -> int:
    """The main menu loop. Returns 0 when the student quits.

    input_fn(prompt) -> str and the console can be replaced (the tests do); start_fn starts one
    exercise (default: `start`)."""
    console = console or Console()
    start_fn = start_fn or _starter(input_fn)

    def ask(prompt: str) -> str:
        try:
            if input_fn is None:
                return console.input(f"[{_PROMPT_STYLE}]{escape(prompt)}[/]")
            return input_fn(prompt)
        except (EOFError, KeyboardInterrupt):
            raise _Quit from None

    settings = config.load()
    try:
        while True:
            console.print(home_panel(settings, _ai_ready()))
            console.print(libraries_text())
            console.print(Text("m mode · l level · u window · ? help · q quit",
                               style=style.PALETTE["muted"]))
            picked = _pick_target(console, ask, settings)
            if picked is None:
                continue
            library, target = picked
            level = _pick_level(console, ask, settings, target)
            if level is None:
                continue
            settings.level, settings.library = level, library
            _save(settings, console)
            console.print(Text(f"Starting {target} · {LEVELS[level]} · {settings.mode} …",
                               style=style.PALETTE["muted"]))
            start_fn(Choice(target, level, settings.mode, settings.ui), console)
            settings = config.load()  # the start step may have changed nothing; stay in sync anyway
    except _Quit:
        console.print(Text("\nBye!", style=style.PALETTE["muted"]))
        return 0


def _pick_target(console: Console, ask, settings: config.Settings) -> Optional[Tuple[str, str]]:
    """Library prompt, then function prompt. Returns (library, target), or None to redraw the home
    screen (after a mode / level / window change)."""
    while True:
        raw = ask(f"library [{settings.library}] › ").strip()
        key = raw.lower()
        if key in ("q", "quit", "exit"):
            raise _Quit
        if key == "?":
            console.print(help_panel())
            console.print(libraries_text(limit=None))
            continue
        if key == "m":
            _toggle_mode(settings, console)
            return None
        if key == "l":
            level = _ask_level(console, ask, settings)
            if level is not None:
                settings.level = level
                _save(settings, console)
            return None
        if key == "u":
            _cycle_ui(settings, console)
            return None
        if key == "b":
            continue
        if not raw:
            raw = settings.library
        if raw.isdigit():
            n = int(raw)
            if not 1 <= n <= len(LIBRARIES):
                _say(console, f"There is no library number {n}. Pick 1 to {len(LIBRARIES)}.")
                continue
            raw = LIBRARIES[n - 1]
        library = _enter_library(console, raw)
        if library is None:
            continue
        if isinstance(library, tuple):      # a full name was typed: the function step is skipped
            return library
        result = _pick_function(console, ask, settings, library)
        if result is not None:
            return library, result
        # None = "back": ask for the library again


def _enter_library(console: Console, raw: str):
    """A library name -> the library; a full name -> (library, target); None after a message."""
    name = clean_target(raw)
    try:
        functions_of(name)
        return name
    except LibraryError as first_error:
        # 'zip' is a function, 'statistics.median' is a full name
        target, _error, _sugg = check_target(name)
        if target is not None:
            library, _, _func = target.rpartition(".")
            return (library or "builtins", target)
        message = str(first_error)
        if "." not in name and "No library called" in message:
            message += " Type a library name such as builtins, math or statistics."
        _say(console, message, _library_suggestions(name, first_error))
        return None


def _pick_function(console: Console, ask, settings: config.Settings, library: str) -> Optional[str]:
    """Function prompt for one library. Returns the full target, or None to go back."""
    names = functions_of(library)
    presets = preset_levels()
    console.print(functions_panel(library, presets, names))
    console.print(Text("b back · m mode · ? all functions · q quit", style=style.PALETTE["muted"]))
    while True:
        raw = ask("function › ").strip()
        key = raw.lower()
        if key in ("q", "quit", "exit"):
            raise _Quit
        if key == "b":
            return None
        if key == "?":
            console.print(functions_panel(library, presets, names, limit=None))
            continue
        if key == "m":
            _toggle_mode(settings, console)
            continue
        if not raw:
            console.print(Text("Type the name of a function (? lists them).",
                               style=style.PALETTE["muted"]))
            continue
        name = clean_target(raw)
        prefix = library + "."
        if library != "builtins" and name.startswith(prefix):
            name = name[len(prefix):]
        if name in names:
            return _full(library, name)
        _say(console, f"No function '{name}' in {library}.", _close(name, names))


def _ask_level(console: Console, ask, settings: config.Settings, target: str = "") -> Optional[int]:
    """Level prompt. Returns 1/2/3, or None for 'back'."""
    console.print(Text("1 beginner   2 intermediate   3 advanced", style=style.PALETTE["dots"]))
    while True:
        raw = ask(f"level [{LEVELS[settings.level]}] › ").strip()
        key = raw.lower()
        if key in ("q", "quit", "exit"):
            raise _Quit
        if key in ("back", "0"):
            return None
        if key == "m":
            _toggle_mode(settings, console)
            continue
        if key == "?":
            console.print(help_panel())
            continue
        if not raw:
            return settings.level
        level = parse_level(raw)
        if level is not None:
            return level
        _say(console, f"'{raw}' is not a level. Type 1, 2 or 3 (or b, i, a).")


def _pick_level(console: Console, ask, settings: config.Settings, target: str) -> Optional[int]:
    levels = preset_levels().get(target)
    if levels:
        info = Text.assemble((target, style.PALETTE["target"]), " · ★ preset levels "
                             + " ".join(str(lv) for lv in levels))
    else:
        info = Text.assemble((target, style.PALETTE["target"]), " · no hand-checked exercise "
                             "(AI mode or a running model is needed)", )
    console.print(info)
    return _ask_level(console, ask, settings, target)


# ----- starting one exercise -------------------------------------------------------------------


def _starter(input_fn: Optional[Callable[[str], str]]) -> Callable[[Choice, Console], int]:
    """`start` with the menu's input function (for the bigger-task question)."""
    if input_fn is None:
        return start
    return lambda choice, console: start(choice, console, input_fn=input_fn)


def asks_bigger(choice: Choice) -> bool:
    """True if starting this choice asks the bigger-task question first: advanced, a function
    too small for it on its own (bin, abs, str.upper) and the AI writes its exercises (AI mode,
    or no hand-written exercise). The same rule as Session.bigger_applies."""
    if choice.level != MAX_LEVEL or ai.engine._turned_off():
        return False
    may_use_ai = choice.mode == "ai" or choice.target not in preset_levels()
    return may_use_ai and simple_target(choice.target)


def _ask_bigger(choice: Choice, console: Console,
                input_fn: Optional[Callable[[str], str]]) -> Optional[bool]:
    """Ask the bigger-task question. True / False, or None when not answered (EOF, Ctrl-C or
    another word: back to the menu)."""
    console.print(Text(f"`{choice.target}` is too small for an advanced exercise on its own.",
                       style=style.PALETTE["level"]))
    prompt = BIGGER_PROMPT.format(name=choice.target)
    try:
        raw = input_fn(prompt) if input_fn is not None else console.input(
            f"[{_PROMPT_STYLE}]{escape(prompt)}[/]")
    except (EOFError, KeyboardInterrupt):
        raw = None
    command = bigger_answer(raw)
    return None if command is None else command.endswith("yes")


def start(choice: Choice, console: Console, *,
          input_fn: Optional[Callable[[str], str]] = None) -> int:
    """Open the exercise window. Returns 0 when it ran and 1 when no exercise was available.

    Advanced with a function too small for it (asks_bigger): asks first whether to practise it
    inside a bigger task (input_fn, default: the console); no -> intermediate."""
    settings = config.load()
    may_use_ai = choice.mode == "ai" or choice.target not in preset_levels()
    bigger: Optional[bool] = None
    if asks_bigger(choice):
        bigger = _ask_bigger(choice, console, input_fn)
        if bigger is None:
            console.print(Text("Not answered: back to the menu.", style=style.PALETTE["muted"]))
            return 1
        if not bigger:
            console.print(Text(f"Staying at intermediate {level_dots(MAX_LEVEL - 1)}.",
                               style=style.PALETTE["level"]))
    extra = {} if bigger is None else {"bigger": bigger}  # only when the question was asked
    try:
        if may_use_ai and _ai_ready():
            # The same progress bar as :s in the line CLI (the model may take up to ai_timeout s).
            console.print(Text(f"Writing a new exercise with {_ai_target(settings)[1]} … "
                               f"({_ai_timeout_text()})", style=style.PALETTE["muted"]))
            job = Job(lambda report: Session(choice.target, choice.level, choice.mode,
                                             on_progress=report, **extra))
            if not style.wait_with_bar(job, console):
                console.print(Text("Cancelled.", style=style.PALETTE["muted"]))
                return 1
            session = job.result()
        else:
            session = Session(choice.target, choice.level, choice.mode, **extra)
    except ExerciseUnavailable as e:
        _say(console, f"✗ {e}", kind="fail")
        if "AI could not" in str(e):
            tip = "Pick a function with ★, or try again (the AI writes a new exercise each time)."
        elif choice.mode == "preset":
            tip = "Tip: switch to AI mode (m in the menu, or --mode ai), or pick a function with ★."
        else:
            tip = "AI mode could not write one and there is no preset. Pick a function with ★."
        console.print(Text(tip, style=style.PALETTE["muted"]))
        return 1
    # Session.notice (AI warning / fallback note) is shown by the front-end itself (ui.run).
    try:
        ui.run(session, choice.ui)
    except KeyboardInterrupt:
        console.print(Text("Bye!", style=style.PALETTE["muted"]))
    return 0


# ----- the command line ------------------------------------------------------------------------


def _level_arg(text: str) -> int:
    level = parse_level(text)
    if level is None:
        raise argparse.ArgumentTypeError(
            f"'{text}' is not a level (use 1, 2, 3 or beginner, intermediate, advanced)")
    return level


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pygym", description="Practice one Python function at a time. "
        "Run without arguments to open the menu.",
        epilog="With no function, --mode / --level / --ui are saved as your defaults.")
    p.add_argument("target", nargs="?", metavar="FUNCTION",
                   help="start this function at once, e.g. zip, str.join, statistics.median")
    p.add_argument("-l", "--level", type=_level_arg, metavar="LEVEL",
                   help="1/2/3 or beginner/intermediate/advanced")
    p.add_argument("--mode", choices=config.MODES, help="preset or ai (this run only when a function is given)")
    p.add_argument("--ui", choices=config.UIS, help="window to use: " + ", ".join(config.UIS))
    p.add_argument("--list", nargs="?", const="", default=None, metavar="LIBRARY",
                   help="list the libraries, or the functions in LIBRARY (★ = has presets)")
    p.add_argument("--status", action="store_true", help="show the settings and whether AI mode is ready")
    p.add_argument("--tips", choices=("on", "off", "status"),
                   help="tips under your tracebacks: on = always (no import needed), off, status")
    p.add_argument("--set-mode", choices=config.MODES, metavar="MODE", help="save the default mode")
    p.add_argument("--set-model", metavar="NAME",
                   help="save the model used by AI mode ('default' = the provider's own)")
    p.add_argument("--set-provider", metavar="NAME",
                   help="save the AI provider: " + ", ".join(PROVIDERS) + " (pygym --providers "
                   "describes them; the address and model go back to the provider's defaults)")
    p.add_argument("--set-base-url", metavar="URL",
                   help="save the AI server's address, e.g. http://localhost:8080/v1 "
                   "('default' = the provider's own)")
    p.add_argument("--set-key", metavar="PROVIDER",
                   help="save an API key for PROVIDER in PYGYM_HOME/keys.json (only you can read "
                   "it). pygym asks for the key without showing it; never type it on the command "
                   "line")
    p.add_argument("--forget-key", metavar="PROVIDER", help="delete the saved key of PROVIDER")
    p.add_argument("--providers", action="store_true",
                   help="list the AI providers: keys, default models, who receives the prompt")
    p.add_argument("--version", action="version", version=f"pygym {__version__}")
    return p


def _tolerate_any_encoding() -> None:
    """Never crash on a character the output encoding cannot show.

    A Windows console shows everything (UTF-8 console API), but redirected output (a pipe, `> file`,
    an IDE output window) uses the ANSI code page: cp1252, or cp950 on Traditional Chinese Windows.
    There the first screen died with UnicodeEncodeError on "●" and "✗". Unshowable characters are
    now replaced by "?" instead.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: Optional[Sequence[str]] = None, *, console: Optional[Console] = None,
         input_fn: Optional[Callable[[str], str]] = None,
         start_fn: Optional[Callable[[Choice, Console], int]] = None) -> int:
    """Exit code: 0 done, 1 unknown function or no exercise, 2 bad arguments."""
    parser = build_parser()
    try:
        args = parser.parse_args(None if argv is None else list(argv))
    except SystemExit as e:  # argparse already printed the message (or --help / --version)
        code = e.code
        return code if isinstance(code, int) else (0 if code is None else 2)
    if console is None:
        _tolerate_any_encoding()
        console = Console()
    start_fn = start_fn or _starter(input_fn)
    settings = config.load()
    muted = style.PALETTE["muted"]

    fail = style.PALETTE["fail"]
    # Key flags first, before anything is saved. Their value is never printed: someone who typed
    # the key itself (--set-key sk-...) must not see it again on the screen or in a log.
    key_specs: Dict[str, registry.ProviderSpec] = {}
    for flag, value in (("--set-key", args.set_key), ("--forget-key", args.forget_key)):
        if value is None:
            continue
        if args.target:
            console.print(Text(f"{flag} takes only a provider name. Never put an API key on the "
                               "command line (it stays in your shell history); pygym asks for it.",
                               style=fail))
            return 2
        try:
            key_specs[flag] = registry.get_spec(value)
        except registry.UnknownProvider:
            console.print(Text(f"{flag} needs a provider name, one of: {', '.join(PROVIDERS)}. "
                               "(pygym asks for the key itself; never type it on the command "
                               "line.)", style=fail))
            return 2
    if "--set-key" in key_specs and not key_specs["--set-key"].accepts_key:
        console.print(Text(f"{key_specs['--set-key'].title} takes no API key in pygym.",
                           style=fail))
        return 2

    changed = False
    if args.set_mode:
        settings.mode = args.set_mode
        changed = True
    if args.set_model:
        if not args.set_model.strip():
            console.print(Text("The model name can not be empty.", style=fail))
            return 2
        model = args.set_model.strip()
        settings.model = "" if model.lower() == DEFAULT_WORD else model
        changed = True
    if args.set_base_url is not None:
        url = args.set_base_url.strip().rstrip("/")
        if url.lower() == DEFAULT_WORD:
            url = ""
        elif not url.lower().startswith(("http://", "https://")):
            console.print(Text("The address must start with http:// or https://, e.g. "
                               "http://localhost:8080/v1", style=fail))
            return 2
        settings.base_url = url
        changed = True
    if args.set_provider:
        try:
            spec = registry.get_spec(args.set_provider)
        except registry.UnknownProvider as e:
            why = str(e)
            console.print(Text(f"{why[:1].upper()}{why[1:]}. pygym --providers describes them.",
                               style=fail))
            return 2
        before = _resolved(settings)
        settings.provider = spec.name
        changed = True          # also when it is the same: show it and what is missing, no menu
        # Another provider: its own address and model, never the old ones ("" = its default).
        # The same provider under a new name (old "openai" + a custom address read as
        # "openai-compatible") keeps them.
        if before is None or before.spec.name != spec.name:
            if args.set_base_url is None and settings.base_url:
                settings.base_url = ""
                changed = True
            if args.set_model is None and settings.model:
                settings.model = ""
                changed = True
    if changed:
        _save(settings, console)
        console.print(Text(_saved_line(settings), style=muted))

    if args.set_key is not None:
        rc = _set_key_command(console, key_specs["--set-key"], settings)
        if rc:
            return rc
    if args.forget_key is not None:
        _forget_key_command(console, key_specs["--forget-key"])
    if changed or args.set_key is not None:
        for hint in _provider_hints(settings, just_set=args.set_provider):
            console.print(Text(hint, style=style.PALETTE["refused"]))
    keys_done = args.set_key is not None or args.forget_key is not None

    if args.providers:
        _print_providers(console, settings)
        return 0
    if args.tips:
        return _tips_command(console, args.tips)
    if args.list is not None:
        return _print_list(console, args.list)
    if args.status:
        _print_status(console, settings)
        return 0

    if args.target:
        target, error, suggestions = check_target(args.target)
        if target is None:
            _say(console, error, suggestions)
            return 1
        choice = Choice(target, args.level or settings.level, args.mode or settings.mode,
                        args.ui or settings.ui)
        return start_fn(choice, console)

    if args.mode or args.level or args.ui:
        if args.mode:
            settings.mode = args.mode
        if args.level:
            settings.level = args.level
        if args.ui:
            settings.ui = args.ui
        _save(settings, console)
        console.print(Text(f"Saved: mode={settings.mode}, level={LEVELS[settings.level]}, "
                           f"window={settings.ui}", style=muted))
        return 0
    if changed or keys_done:
        return 0
    return run_menu(console=console, input_fn=input_fn, start_fn=start_fn)


def _print_list(console: Console, library: str) -> int:
    if not library:
        console.print(libraries_text(limit=None))
        return 0
    name = clean_target(library)
    try:
        names = functions_of(name)
    except LibraryError as e:
        _say(console, str(e), _library_suggestions(name, e))
        return 1
    console.print(functions_panel(name, preset_levels(), names, limit=None))
    return 0


def _model_text(chosen: registry.Resolved) -> str:
    spec = chosen.spec
    if not chosen.model:
        return ("not set (pygym --set-model NAME)" if spec.model_required
                else "the first model the server lists")
    return f"{chosen.model} (default)" if chosen.model == spec.model else chosen.model


def _address_text(chosen: registry.Resolved) -> str:
    spec = chosen.spec
    if spec.kind == "claude-code":
        return "none (the `claude` command on this computer)"
    if not chosen.base_url:
        return "not set (pygym --set-base-url URL)"
    return f"{chosen.base_url} (default)" if chosen.base_url == spec.base_url else chosen.base_url


def _receiver(spec: registry.ProviderSpec, base_url: str) -> str:
    """Who receives the prompt (the function's name and documentation; never your code)."""
    if spec.company:
        return spec.company
    if spec.kind == "claude-code":
        return "Anthropic"
    if base_url and is_local(base_url):
        return "nobody else: it stays on this computer"
    if base_url:
        host = base_url.split("://", 1)[-1].split("/", 1)[0]
        return f"the server at {host}"
    return "the server you set (pygym --set-base-url URL)"


def _saved_line(settings: config.Settings) -> str:
    chosen = _resolved(settings)
    if chosen is None:
        return (f"Saved: mode={settings.mode}, provider={settings.provider} (unknown), "
                f"model={settings.model}, address={settings.base_url}")
    return (f"Saved: mode={settings.mode}, provider={settings.provider}, "
            f"model={_model_text(chosen)}, address={_address_text(chosen)}")


def _provider_hints(settings: config.Settings, just_set: Optional[str] = None) -> List[str]:
    """What is still missing for the saved provider (empty when nothing obvious is): no
    address, no model, a key that keys.get_key cannot find. Never the key itself."""
    chosen = _resolved(settings)
    if chosen is None:
        return [f"pygym does not know the AI provider '{settings.provider}': "
                "pygym --set-provider NAME (pygym --providers lists them)"]
    spec, hints = chosen.spec, []
    if chosen.note:
        hints.append(f"Note: {chosen.note}.")
    if spec.kind != "claude-code":
        if not chosen.base_url:
            hints.append("Set the server address too: pygym --set-base-url URL "
                         "(with /v1, e.g. http://localhost:8080/v1)")
        elif spec.name == "openai-compatible" and "/v1" not in chosen.base_url:
            hints.append("Most OpenAI-compatible addresses end in /v1. If AI mode is not ready, "
                         "check the address.")
    if spec.model_required and not chosen.model:
        hints.append("Choose the model too: pygym --set-model NAME (the name the server uses)")
    key, _source = keys.get_key(spec.name) if spec.accepts_key else (None, "")
    if spec.key_required and not key:
        hints.append(f"No API key yet for {spec.title}: {key_fix(spec)}.")
    elif (spec.accepts_key and not key and chosen.base_url
          and not is_local(chosen.base_url)):
        hints.append(f"No API key yet: {key_fix(spec)} (a server on another computer usually "
                     "needs one).")
    if just_set and spec.name == "openai" and chosen.base_url == spec.base_url:
        hints.append(f"This is OpenAI's own API ({spec.base_url}). For another OpenAI-compatible "
                     "server: pygym --set-provider openai-compatible --set-base-url URL "
                     "--set-model NAME")
    if spec.kind == "claude-code" and just_set:
        hints.append(f"Needs Claude Code installed and logged in ({spec.start_hint}); "
                     f"{spec.note}.")
    return hints


def _read_secret(prompt: str) -> str:
    """One line with the key: hidden (getpass) at a terminal, else one line from stdin (a pipe:
    `printf '%s\\n' "$KEY" | pygym --set-key openai`)."""
    stdin = sys.stdin
    if stdin is not None and stdin.isatty():
        return getpass.getpass(prompt)
    return stdin.readline() if stdin is not None else ""


def _set_key_command(console: Console, spec: registry.ProviderSpec,
                     settings: config.Settings) -> int:
    """pygym --set-key NAME. 0 saved, 1 cancelled or not written, 2 an empty or bad key."""
    muted, fail = style.PALETTE["muted"], style.PALETTE["fail"]
    try:
        secret = _read_secret(f"API key for {spec.title} (hidden; Enter when done): ")
    except (EOFError, KeyboardInterrupt):
        console.print(Text("Cancelled: no key saved.", style=muted))
        return 1
    if not (secret or "").strip():
        console.print(Text("No key given: nothing saved.", style=fail))
        return 2
    try:
        keys.save_key(spec.name, secret)
    except ValueError as e:      # the messages never hold the key
        console.print(Text(f"Not saved: {e}.", style=fail))
        return 2
    except OSError as e:
        console.print(Text(f"Could not save the key: {e.strerror or type(e).__name__}.",
                           style=fail))
        return 1
    where = ("only you can read it" if os.name == "posix" else "in your user folder")
    console.print(Text(f"Key saved for {spec.title} in {keys.path()} ({where}).", style="bold"))
    _key, source = keys.get_key(spec.name)
    if source != "saved":
        console.print(Text(f"Note: {source.replace('env ', '')} is set in your environment and is "
                           "used first.", style=muted))
    chosen = _resolved(settings)
    if chosen is None or chosen.spec.name != spec.name:
        console.print(Text(f"To use it: pygym --set-provider {spec.name}", style=muted))
    return 0


def _forget_key_command(console: Console, spec: registry.ProviderSpec) -> None:
    muted = style.PALETTE["muted"]
    try:
        removed = keys.remove_key(spec.name)
    except OSError as e:
        console.print(Text(f"Could not change {keys.path()}: {e.strerror or type(e).__name__}.",
                           style=style.PALETTE["fail"]))
        return
    console.print(Text(f"Saved key for {spec.title} deleted." if removed
                       else f"No saved key for {spec.title}.", style="bold" if removed else muted))
    _key, source = keys.get_key(spec.name) if spec.accepts_key else (None, "")
    if source:
        console.print(Text(f"{spec.title} still gets a key from {source.replace('env ', '')}.",
                           style=muted))


def _print_providers(console: Console, settings: config.Settings) -> None:
    """pygym --providers: every provider pygym knows. No network calls."""
    chosen = _resolved(settings)
    current = chosen.spec.name if chosen else ""
    table = Table(title="AI providers (pygym --set-provider NAME)", title_justify="left",
                  header_style="bold", pad_edge=False, show_lines=False)
    table.add_column("name", style=style.PALETTE["command"], no_wrap=True)
    table.add_column("what it is")
    table.add_column("where")
    table.add_column("API key")
    table.add_column("default model", overflow="fold")
    table.add_column("receives the prompt")
    for spec in registry.PROVIDERS.values():
        if spec.name == "openai-compatible":
            where = "you choose"
        elif spec.local and spec.company:      # the Hermes servers pass the prompt on
            where = "this computer, then cloud"
        elif spec.kind == "claude-code":
            where = "cloud (claude command)"
        else:
            where = "this computer" if spec.local else "cloud"
        try:
            key_state = keys.describe(spec.name)
        except Exception:  # noqa: BLE001  (a strange keys.json must not break the list)
            key_state = "unknown"
        if spec.accepts_key and key_state.startswith("no key"):
            key_state += f"\n{' or '.join(spec.key_env) or keys.FALLBACK_ENV}"
        model = spec.model or ("you choose" if spec.model_required else "the first it lists")
        name = Text(spec.name + (" *" if spec.name == current else ""))
        receiver = _receiver(spec, spec.base_url) if spec.name != "openai-compatible" \
            else "the server you set"
        table.add_row(name, spec.description, where, key_state, model, receiver)
    console.print(table)
    muted = style.PALETTE["muted"]
    console.print(Text("* = your provider now. Only the prompt (the function's name and "
                       "documentation) is sent, never your code.", style=muted))
    console.print(Text("Other names: " + ", ".join(f"{a} = {n}" for a, n in registry.ALIASES.items()),
                       style=muted))
    console.print(Text("Next: pygym --set-provider NAME, then pygym --set-key NAME (if it needs "
                       "a key), then pygym --status.", style=muted))


def _print_status(console: Console, settings: config.Settings) -> None:
    ready, reason = _ai_ready(), ""
    if ready:
        # The fast check counts a cloud API as ready once a key exists. --status is where people
        # check their key, so ask the server for real (a wrong key or model shows up here).
        try:
            live = ai.live_check(settings)
        except Exception:  # noqa: BLE001  (--status must never crash)
            live = None
        if live is not None and not live.ok:
            ready, reason = False, live.message
    if not ready and not reason:
        try:  # why not: no server, wrong model, a refused API key... (cached, never raises)
            state = ai.status(settings)
            reason = "" if state.ok else state.message
        except Exception:  # noqa: BLE001  (--status must never crash)
            reason = ""
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0), pad_edge=False)
    table.add_column(style=style.PALETTE["muted"])
    table.add_column()
    table.add_row("mode", settings.mode)
    table.add_row("level", f"{settings.level} ({LEVELS[settings.level]})")
    table.add_row("window", settings.ui)
    chosen = _resolved(settings)
    if chosen is None:
        table.add_row("provider", Text(f"{settings.provider}: unknown (pygym --set-provider NAME; "
                                       "pygym --providers lists them)", style=style.PALETTE["fail"]))
    else:
        spec = chosen.spec
        table.add_row("provider", f"{spec.title} ({spec.name})")
        table.add_row("model", _model_text(chosen))
        table.add_row("address", _address_text(chosen))
        if chosen.note:
            table.add_row("note", chosen.note)
        key_state = keys.describe(spec.name)
        if spec.key_required and key_state == "no key":
            key_state += f": {key_fix(spec)}"
        table.add_row("API key", key_state)
        table.add_row("prompt goes to", _receiver(spec, chosen.base_url)
                      + " (only the function's name and documentation, never your code)")
    table.add_row("prefetch", "on (the next AI exercise is written while you work)"
                  if settings.prefetch else "off")
    table.add_row("AI time", f"up to {settings.ai_timeout} s per exercise")
    table.add_row("AI", Text("ready", style=style.PALETTE["pass"]) if ready
                  else Text("not ready (AI mode falls back to preset)"
                            + (f": {reason}" if reason else ""), style=style.PALETTE["fail"]))
    table.add_row("sandbox", _sandbox_cell())
    for name, value in _tips_rows():
        table.add_row(name, value)
    console.print(table)


def _tips_rows() -> List[Tuple[str, str]]:
    """The tips lines of --status and --tips status."""
    try:
        state = tips.global_status()
    except Exception as e:  # noqa: BLE001  (a strange site setup must not break --status)
        return [("tips", f"unknown ({type(e).__name__}: {e})")]
    rows = [("tips", "on (import pygym shows a tip under your tracebacks)" if state["setting"]
             else "off (saved; pygym --tips on turns them on)")]
    if state["pth"]:
        rows.append(("tips hook", f"global, without import: {state['pth']}"))
    else:
        rows.append(("tips hook", "not global (only after import pygym; pygym --tips on: always)"))
    if state["startup"]:
        rows.append(("tips IPython", f"startup file {state['startup']}"))
    return rows


def _tips_command(console: Console, what: str) -> int:
    """pygym --tips on / off / status. 0 done, 1 the files could not be written."""
    muted = style.PALETTE["muted"]
    if what == "on":
        try:
            done = tips.enable_global()
        except (OSError, ValueError) as e:
            _say(console, f"Could not turn the tips on everywhere: {e}", kind="fail")
            return 1
        console.print(Text("Tips at your error: on, also without `import pygym`.", style="bold"))
        console.print(Text(f"global hook: {done['pth']}", style=muted))
        if done["startup"]:
            console.print(Text(f"IPython / Jupyter: {done['startup']}", style=muted))
        for note in done["notes"]:
            console.print(Text(note, style=muted))
        console.print(Text("Turn off with: pygym --tips off", style=muted))
        return 0
    if what == "off":
        try:
            removed = tips.disable_global()
        except (OSError, ValueError) as e:
            _say(console, f"Could not turn the tips off: {e}", kind="fail")
            return 1
        console.print(Text("Tips at your error: off (saved; import pygym no longer turns them on).",
                           style="bold"))
        for path in removed:
            console.print(Text(f"removed {path}", style=muted))
        return 0
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0), pad_edge=False)
    table.add_column(style=muted)
    table.add_column()
    for name, value in _tips_rows():
        table.add_row(name, value)
    console.print(table)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
