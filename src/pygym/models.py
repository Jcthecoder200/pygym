"""Shared data contract. Every component (content, grader, UI) talks through these."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, List, Optional


# Built-in types whose methods can be targets ("str.join"): no import needed.
_METHOD_OWNERS = {"str", "list", "dict", "set", "tuple", "int", "float", "bytes"}


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
    # Extra modules the student's code may import for this exercise (besides the sandbox defaults).
    allowed_imports: List[str] = field(default_factory=list)
    source: str = "preset"        # "preset" (bundled, human-reviewed) or "ai" (generated, unreviewed)
    generator: str = ""           # for source "ai": which model made it, e.g. "ollama:qwen2.5:3b"

    @property
    def import_lines(self) -> List[str]:
        """The import the student needs for the target, e.g. ["import statistics"] for
        "statistics.median". Built-ins ("zip") and methods of built-in types ("str.join") need none."""
        module, _, _ = self.target_function.rpartition(".")
        if not module or module in _METHOD_OWNERS:
            return []
        return [f"import {module.split('.')[0]}"]

    @property
    def starter_code(self) -> str:
        """What every front-end pre-fills: the needed import (if any) above the def line."""
        head = "\n".join(self.import_lines)
        return (head + "\n\n" if head else "") + self.function_signature

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


@dataclass
class Response:
    """What Session.handle() returns. A front-end only displays it and obeys the flags."""
    text: str
    # "pass" | "fail" | "hint" | "help" | "solution" | "exercise" | "refused" | "unknown" | "quit"
    kind: str
    quit: bool = False          # the front-end should close now
    ask_reveal: bool = False    # ask the student whether to show the solution (after a failed :wq)
    clear_code: bool = False    # a new exercise started: reset the code box / buffer
    result: Optional[GradeResult] = None  # set for :w / :wq
