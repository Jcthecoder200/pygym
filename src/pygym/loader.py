"""Loads exercises from the bundled JSON dataset."""
from __future__ import annotations

import json
import random
from importlib import resources
from typing import List, Optional, Set

from .models import Exercise


class ExerciseUnavailable(Exception):
    """Raised when no exercise exists for the requested function."""


def load_all() -> List[Exercise]:
    text = resources.files("pygym.data").joinpath("exercises.json").read_text(encoding="utf-8")
    return [Exercise.from_dict(d) for d in json.loads(text)]


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
