"""Front-ends over the same Session, and run() which picks one.

Here: the line CLI (run_text, also the Windows / non-terminal fallback) and the tkinter popup.
The full-screen terminal editor is in editor.py, the Jupyter %%pygym magic in jupyter.py.
"""
from __future__ import annotations

import re
from typing import Callable, List, Optional

from rich.console import Console, RenderableType
from rich.text import Text

from . import style
from .session import NOT_ANSWERED, Session, bigger_answer, is_command_line

# A line counts as a command only if the whole line is one (after trimming spaces): ":" followed
# by letters/"!", or ":level <one word>" (session.is_command_line). Known commands run; other
# ":xyz" lines are reported as unknown. Real Python code practically never looks like this, so it
# is safe to keep out of the code. (_COMMAND_LINE: the plain form, kept for old imports.)
_COMMAND_LINE = re.compile(r"^:[A-Za-z!]+$")


def _printer(print_fn: Callable[..., None]) -> Callable[[RenderableType], None]:
    """The real print gets colours through Rich; any other print_fn gets plain text."""
    if print_fn is print:
        return Console(highlight=False).print
    return lambda renderable: print_fn(style.to_plain(renderable))


def run_text(session: Session, input_fn: Callable[[str], str] = input,
             print_fn: Callable[..., None] = print, console: Optional[Console] = None) -> None:
    """console: where the progress bar goes while the AI writes the next exercise (default: the
    terminal when print_fn is print; with another print_fn and no console there is no bar)."""
    show = _printer(print_fn)
    if console is None and print_fn is print:
        console = Console(highlight=False)
    show(style.intro())
    show(style.exercise_view(session))  # with Session.notice (AI warning / fallback note) below
    buffer: List[str] = []  # lines typed since the last :w

    def execute(command: str, code: Optional[str]):
        job = session.start(command, code)  # done at once unless the AI writes a new exercise
        if not job.done:
            if console is not None:
                style.wait_with_bar(job, console)
            else:
                try:
                    job.wait()
                except KeyboardInterrupt:
                    job.cancel()
        return job.result()

    def ask(prompt: str) -> Optional[str]:
        try:
            return input_fn(prompt)
        except (EOFError, KeyboardInterrupt):
            return None

    def obey(resp) -> bool:
        """Show a Response and obey its flags. True when the session is over."""
        show(style.response(resp, session))
        if resp.ask_reveal:
            answer = ask("Reveal the solution? [y/N] ") or ""
            if answer.strip().lower() in ("y", "yes"):
                show(style.solution(session.solution()))
        if resp.clear_code:
            buffer.clear()
            show(style.exercise_view(session))
        if resp.quit:
            return True
        if resp.ask_bigger:  # the answer is the next command (":bigger yes" / ":bigger no")
            command = bigger_answer(ask(session.bigger_prompt()))
            if command is None:
                show(style.line(NOT_ANSWERED, "refused"))
                return False
            return obey(execute(command, None))
        return False

    question = session.question()  # started at level 3 with a function too small for it
    if question is not None and obey(question):
        return

    while True:
        line = ask(">>> " if not buffer else "... ")
        if line is None:
            show(Text("\nBye!", style=style.PALETTE["muted"]))
            return

        stripped = line.strip()
        if not is_command_line(stripped):
            buffer.append(line)
            continue

        # Current code: what was typed since the last :w; None = "no new code" (Session uses the saved code).
        code = "\n".join(buffer) if buffer else None
        resp = execute(stripped, code)
        if stripped in (":w", ":wq"):
            buffer.clear()  # the next lines start a fresh attempt
        if obey(resp):
            return


def run_tk(session: Session, _driver=None) -> None:
    """The tkinter popup. `_driver(root, box, status, buttons)` is a hook for tests and for
    tools/real_machine_check.py --tk: it is called once, just after the window is up, and is
    expected to end by calling root.destroy()."""
    import tkinter as tk
    from tkinter import scrolledtext

    root = tk.Tk()
    root.title("pygym")
    question = tk.Label(root, justify="left", wraplength=560, anchor="w")
    question.pack(fill="x", padx=10, pady=(10, 4))
    box = scrolledtext.ScrolledText(root, width=70, height=14, font=("Courier", 11))
    box.pack(padx=10, pady=4)
    status = tk.Label(root, text="", anchor="w")
    status.pack(fill="x", padx=10)

    def show(ex):
        question.config(text=f"[{ex.target_function} · level {ex.difficulty}]\n"
                             f"{ex.question_text}")
        box.delete("1.0", "end")
        box.insert("1.0", ex.starter_code + "\n    ")
        status.config(text="", fg="black")

    def submit():
        r = session.submit(box.get("1.0", "end"))
        status.config(text=("Passed! " if r.passed else "Failed. ") + r.message,
                      fg="green" if r.passed else "red")

    def hint():
        status.config(text="Hint: " + session.hint(), fg="black")

    def solution():
        box.delete("1.0", "end")
        box.insert("1.0", session.solution())

    bar = tk.Frame(root)
    bar.pack(pady=8)
    buttons = {}
    for label, cmd in [("Submit", submit), ("Hint", hint), ("Show solution", solution),
                       ("Skip", lambda: show(session.skip()))]:
        buttons[label] = tk.Button(bar, text=label, command=cmd)
        buttons[label].pack(side="left", padx=4)

    show(session.exercise)
    if _driver is not None:
        root.after(100, lambda: _driver(root, box, status, buttons))
    root.mainloop()


def tk_available() -> bool:
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


def _auto_ui() -> str:
    """Pick the best front-end for where pygym is running."""
    from . import editor, jupyter

    if jupyter.in_notebook():
        return "jupyter"
    # Windows: the tkinter popup is the default; the editor runs there only with ui="editor".
    if not editor.is_windows() and editor.editor_available():
        return "editor"
    if tk_available():
        return "tk"
    return "text"


def run(session: Session, ui: str = "auto") -> None:
    """ui: "auto" (default), "jupyter", "editor", "tk" or "text".

    When the window closes (quit, Ctrl-C, end of input) the session is closed, which stops its
    background prefetch. Jupyter is the exception: start() returns at once and the session lives
    on in the notebook (a quit command or the next session closes it)."""
    if ui == "auto":
        ui = _auto_ui()
    if ui == "jupyter":
        from . import jupyter
        jupyter.start(session)
        return
    try:
        _run_window(session, ui)
    finally:
        session.close()


def _run_window(session: Session, ui: str) -> None:
    if ui == "editor":
        from . import editor
        if editor.editor_available():
            editor.run_editor(session)
        else:
            print("The full-screen editor needs a real terminal (Linux/macOS, or a Windows 10+ "
                  "console); using the line CLI instead.")
            run_text(session)
    elif ui == "tk":
        if session.notice:  # the popup has no place for it: say it in the terminal first
            print(style.NOTICE_ICON + session.notice)
        run_tk(session)
    else:
        run_text(session)
