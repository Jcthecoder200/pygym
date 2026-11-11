"""Shared data contract. Every component (content, grader, UI) talks through these."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional


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
    # Small text files written into the (temporary, empty) working folder before every test:
    # {"notes.txt": "a\nb"}. Relative paths only, 64 KB in total. open() is allowed when set.
    files: Dict[str, str] = field(default_factory=dict)
    # Answers of a local test server the code reaches through the global name BASE_URL:
    # {"/users/1": {"status": 200, "json": {...}}, "/hello": {"text": "hi"}}.
    http_fixtures: Dict[str, Any] = field(default_factory=dict)

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


def short_repr(value: Any, limit: int = 80) -> str:
    """repr(value), cut to `limit` characters with "…" at the end."""
    text = repr(value)
    return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"


@dataclass
class TestResult:
    """One test of a check: what went in, what should come out, what came out."""
    __test__ = False              # not a pytest test class

    index: int                    # the test's number as the student sees it: 1, 2, 3 ...
    args: List[Any]               # the arguments: the call is function_name(*args)
    expected: Any                 # the expected return value (as the grader compares it)
    got: Any = None               # the student's return value (when error == "")
    error: str = ""               # "ZeroDivisionError: division by zero", or why it did not run
    passed: bool = False
    stdout: str = ""              # what print() wrote during this test

    def call_text(self, function_name: str, limit: int = 80) -> str:
        """The call the student would write: "combine(['a'], [1])", cut to `limit` with "…"."""
        text = f"{function_name}({', '.join(repr(a) for a in self.args)})"
        return text if len(text) <= limit else text[:max(1, limit - 1)] + "…"

    @property
    def ran(self) -> bool:
        """False for a test that never started (an earlier test ran too long)."""
        return self.error != NOT_RUN


# TestResult.error after a timeout: the test that was running, and the tests after it.
TIMED_OUT = "ran too long and was stopped (an endless loop?)"
NOT_RUN = "not run (an earlier test ran too long)"


@dataclass
class GradeResult:
    passed: bool
    # "ok" | "wrong_output" | "target_not_used" | "timeout" | "error"
    reason: str
    message: str = ""
    # One row per test, in order (empty when the code could not run: syntax error, broken
    # exercise, a crash while loading). Filled for passing checks too.
    tests: List[TestResult] = field(default_factory=list)
    setup_stdout: str = ""        # what print() wrote while the code was loaded (top-level code)

    @property
    def passed_count(self) -> int:
        return sum(1 for t in self.tests if t.passed)

    def summary(self) -> str:
        """"3/5 passed" ("" when there are no rows)."""
        return f"{self.passed_count}/{len(self.tests)} passed" if self.tests else ""


@dataclass
class Response:
    """What Session.handle() returns. A front-end only displays it and obeys the flags."""
    text: str
    # "pass" | "fail" | "hint" | "help" | "solution" | "commands" | "results" | "exercise"
    # | "refused" | "unknown" | "quit"   ("results": :out, result = the last check or None)
    kind: str
    quit: bool = False          # the front-end should close now
    ask_reveal: bool = False    # ask the student whether to show the solution (after a failed :wq)
    clear_code: bool = False    # a new exercise started: reset the code box / buffer
    result: Optional[GradeResult] = None  # set for :w / :wq, and for :out after a check
