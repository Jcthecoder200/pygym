"""Jupyter front-end: the %%pygym cell magic (and the %pygym line magic).

The student writes the function in a notebook cell whose first line is the command:

    %%pygym :w
    def combine(keys, values):
        return dict(zip(keys, values))

The cell body is the code; the magic sends both to session.handle() and prints the Response.
A command without code (%pygym :h, or an empty %%pygym :q cell) passes code=None, so Session
uses the code from the last check. IPython is imported only here, lazily, and is never required.

Output goes through Rich (the same panels as the line CLI, see style.py): in a notebook Rich
shows it as HTML, elsewhere (a plain IPython shell) as text. A check prints the test table (call,
expected, got, ✓/✗) and each test's print() output under the cell; %pygym :out prints it again.
While the AI writes a new exercise (%pygym :s, %pygym :level 2 in AI mode) a progress line is
shown (see _wait); interrupting the kernel cancels and keeps the current exercise.

The bigger-task question (Response.ask_bigger, or Session.question() right after a level-3 start
with a function too small for advanced, such as bin) is asked with input() like the reveal
question; the answer is sent as the next command (:bigger yes / :bigger no).
"""
from __future__ import annotations

from typing import Optional

from rich.console import Console, Group
from rich.text import Text

from . import style
from .session import NOT_ANSWERED, bigger_answer

_active = None  # the Session the magics talk to (None = no exercise running)

INSTRUCTIONS = ("Write your function in a new cell that starts with  %%pygym :w   "
                "(other commands: %pygym :out, %pygym :h, %pygym :help, %pygym :solution, "
                "%pygym :s, %pygym :level, %%pygym :wq, %pygym :q)")
PROGRESS_WIDTH = 72  # characters of the progress line while the AI writes an exercise
NO_SESSION = 'No active exercise. Run exercise("zip") first.'


def in_notebook() -> bool:
    """True inside a Jupyter notebook kernel (not the IPython terminal, not plain Python).

    Looks at the whole class hierarchy, not just the name: Google Colab's shell is
    google.colab._shell.Shell, a SUBCLASS of ZMQInteractiveShell, so a plain name comparison
    would send every Colab user to the text front-end (Rich's own notebook check has the same
    special case)."""
    try:
        from IPython import get_ipython
    except Exception:
        return False
    ip = get_ipython()
    if ip is None:
        return False
    cls = type(ip)
    return (any(c.__name__ == "ZMQInteractiveShell" for c in cls.__mro__)
            or "google.colab" in str(cls))


def _console() -> Console:
    # Made at call time, so it writes to whatever sys.stdout is now (the notebook, or a capture).
    return Console(highlight=False)


def _example(ex) -> Group:
    return Group(Text("For example:", style=style.PALETTE["muted"]),
                 Text("%%pygym :w", style=style.PALETTE["command"]),
                 style.code(ex.starter_code + "\n    ..."))


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
    if _active is not None and _active is not session:
        _active.close()  # the old session's prefetch is no longer wanted
    _active = session
    # "line_cell" registers both %pygym (line magic) and %%pygym (cell magic).
    ip.register_magic_function(pygym_magic, magic_kind="line_cell", magic_name="pygym")
    console = _console()
    console.print(_intro(session.exercise))
    console.print(style.exercise_view(session))  # with Session.notice below, if any
    question = getattr(session, "question", lambda: None)()
    if question is not None:  # started at level 3 with a function too small for it
        _obey(console, session, question)


def _wait(job, refresh: float = 0.25) -> None:
    """Show a progress line until the job is done. Interrupting the kernel (the ■ button) cancels.

    In a notebook: one output line updated in place (an IPython display handle, no ipywidgets
    needed), cleared at the end. Elsewhere (the IPython terminal): a plain line each time the
    text changes, without the seconds (so it is not one line per tick)."""
    notebook = in_notebook()
    handle = None
    last = ""
    try:
        while True:
            finished = job.wait(refresh)
            event, elapsed = job.progress, job.elapsed
            if notebook:
                text = style.progress_line(event, elapsed, PROGRESS_WIDTH,
                                           hint="■ interrupts").plain
                if handle is None:
                    from IPython.display import display
                    handle = display({"text/plain": text}, raw=True, display_id=True)
                else:
                    handle.update({"text/plain": text}, raw=True)
            else:
                text = style.progress_text(event, elapsed).rsplit(" · ", 1)[0]
                if text != last and event.get("stage") != "starting":
                    print("… " + text, flush=True)
                    last = text
            if finished:
                break
    except KeyboardInterrupt:
        job.cancel()
    finally:
        if handle is not None:
            handle.update({"text/plain": ""}, raw=True)


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
    if _obey(console, session, _execute(session, command, code)):
        _active = None


def _execute(session, command: str, code: Optional[str]):
    if session.needs_wait(command):  # the AI writes the next exercise: show its progress
        job = session.start(command, code)
        _wait(job)
        return job.result()
    return session.handle(command, code)


def _ask(prompt: str) -> Optional[str]:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


def _obey(console, session, resp) -> bool:
    """Print a Response and obey its flags. True when the session ended."""
    console.print(style.response(resp, session))
    if resp.ask_reveal:
        answer = _ask("Reveal the solution? [y/N] ") or ""
        if answer.strip().lower() in ("y", "yes"):
            console.print(style.solution(session.solution()))
    if resp.clear_code:
        console.print(style.exercise_view(session))
    if resp.quit:
        console.print(Text("Session ended.", style=style.PALETTE["muted"]))
        return True
    if resp.ask_bigger:  # the answer is the next command (":bigger yes" / ":bigger no")
        command = bigger_answer(_ask(session.bigger_prompt()))
        if command is None:
            console.print(style.line(NOT_ANSWERED, "refused"))
            return False
        return _obey(console, session, _execute(session, command, None))
    return False
