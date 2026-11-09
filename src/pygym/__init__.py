"""pygym: micro-exercises for Python built-ins."""
from .loader import ExerciseUnavailable, available_functions
from .session import Session

__version__ = "0.0.0"
__all__ = ["exercise", "available_functions", "ExerciseUnavailable", "Session"]


def exercise(name, difficulty=None, ui="auto"):
    """Start an exercise. `name` is a built-in's name (or the function itself)."""
    from . import ui as _ui

    if callable(name):
        name = getattr(name, "__name__", str(name))
    try:
        session = Session(name, difficulty)
    except ExerciseUnavailable as e:
        print(e)
        return
    _ui.run(session, ui)
