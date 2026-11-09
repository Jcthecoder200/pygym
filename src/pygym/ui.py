"""Two front-ends over the same Session: a text UI (terminal/Jupyter) and a tkinter popup."""
from __future__ import annotations

from .session import Session


def _header(ex) -> str:
    return (f"\n=== {ex.target_function} · level {ex.difficulty} ===\n"
            f"{ex.question_text}\n\nYour function should look like:\n  {ex.function_signature}\n")


def run_text(session: Session) -> None:
    print(_header(session.exercise))
    print("Type your code, then a line containing only END to submit.")
    print("Commands: :hint  :solution  :skip  :quit")
    while True:
        lines = []
        while True:
            line = input(">>> " if not lines else "... ")
            if not lines and line.strip().startswith(":"):
                lines = [line.strip()]
                break
            if line.strip() == "END":
                break
            lines.append(line)
        cmd = lines[0] if lines and lines[0].startswith(":") else None
        if cmd == ":quit":
            return
        if cmd == ":hint":
            print("Hint:", session.hint())
        elif cmd == ":solution":
            print("Solution:\n" + session.solution())
        elif cmd == ":skip":
            print(_header(session.skip()))
        elif cmd:
            print("Unknown command.")
        else:
            result = session.submit("\n".join(lines))
            print(("PASSED: " if result.passed else "FAILED: ") + result.message)
            if result.passed:
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
        box.insert("1.0", ex.function_signature + "\n    ")
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


def run(session: Session, ui: str = "auto") -> None:
    if ui == "tk" or (ui == "auto" and tk_available()):
        run_tk(session)
    else:
        run_text(session)
