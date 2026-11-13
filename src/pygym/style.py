"""How pygym looks: the colours and the Rich building blocks shared by the front-ends.

The line CLI (ui.run_text) and the Jupyter magic (jupyter.py) print the renderables built here,
so both look the same. The terminal editor (editor.py) draws its own screen but takes its colours
from PALETTE. A tkinter front-end can mirror the same colours (see docs/FRONTEND_GUIDE.md).
"""
from __future__ import annotations

import io
import textwrap
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

from rich import box
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.measure import Measurement
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from . import __version__
from .models import TIMED_OUT, short_repr
from .session import COMMANDS, LEVEL_UP_AFTER, LEVELS

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
    # test results (:w, :out): the ✓ / ✗ use "pass" / "fail" above
    "printed": "dim",               # print() output under a test:  │ hello
    "error_text": "red",            # "raised ZeroDivisionError: ..." in a results row
    "results_bar": "black on grey58",        # the editor's results window title bar
    "results_bar_focus": "bold black on cyan",  # ... while it has the focus (Ctrl-W w)
    "results_pass_block": "bold black on green",  # " 5/5 passed " in that bar (check passed)
    "results_fail_block": "bold white on red",    # " 3/5 passed " (check failed)
    # level and progress
    "level": "yellow",              # :level answers, "beginner ●○○ 1/3" under a passed check
    "status_level": "yellow on grey23",  # "●○○ 2/3" in the editor's status line
    "progress_spinner": "cyan",     # ⠋ while the next exercise is being written
    "progress_bar": "cyan",         # the filled part of the bar
    "progress_rest": "grey35",      # the rest of the bar
    "progress_text": "",            # "writing exercise · try 2 of 4 · 7 s"
}
CODE_THEME = "monokai"
DOT_FULL, DOT_EMPTY, MAX_LEVEL = "●", "○", 3
# Shown before a one-line response (CLI, Jupyter and the editor's message line).
ICONS = {"pass": "✓ ", "fail": "✗ ", "refused": "! ", "notice": "! "}
# The style of a one-line response by Response.kind ("" = a message from the front-end itself;
# "notice" = Session.notice, shown by the front-ends, never a Response kind).
LINE_STYLES = {"pass": PALETTE["pass"], "fail": PALETTE["fail"], "refused": PALETTE["refused"],
               "unknown": PALETTE["unknown"], "exercise": PALETTE["muted"],
               "quit": PALETTE["muted"], "notice": PALETTE["notice"], "results": PALETTE["muted"],
               "level": PALETTE["level"], "": PALETTE["muted"]}

CLI_HOW_TO = ("Type your function line by line. A line that is exactly a command runs it; "
              "every other line becomes part of your code. After a :w, the next lines you "
              "type start a fresh attempt (:w again with no new lines re-checks the same code).")


def dots(difficulty: int) -> str:
    """●○○ for level 1, ●●○ for level 2, ●●● for level 3."""
    n = max(0, min(MAX_LEVEL, int(difficulty or 0)))
    return DOT_FULL * n + DOT_EMPTY * (MAX_LEVEL - n)


class _Code:
    """Python code, highlighted. In a terminal it sits on the terminal's own background.

    In a notebook Rich exports HTML, and there an explicit "default" background is painted with
    Rich's LIGHT terminal theme: a white box with light text on a dark notebook (seen on Colab).
    So a notebook gets the highlight theme's own (dark) background instead, which reads fine on
    both light and dark pages. The choice is made per console, when the code is drawn.
    """

    def __init__(self, source: str) -> None:
        self.source = source

    def _syntax(self, console: Console) -> Syntax:
        background = None if console.is_jupyter else "default"
        return Syntax(self.source, "python", theme=CODE_THEME, background_color=background)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield self._syntax(console)

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement.get(console, options, self._syntax(console))


def code(source: str) -> _Code:
    """Python code, highlighted (see _Code)."""
    return _Code(source)


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
                 code(ex.starter_code))
    return Panel(body, title=Text(ex.id, style=PALETTE["muted"]),
                 border_style=PALETTE["exercise"], padding=(1, 2))


def solution(source: str) -> Group:
    """'Solution:' and the code below it (no panel, so it can be copied as it is)."""
    return Group(Text("Solution:"), code(source))


def line(text: str, kind: str = "") -> Text:
    """A one-line message with its icon and colour (✓ Passed! ..., ✗ Failed: ...)."""
    return Text(ICONS.get(kind, "") + text, style=LINE_STYLES.get(kind, ""))


def response(resp, session) -> RenderableType:
    """How the line CLI and Jupyter show a Response. A check (:w, :wq, :out) shows the test
    results first and the verdict line below them."""
    kind = resp.kind
    result = getattr(resp, "result", None)
    if result is not None and kind in ("pass", "fail", "results"):
        verdict = line(resp.text, kind if kind != "results" else
                       ("pass" if result.passed else "fail"))
        parts: List[RenderableType] = [verdict]
        if has_results(result):
            parts.insert(0, results(result, session.exercise.function_name))
        if kind == "pass" and getattr(session, "level", None):  # the line CLI's "status bar"
            parts.append(level_line(session))
        return Group(*parts) if len(parts) > 1 else verdict
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


# ----- test results (:w, :wq, :out) -------------------------------------------------------------
#
# results() is the Rich table of the line CLI and Jupyter; result_lines() the same information as
# one Text per screen row, for the editor's results window (and anything else line based).

VALUE_LIMIT = 60      # characters of an expected / returned value before "…"
CALL_LIMIT = 80       # characters of a call like combine(['a'], [1])
PRINT_LINES = 20      # print() lines per test in the table (the editor shows fewer)
PRINT_BAR = "│ "


def has_results(result) -> bool:
    """True when a GradeResult has anything to show besides its message."""
    return bool(result is not None and (result.tests or result.setup_stdout))


def visible(text: str) -> str:
    """Printed text made safe to show: tabs become spaces, control characters (a stray "\\x1b"
    would move the cursor) become escapes like \\x1b. Newlines stay."""
    text = (text or "").replace("\r\n", "\n").expandtabs(4)
    if text.isprintable() or all(c == "\n" or c.isprintable() for c in text):
        return text
    return "".join(c if c == "\n" or c.isprintable() else repr(c)[1:-1] for c in text)


def one_line(text: str) -> str:
    """visible() on a single row: newlines become " ↵ " (multi-line exception messages)."""
    return visible(text).replace("\n", " ↵ ")


def printed_lines(text: str, max_lines: int) -> Tuple[List[str], int]:
    """The lines of print() output to show (at most max_lines) and how many were left out."""
    lines = visible(text).split("\n")
    if lines and lines[-1] == "":
        lines.pop()                    # print() ends with a newline: no empty last line
    if len(lines) <= max_lines:
        return lines, 0
    return lines[:max(0, max_lines)], len(lines) - max(0, max_lines)


def _more(hidden: int) -> str:
    return f"… {hidden} more line{'s' if hidden != 1 else ''}"


def row_icon(t) -> Tuple[str, str]:
    """("✓", pass colour), ("✗", fail colour) or ("·", muted) for a test that never ran."""
    if t.passed:
        return "✓", PALETTE["pass"]
    if not t.ran:
        return "·", PALETTE["muted"]
    return "✗", PALETTE["fail"]


def outcome_parts(t, limit: int = VALUE_LIMIT) -> List[Tuple[str, str]]:
    """What came out of a test, as (text, style) pieces:
    passed  ->  ["→  ", "{'a': 1}"]; wrong -> "expected {}  got None";
    raised  ->  "expected {}  raised ZeroDivisionError: ..."; never ran -> "not run (...)"."""
    muted = PALETTE["muted"]
    if t.passed:
        return [("→  ", muted), (short_repr(t.got, limit), "")]
    if not t.ran:
        return [(t.error, muted)]
    parts = [("expected ", muted), (short_repr(t.expected, limit), ""), ("  ", "")]
    if t.error == TIMED_OUT:
        return parts + [(t.error, PALETTE["error_text"])]
    if t.error:
        return parts + [("raised ", muted), (one_line(t.error), PALETTE["error_text"])]
    return parts + [("got ", muted), (short_repr(t.got, limit), PALETTE["error_text"])]


def results_title(result) -> str:
    """"3/5 passed", or "no tests ran" when the code could not run."""
    return result.summary() or "no tests ran"


def results(result, function_name: str, width: Optional[int] = None) -> RenderableType:
    """The test table (#, call, expected, got, ✓/✗) and the print() output of each test.
    width: only shortens long values (Rich wraps the rest to the console's width)."""
    value_limit = VALUE_LIMIT if not width else max(20, min(VALUE_LIMIT, width // 3))
    parts: List[RenderableType] = []
    if result.setup_stdout:
        parts.append(Text("Printed while your code was loading:", style=PALETTE["muted"]))
        parts.append(_printed_block(result.setup_stdout))
    if result.tests:
        all_passed = all(t.passed for t in result.tests)
        title = Text.assemble(("tests  ", "bold"), (results_title(result),
                              PALETTE["pass"] if all_passed else PALETTE["fail"]))
        table = Table(title=title, title_justify="left", box=box.SIMPLE_HEAD,
                      show_edge=False, pad_edge=False, header_style="bold")
        table.add_column("#", justify="right", no_wrap=True)
        table.add_column("call", overflow="fold")
        table.add_column("expected", overflow="fold")
        table.add_column("got", overflow="fold")
        table.add_column("", no_wrap=True)
        for t in result.tests:
            icon, icon_style = row_icon(t)
            expected = short_repr(t.expected, value_limit)
            if t.passed:
                got = Text(short_repr(t.got, value_limit))
            elif not t.ran:
                got = Text(t.error, style=PALETTE["muted"])
            elif t.error:
                got = Text(one_line(t.error), style=PALETTE["error_text"])
            else:
                got = Text(short_repr(t.got, value_limit), style=PALETTE["error_text"])
            table.add_row(str(t.index), t.call_text(function_name, CALL_LIMIT), expected, got,
                          Text(icon, style=icon_style))
        parts.append(table)
        for t in result.tests:
            if t.stdout:
                parts.append(Text.assemble((f"print() output of test {t.index}  ",
                                            PALETTE["muted"]),
                                           (t.call_text(function_name, 50), PALETTE["muted"])))
                parts.append(_printed_block(t.stdout))
    return Group(*parts)


def _printed_block(text: str, max_lines: int = PRINT_LINES) -> Text:
    lines, hidden = printed_lines(text, max_lines)
    if hidden:
        lines.append(_more(hidden))
    out = Text(style=PALETTE["printed"])
    for i, ln in enumerate(lines or [""]):
        out.append(("\n" if i else "") + "  " + PRINT_BAR + ln)
    return out


def result_lines(result, function_name: str, width: int, print_lines: int = 3) -> List[Text]:
    """The results as one Text per screen row (never wider than `width`), for the editor:

        printed while loading:
          │ hello
        ✓ 1  combine(['a'], [1])  →  {'a': 1}
        ✗ 3  combine([], [])  expected {}  got None
            │ hello from test 3
            │ … 4 more lines
        ✗ 4  combine(['a', 'b', 'c'], [1, 2, 3])          <- too long for one row: the outcome
             expected {'a': 1, ...}  got {'a': 'a', ...}     gets a row of its own

    A result without tests (syntax error, crash while loading) shows its message instead."""
    width = max(1, width)
    out: List[Text] = []

    def add(row: Text) -> None:
        row.truncate(width, overflow="ellipsis")
        out.append(row)

    def add_printed(text: str, indent: str) -> None:
        lines, hidden = printed_lines(text, print_lines)
        for ln in lines + ([_more(hidden)] if hidden else []):
            add(Text(indent + PRINT_BAR + ln, style=PALETTE["printed"]))

    if result.setup_stdout:
        add(Text("printed while loading:", style=PALETTE["muted"]))
        add_printed(result.setup_stdout, "  ")
    for t in result.tests:
        icon, icon_style = row_icon(t)
        head = Text.assemble((f"{icon} {t.index}", icon_style), "  ")
        row = head.copy()
        row.append(t.call_text(function_name, CALL_LIMIT))
        row.append_text(Text.assemble("  ", *outcome_parts(t)))
        if row.cell_len <= width:
            add(row)
        else:  # the outcome on a row of its own, under the call, values shortened to fit
            indent = " " * head.cell_len
            room = width - len(indent) - (3 if t.passed else len("expected   got "))
            head.append(t.call_text(function_name, max(12, width - head.cell_len)))
            add(head)
            limit = max(12, room if t.passed else room // 2)
            add(Text.assemble(indent, *outcome_parts(t, limit)))
        if t.stdout:
            add_printed(t.stdout, "    ")
    if not result.tests:
        icon = ICONS["pass" if result.passed else "fail"]
        for row in textwrap.wrap(icon + one_line(result.message), width=width) or [""]:
            add(Text(row, style=LINE_STYLES["pass" if result.passed else "fail"]))
    return out


# ----- level and progress ----------------------------------------------------------------------

def level_badge(session) -> str:
    """"●○○ 2/3": the session's level and how many solved towards the next one (the editor's
    status line). At the highest level only the dots: there is nothing more to reach."""
    level = getattr(session, "level", 1)
    if level >= MAX_LEVEL:
        return dots(level)
    return f"{dots(level)} {getattr(session, 'solved_at_level', 0)}/{LEVEL_UP_AFTER}"


def level_line(session) -> Text:
    """"Level: beginner ●○○ 1/3 solved to level up" (under a passed check in the CLI / Jupyter)."""
    level = getattr(session, "level", 1)
    name = LEVELS.get(level, "")
    if level >= MAX_LEVEL:
        return Text(f"Level: {name} {dots(level)} (the highest)", style=PALETTE["level"])
    n = getattr(session, "solved_at_level", 0)
    return Text(f"Level: {name} {dots(level)} {n}/{LEVEL_UP_AFTER} solved to level up",
                style=PALETTE["level"])


# While the AI writes the next exercise a front-end shows a Job's progress (session.Job): a model
# reports no progress of its own, so the bar fills against the time budget (elapsed / budget, 60 s by default) and the
# text says which try it is on. These helpers turn a progress event (a dict, see session.STARTING)
# into what every front-end shows.

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"   # one frame per 0.1 s
BAR_FULL, BAR_EMPTY = "━", "─"
CANCEL_HINT = "Ctrl-C cancels"


def progress_text(event: dict, elapsed: Optional[float] = None) -> str:
    """"finding real-world uses · 1 s", "writing exercise · try 2 of 4 · 7 s",
    "try 1 rejected: crashed on its tests · 9 s",
    "using a hand-written exercise instead". elapsed: the live seconds (default: the event's)."""
    stage = event.get("stage", "starting")
    seconds = int(event.get("elapsed", 0.0) if elapsed is None else elapsed)
    attempt, most = event.get("attempt") or 0, event.get("max_attempts") or 0
    tries = f"try {attempt} of {most}" if attempt and most else ""
    detail = str(event.get("detail") or "")
    if stage == "ideas":
        text = "finding real-world uses"
    elif stage == "asking":
        text = f"writing exercise · {tries}" if tries else "writing exercise"
    elif stage == "checking":
        text = f"checking exercise · {tries}" if tries else "checking exercise"
    elif stage == "rejected":
        text = f"try {attempt} rejected: {detail or 'not usable'}"
    elif stage == "done":
        return "exercise ready"
    elif stage == "fallback":
        return detail or "using a hand-written exercise instead"
    else:
        text = "asking the model"
    return f"{text} · {seconds} s"


def progress_fraction(event: dict, elapsed: Optional[float] = None) -> float:
    """How full the bar is: elapsed / budget, at most 0.99 until the exercise is ready."""
    if event.get("stage") in ("done", "fallback"):
        return 1.0
    budget = float(event.get("budget") or 60.0)
    seconds = float(event.get("elapsed", 0.0) if elapsed is None else elapsed)
    return max(0.0, min(0.99, seconds / budget)) if budget > 0 else 0.0


def spinner_frame(elapsed: float) -> str:
    return SPINNER[int(max(0.0, elapsed) * 10) % len(SPINNER)]


def progress_line(event: dict, elapsed: float, width: int, hint: str = CANCEL_HINT) -> Text:
    """One row: "⠹ writing exercise · try 2 of 4 · 7 s  ━━━━━━━━──────────  Ctrl-C cancels".
    Never wider than `width`: the hint goes first, then the bar gets shorter (at least 8 cells)."""
    width = max(1, width)
    head = Text.assemble((spinner_frame(elapsed), PALETTE["progress_spinner"]), " ",
                         (progress_text(event, elapsed), PALETTE["progress_text"]))
    tail = f"  {hint}" if hint else ""
    room = width - head.cell_len - 2 - len(tail)
    if room < 8 and tail:
        tail, room = "", width - head.cell_len - 2
    row = head
    if room >= 8:
        bar = min(30, room)
        full = int(round(progress_fraction(event, elapsed) * bar))
        row.append("  ")
        row.append(BAR_FULL * full, style=PALETTE["progress_bar"])
        row.append(BAR_EMPTY * (bar - full), style=PALETTE["progress_rest"])
        row.append(tail, style=PALETTE["muted"])
    row.truncate(width, overflow="ellipsis")
    return row


def wait_with_bar(job, console: Console, refresh: float = 0.1) -> bool:
    """Show a transient Rich progress bar until `job` (a session.Job) is done; Ctrl-C cancels it.
    Returns False if it was cancelled. A job that is already done shows nothing (a preset)."""
    if job.done:
        return not job.cancelled
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

    bar = Progress(SpinnerColumn(style=PALETTE["progress_spinner"]),
                   TextColumn("{task.description}"),
                   BarColumn(bar_width=30, style=PALETTE["progress_rest"],
                             complete_style=PALETTE["progress_bar"],
                             finished_style=PALETTE["progress_bar"]),
                   TextColumn(CANCEL_HINT, style=PALETTE["muted"]),
                   console=console, transient=True, refresh_per_second=10)
    try:
        with bar:
            task = bar.add_task(progress_text(job.progress, job.elapsed), total=100)
            while not job.wait(refresh):
                event, elapsed = job.progress, job.elapsed
                bar.update(task, description=progress_text(event, elapsed),
                           completed=100 * progress_fraction(event, elapsed))
            bar.update(task, completed=100)
    except KeyboardInterrupt:
        job.cancel()
    return not job.cancelled


# ----- syntax highlighting for the editor ------------------------------------------------------

@lru_cache(maxsize=1)
def _highlighter() -> Syntax:
    return Syntax("", "python", theme=CODE_THEME, background_color="default")  # only its lexer and theme are used


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
