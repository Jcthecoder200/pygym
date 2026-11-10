"""pygym: micro-exercises for Python built-ins."""
from .loader import ExerciseUnavailable, available_functions
from .session import Session

__version__ = "0.1.0"
__all__ = ["exercise", "available_functions", "ExerciseUnavailable", "Session"]


def exercise(name, difficulty=None, ui="auto"):
    """Start an exercise. `name` is a built-in's name (or the function itself).

    `difficulty` is 1, 2 or 3 (None = any level). `ui` chooses the front-end:
      "auto"     (default) Jupyter notebook -> "jupyter"; Linux/macOS terminal -> "editor";
                 otherwise "tk" if a window can open, else "text"
      "jupyter"  write code in notebook cells that start with  %%pygym :w
      "editor"   full-screen terminal editor (Neovim-like: i to type, Esc, then :w / :wq / :q)
      "tk"       a small tkinter window
      "text"     the line-by-line CLI (works everywhere, also on Windows)
    """
    from . import ui as _ui

    if callable(name):
        name = getattr(name, "__name__", str(name))
    try:
        session = Session(name, difficulty)
    except ExerciseUnavailable as e:
        print(e)
        return
    _ui.run(session, ui)
