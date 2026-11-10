"""Jupyter front-end: the %%pygym cell magic (and the %pygym line magic).

The student writes the function in a notebook cell whose first line is the command:

    %%pygym :w
    def combine(keys, values):
        return dict(zip(keys, values))

The cell body is the code; the magic sends both to session.handle() and prints the Response.
A command without code (%pygym :h, or an empty %%pygym :q cell) passes code=None, so Session
uses the code from the last check. IPython is imported only here, lazily, and is never required.

Output goes through Rich (the same panels as the line CLI, see style.py): in a notebook Rich
shows it as HTML, elsewhere (a plain IPython shell) as text.
"""
from __future__ import annotations

from typing import Optional

from rich.console import Console, Group
from rich.text import Text

from . import style

_active = None  # the Session the magics talk to (None = no exercise running)

INSTRUCTIONS = ("Write your function in a new cell that starts with  %%pygym :w   "
                "(other commands: %pygym :h, %pygym :help, %pygym :solution, %pygym :s, "
                "%%pygym :wq, %pygym :q)")
NO_SESSION = 'No active exercise. Run exercise("zip") first.'


def in_notebook() -> bool:
    """True inside a Jupyter notebook kernel (not the IPython terminal, not plain Python)."""
    try:
        from IPython import get_ipython
    except Exception:
        return False
    ip = get_ipython()
    return ip is not None and type(ip).__name__ == "ZMQInteractiveShell"


def _console() -> Console:
    # Made at call time, so it writes to whatever sys.stdout is now (the notebook, or a capture).
    return Console(highlight=False)


def _example(ex) -> Group:
    return Group(Text("For example:", style=style.PALETTE["muted"]),
                 Text("%%pygym :w", style=style.PALETTE["command"]),
                 style.code(ex.function_signature + "\n    ..."))


def _intro(ex):
    return style.intro(how_to=[Text(INSTRUCTIONS, style=style.PALETTE["muted"]), Text(""),
                               _example(ex)])


def start(session, ip=None) -> None:
    """Make `session` the active exercise, register the magics and print the question."""
    global _active
    if ip is None:
        try:
            from IPython import get_ipython
            ip = get_ipython()
        except Exception:
            ip = None
    if ip is None:
        print("The Jupyter front-end only works inside Jupyter or IPython. Try ui='text'.")
        return
    _active = session
    # "line_cell" registers both %pygym (line magic) and %%pygym (cell magic).
    ip.register_magic_function(pygym_magic, magic_kind="line_cell", magic_name="pygym")
    console = _console()
    console.print(_intro(session.exercise))
    console.print(style.exercise_view(session))  # with Session.notice below, if any


def pygym_magic(line: str, cell: Optional[str] = None) -> None:
    """%pygym <command>  or  %%pygym <command> + code in the cell body."""
    try:
        _run(line, cell)
    except Exception as e:  # never raise into the notebook
        print(f"pygym: something went wrong ({type(e).__name__}: {e})")


def _run(line: str, cell: Optional[str]) -> None:
    global _active
    console = _console()
    session = _active
    if session is None:
        console.print(Text(NO_SESSION, style=style.PALETTE["refused"]))
        return
    command = (line or "").strip() or ":w"
    code = cell if cell is not None and cell.strip() else None
    resp = session.handle(command, code)
    console.print(style.response(resp, session))
    if resp.ask_reveal:
        try:
            answer = input("Reveal the solution? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer.strip().lower() in ("y", "yes"):
            console.print(style.solution(session.solution()))
    if resp.clear_code:
        console.print(style.exercise_view(session))
    if resp.quit:
        console.print(Text("Session ended.", style=style.PALETTE["muted"]))
        _active = None
