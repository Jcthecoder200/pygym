"""One learning session. The UI only ever talks to this object."""
from __future__ import annotations

from typing import Set

from . import grader, loader, progress
from .models import Exercise, GradeResult


class Session:
    def __init__(self, name: str, difficulty=None):
        self.name = name
        self.difficulty = difficulty
        self.seen: Set[str] = set()
        self.exercise: Exercise = self._next()

    def _next(self) -> Exercise:
        ex = loader.pick(self.name, self.difficulty, self.seen)
        self.seen.add(ex.id)
        return ex

    def submit(self, code: str) -> GradeResult:
        result = grader.grade(code, self.exercise)
        progress.record(self.exercise.id, result.passed)
        return result

    def hint(self) -> str:
        return self.exercise.hint or "No hint for this one. Re-read the question carefully."

    def solution(self) -> str:
        return self.exercise.reference_solution

    def skip(self) -> Exercise:
        self.exercise = self._next()
        return self.exercise
