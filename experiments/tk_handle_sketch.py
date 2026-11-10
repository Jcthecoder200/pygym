"""Starting point for the tkinter popup on top of Session.handle(). NOT wired into the package.

The tkinter developer can copy this into src/pygym/ui.py (replacing run_tk) and restyle it.
It contains no rules of its own: every button sends a command to session.handle() and the
popup only displays the Response and obeys its flags.

Try it from the repo root (opens a window):
    PYTHONPATH=src python experiments/tk_handle_sketch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pygym.session import Session

# How each Response.kind is coloured in the status line (the UI's own choice, not a rule).
COLORS = {"pass": "green", "fail": "red", "refused": "orange3", "unknown": "red"}


def build_tk(session: Session):
    """Create the window and return (root, widgets, run). Split from mainloop() so it can be tested."""
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    root = tk.Tk()
    root.title("pygym")
    question = tk.Label(root, justify="left", wraplength=560, anchor="w")
    question.pack(fill="x", padx=10, pady=(10, 4))
    box = scrolledtext.ScrolledText(root, width=70, height=14, font=("Courier", 11))
    box.pack(padx=10, pady=4)
    status = tk.Label(root, text="", anchor="w", justify="left", wraplength=560)
    status.pack(fill="x", padx=10)

    def code() -> str:
        return box.get("1.0", "end-1c")  # "end-1c" drops the newline Tk always adds

    def popup(title: str, text: str) -> None:
        win = tk.Toplevel(root)
        win.title(title)
        view = scrolledtext.ScrolledText(win, width=72, height=16, font=("Courier", 10))
        view.insert("1.0", text)
        view.config(state="disabled")
        view.pack(padx=8, pady=8)

    def show(ex) -> None:
        question.config(text=f"[{ex.target_function} · level {ex.difficulty}]\n{ex.question_text}")
        starter = ex.function_signature + "\n    "
        box.delete("1.0", "end")
        box.insert("1.0", starter)
        # The pre-filled starter text counts as "saved", so :q right after opening is allowed.
        session.saved_code = starter
        status.config(text="", fg="black")

    def run(cmd: str) -> None:
        r = session.handle(cmd, code())
        if r.kind == "help":
            popup(f"Documentation: {session.exercise.target_function}", r.text)
            return
        if r.kind == "solution":
            popup("Solution", r.text)  # shown separately so the student's own code is kept
            return
        status.config(text=("Hint: " if r.kind == "hint" else "") + r.text,
                      fg=COLORS.get(r.kind, "black"))
        if r.ask_reveal and messagebox.askyesno("pygym", "That's not right yet. Reveal the solution?"):
            popup("Solution", session.solution())
        if r.clear_code:
            show(session.exercise)
        if r.quit:
            root.destroy()

    def on_close() -> None:
        # Window X button = :q. If refused (unchecked changes), offer :q! like vim's warning.
        r = session.handle(":q", code())
        if r.quit or messagebox.askyesno("pygym", r.text + "\n\nClose anyway?"):
            root.destroy()

    bar = tk.Frame(root)
    bar.pack(pady=8)
    buttons = {}
    for label, cmd in [("Check  (:w)", ":w"), ("Check & close  (:wq)", ":wq"), ("Hint", ":h"),
                       ("Docs", ":help"), ("Skip", ":s"), ("Solution", ":solution")]:
        b = tk.Button(bar, text=label, command=lambda c=cmd: run(c))
        b.pack(side="left", padx=4)
        buttons[cmd] = b

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.bind("<Control-s>", lambda e: run(":w"))  # optional keyboard shortcut

    show(session.exercise)
    widgets = {"question": question, "box": box, "status": status, "buttons": buttons}
    return root, widgets, run


def run_tk(session: Session) -> None:
    root, _, _ = build_tk(session)
    root.mainloop()


if __name__ == "__main__":
    run_tk(Session("zip"))
