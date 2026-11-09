"""Shared data contract. Every component (content, grader, UI) talks through these."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, List


@dataclass
class Exercise:
    id: str
    target_function: str          # the built-in the user must use, e.g. "zip"
    difficulty: int               # 1, 2 or 3
    question_text: str
    function_name: str            # name the user's function must have
    function_signature: str       # shown to the user
    reference_solution: str
    test_inputs: List[List[Any]]  # each item is the argument list for one test
    expected_outputs: List[Any] = field(default_factory=list)
    hint: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Exercise":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class GradeResult:
    passed: bool
    # "ok" | "wrong_output" | "target_not_used" | "timeout" | "error"
    reason: str
    message: str = ""
