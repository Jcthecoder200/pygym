"""Front-ends over the same Session, and run() which picks one.

Here: the line CLI (run_text, also the Windows / non-terminal fallback) and the tkinter popup.
The full-screen terminal editor is in editor.py, the Jupyter %%pygym magic in jupyter.py.
"""
from __future__ import annotations

import re
from typing import Callable, List

from rich.console import Console, RenderableType
from rich.text import Text

from . import style
from .session import Session

# A line counts as a command only if it is ":" followed by letters/"!" and nothing else
# (after trimming spaces). Known commands run; other ":xyz" lines are reported as unknown.
# Real Python code practically never looks like this, so it is safe to keep out of the code.
_COMMAND_LINE = re.compile(r"^:[A-Za-z!]+$")


def _printer(print_fn: Callable[..., None]) -> Callable[[RenderableType], None]:
    """The real print gets colours through Rich; any other print_fn gets plain text."""
    if print_fn is print:
        return Console(highlight=False).print
    return lambda renderable: print_fn(style.to_plain(renderable))


def run_text(session: Session, input_fn: Callable[[str], str] = input,
             print_fn: Callable[..., None] = print) -> None:
    show = _printer(print_fn)
    show(style.intro())
    show(style.exercise_view(session))  # with Session.notice (AI warning / fallback note) below
    buffer: List[str] = []  # lines typed since the last :w

    while True:
        try:
            line = input_fn(">>> " if not buffer else "... ")
        except (EOFError, KeyboardInterrupt):
            show(Text("\nBye!", style=style.PALETTE["muted"]))
            return

        stripped = line.strip()
        if not _COMMAND_LINE.match(stripped):
            buffer.append(line)
            continue

        # Current code: what was typed since the last :w; None = "no new code" (Session uses the saved code).
        code = "\n".join(buffer) if buffer else None
        resp = session.handle(stripped, code)
        if stripped in (":w", ":wq"):
            buffer = []  # the next lines start a fresh attempt
        show(style.response(resp, session))

        if resp.ask_reveal:
            try:
                answer = input_fn("Reveal the solution? [y/N] ")
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer.strip().lower() in ("y", "yes"):
                show(style.solution(session.solution()))
        if resp.clear_code:
            buffer = []
            show(style.exercise_view(session))
        if resp.quit:
            return


def run_tk(session: Session) -> None:
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
    for label, cmd in [("Submit", submit), ("Hint", hint), ("Show solution", solution),
                       ("Skip", lambda: show(session.skip()))]:
        tk.Button(bar, text=label, command=cmd).pack(side="left", padx=4)

    show(session.exercise)
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
    """ui: "auto" (default), "jupyter", "editor", "tk" or "text"."""
    if ui == "auto":
        ui = _auto_ui()
    if ui == "jupyter":
        from . import jupyter
        jupyter.start(session)
    elif ui == "editor":
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
