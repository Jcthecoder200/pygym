"""Loads exercises from the bundled JSON dataset."""
from __future__ import annotations

import json
import os
import random
from typing import List, Optional, Set

from .models import Exercise


class ExerciseUnavailable(Exception):
    """Raised when no exercise exists for the requested function."""


def _dataset_text() -> str:
    # A plain file next to this module in every normal install. Reading it directly avoids
    # importing importlib.resources (~35 ms), which the tips under a traceback would pay.
    path = os.path.join(os.path.dirname(__file__), "data", "exercises.json")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:  # e.g. installed inside a zip file
        from importlib import resources
        return resources.files("pygym.data").joinpath("exercises.json").read_text(encoding="utf-8")


def load_all() -> List[Exercise]:
    return [Exercise.from_dict(d) for d in json.loads(_dataset_text())]


def available_functions() -> List[str]:
    return sorted({e.target_function for e in load_all()})


def pick(name: str, difficulty: Optional[int] = None,
         exclude_ids: Optional[Set[str]] = None) -> Exercise:
    pool = [e for e in load_all() if e.target_function == name]
    if not pool:
        raise ExerciseUnavailable(
            f"No exercise available for '{name}' yet. Available: {', '.join(available_functions())}"
        )
    if difficulty is not None:
        by_level = [e for e in pool if e.difficulty == difficulty]
        pool = by_level or pool
    fresh = [e for e in pool if e.id not in (exclude_ids or set())]
    return random.choice(fresh or pool)
