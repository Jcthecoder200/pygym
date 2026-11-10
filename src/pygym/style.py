"""How pygym looks: the colours and the Rich building blocks shared by the front-ends.

The line CLI (ui.run_text) and the Jupyter magic (jupyter.py) print the renderables built here,
so both look the same. The terminal editor (editor.py) draws its own screen but takes its colours
from PALETTE. A tkinter front-end can mirror the same colours (see docs/FRONTEND_GUIDE.md).
"""
from __future__ import annotations

import io
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import __version__
from .session import COMMANDS

# Every colour pygym uses, by meaning. Values are Rich style strings.
PALETTE = {
    # intro / exercise panels
    "brand": "cyan",                # intro border
    "command": "bold cyan",         # command names in the intro table
    "exercise": "magenta",          # exercise panel border
    "target": "bold magenta",       # the built-in's name ("zip")
    "dots": "yellow",               # difficulty dots ●●○
    "muted": "dim",                 # explanations, level, ids, "Skipped", "Bye!"
    "ai_badge": "bold black on yellow",  # " AI " next to the level of a generated exercise
    "notice": "yellow",             # Session.notice (AI warning or fallback note)
    # responses
    "pass": "bold green",
    "fail": "bold red",
    "refused": "yellow",
    "unknown": "red",
    "hint": "yellow",               # hint panel border (CLI / Jupyter)
    "hint_line": "cyan",            # one-line hint in the editor's message line
    "help": "blue",                 # documentation panel border / pager title
    "solution": "green",            # solution pager title
    "pager": "cyan",                # other pager titles
    "prompt": "bold yellow",        # "Reveal the solution? [y/N]" in the editor
    # editor
    "gutter": "grey50",             # line numbers
    "gutter_current": "bold yellow",  # the cursor line's number
    "filler": "blue",               # "~" below the end of the buffer
    "mode_normal": "bold black on blue",
    "mode_insert": "bold black on green",
    "mode_command": "bold black on yellow",
    "status_bar": "white on grey23",
    "status_pos": "black on grey58",
    "insert_label": "bold",         # "-- INSERT --"
}
CODE_THEME = "monokai"
DOT_FULL, DOT_EMPTY, MAX_LEVEL = "●", "○", 3
# Shown before a one-line response (CLI, Jupyter and the editor's message line).
ICONS = {"pass": "✓ ", "fail": "✗ ", "refused": "! ", "notice": "! "}
# The style of a one-line response by Response.kind ("" = a message from the front-end itself;
# "notice" = Session.notice, shown by the front-ends, never a Response kind).
LINE_STYLES = {"pass": PALETTE["pass"], "fail": PALETTE["fail"], "refused": PALETTE["refused"],
               "unknown": PALETTE["unknown"], "exercise": PALETTE["muted"],
               "quit": PALETTE["muted"], "notice": PALETTE["notice"], "": PALETTE["muted"]}

CLI_HOW_TO = ("Type your function line by line. A line that is exactly a command runs it; "
              "every other line becomes part of your code. After a :w, the next lines you "
              "type start a fresh attempt (:w again with no new lines re-checks the same code).")


def dots(difficulty: int) -> str:
    """●○○ for level 1, ●●○ for level 2, ●●● for level 3."""
    n = max(0, min(MAX_LEVEL, int(difficulty or 0)))
    return DOT_FULL * n + DOT_EMPTY * (MAX_LEVEL - n)


def code(source: str) -> Syntax:
    """Python code, highlighted, on the terminal's own background."""
    return Syntax(source, "python", theme=CODE_THEME, background_color="default")


AI_BADGE = " AI "  # marks a generated (unreviewed) exercise, next to the level
NOTICE_ICON = ICONS["notice"]


def is_generated(ex) -> bool:
    return getattr(ex, "source", "preset") == "ai"


def title_line(ex) -> Text:
    """zip  ●○○  level 1   (and an " AI " badge after it for a generated exercise)"""
    line = Text.assemble((ex.target_function, PALETTE["target"]), "  ",
                         (dots(ex.difficulty), PALETTE["dots"]),
                         (f"  level {ex.difficulty}", PALETTE["muted"]))
    if is_generated(ex):
        line.append("  ")
        line.append(AI_BADGE, style=PALETTE["ai_badge"])
    return line


def notice(text: str) -> Text:
    """Session.notice as one coloured line: "! AI-generated exercise: it may be incorrect. ..." """
    return line(text, "notice")


def exercise_view(session) -> RenderableType:
    """The exercise panel, with Session.notice below it when there is one (line CLI, Jupyter)."""
    header = exercise_header(session.exercise)
    if getattr(session, "notice", ""):
        return Group(header, notice(session.notice))
    return header


def commands_table() -> Table:
    table = Table(show_header=False, box=None, padding=(0, 2, 0, 0), pad_edge=False)
    table.add_column(style=PALETTE["command"], no_wrap=True)
    table.add_column()
    for name, desc in COMMANDS:
        table.add_row(name, desc)
    return table


def intro(how_to: Optional[Sequence[RenderableType]] = None) -> Panel:
    """The welcome panel. `how_to` replaces the line-CLI explanation (Jupyter passes its own)."""
    if how_to is None:
        how_to = [Text(CLI_HOW_TO, style=PALETTE["muted"])]
    body = Group(Text("Learn a Python built-in without leaving your code."), Text(""),
                 *how_to, Text(""), commands_table())
    return Panel(body, title="[bold]pygym[/]", subtitle=f"v{__version__}",
                 border_style=PALETTE["brand"], padding=(1, 2))


def exercise_header(ex) -> Panel:
    body = Group(title_line(ex), Text(""), Text(ex.question_text), Text(""),
                 Text("Your function should look like:", style=PALETTE["muted"]),
                 code(ex.function_signature))
    return Panel(body, title=Text(ex.id, style=PALETTE["muted"]),
                 border_style=PALETTE["exercise"], padding=(1, 2))


def solution(source: str) -> Group:
    """'Solution:' and the code below it (no panel, so it can be copied as it is)."""
    return Group(Text("Solution:"), code(source))


def line(text: str, kind: str = "") -> Text:
    """A one-line message with its icon and colour (✓ Passed! ..., ✗ Failed: ...)."""
    return Text(ICONS.get(kind, "") + text, style=LINE_STYLES.get(kind, ""))


def response(resp, session) -> RenderableType:
    """How the line CLI and Jupyter show a Response."""
    kind = resp.kind
    if kind == "hint":
        return Panel(Text(resp.text), title="hint", border_style=PALETTE["hint"])
    if kind == "help":
        target = session.exercise.target_function
        return Panel(Text(resp.text), title=f"Documentation: {target}",
                     border_style=PALETTE["help"])
    if kind == "solution":
        return solution(resp.text)
    if kind == "commands":
        return Panel(commands_table(), title="commands", border_style=PALETTE["brand"],
                     padding=(0, 1))
    return line(resp.text, kind)


def to_plain(renderable: RenderableType, width: int = 100) -> str:
    """Render to plain text: no colours, no trailing spaces (for tests and custom print functions)."""
    console = Console(file=io.StringIO(), width=width, color_system=None, force_terminal=False,
                      force_jupyter=False, no_color=True, highlight=False, markup=False,
                      emoji=False, legacy_windows=False)
    with console.capture() as captured:
        console.print(renderable)
    return "\n".join(ln.rstrip() for ln in captured.get().rstrip("\n").split("\n"))


# ----- syntax highlighting for the editor ------------------------------------------------------

@lru_cache(maxsize=1)
def _highlighter() -> Syntax:
    return code("")


@lru_cache(maxsize=16)
def _highlight_cached(source: str) -> Tuple[Text, ...]:
    plain = source.split("\n")
    try:
        lines = list(_highlighter().highlight(source).split("\n", allow_blank=True))
    except Exception:  # a lexer problem must never break the screen
        lines = []
    if len(lines) < len(plain) or any(a.plain != b for a, b in zip(lines, plain)):
        return tuple(Text(p) for p in plain)
    return tuple(lines[:len(plain)])


def highlight_lines(source: str) -> List[Text]:
    """The source split into lines, each a highlighted Text (copies: safe to change)."""
    return [t.copy() for t in _highlight_cached(source)]
