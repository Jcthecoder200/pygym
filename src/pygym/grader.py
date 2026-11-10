"""Checks a user's code against an Exercise. Never runs user code in the main process."""
from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, List, Optional

from ._runner import ALLOWED_IMPORTS, SAFE_MODULES
from .models import Exercise, GradeResult

_RUNNER = str(Path(__file__).with_name("_runner.py"))

BROKEN_EXERCISE = ("This exercise is broken (its expected results are missing). "
                   "Use :s to skip it.")


def short_name(target: str) -> str:
    """'statistics.median' -> 'median', 'str.join' -> 'join', 'zip' -> 'zip'."""
    return target.rpartition(".")[2]


def _uses_target(tree: ast.AST, target: str) -> bool:
    """Fast static check: is the target name mentioned at all? (friendly early feedback)

    `target` may be dotted ("str.join", "statistics.median"): then `x.join(...)`,
    `statistics.median(...)` and `from statistics import median; median(...)` all count.
    """
    name = short_name(target)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
    return False


def run_in_sandbox(code: str, function_name: str, target: str,
                   inputs: List[list], timeout: float = 5.0,
                   allowed_imports: Optional[Iterable[str]] = None) -> dict:
    """Run code in a child process. Returns the runner's JSON dict, or a synthetic one.

    target: the full name ("zip", "str.join", "statistics.median").
    allowed_imports: extra modules for this run; the runner only accepts ones in SAFE_MODULES.
    """
    job = json.dumps({"code": code, "function_name": function_name, "target": target,
                      "inputs": inputs, "allowed_imports": sorted(allowed_imports or ())})
    try:
        proc = subprocess.run([sys.executable, "-I", _RUNNER], input=job,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "message": "Your code crashed or used too much memory."}


def values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            return math.isclose(a, b, rel_tol=0.0, abs_tol=1e-9)
        except TypeError:
            return False
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k]) for k in a)
    return a == b


def check_exercise(ex: Exercise) -> List[str]:
    """Problems with an exercise's data, without running anything. [] means it looks fine."""
    problems = []
    for name in ("id", "target_function", "question_text", "function_name",
                 "function_signature", "reference_solution"):
        if not isinstance(getattr(ex, name), str) or not getattr(ex, name).strip():
            problems.append(f"'{name}' is missing or empty")
    if ex.difficulty not in (1, 2, 3):
        problems.append(f"difficulty must be 1, 2 or 3, not {ex.difficulty!r}")
    if isinstance(ex.function_name, str) and not ex.function_name.isidentifier():
        problems.append(f"function_name {ex.function_name!r} is not a valid name")
    if not isinstance(ex.test_inputs, list) or not ex.test_inputs:
        problems.append("there are no test inputs")
    elif not all(isinstance(t, list) for t in ex.test_inputs):
        problems.append("every test input must be a list of arguments")
    if not isinstance(ex.expected_outputs, list) or (
            isinstance(ex.test_inputs, list)
            and len(ex.expected_outputs) != len(ex.test_inputs)):
        problems.append("expected_outputs must have one value per test input")
    bad = [m for m in (ex.allowed_imports or []) if m not in SAFE_MODULES | ALLOWED_IMPORTS]
    if not isinstance(ex.allowed_imports, list) or bad:
        problems.append(f"allowed_imports has modules that are not allowed: {bad}")
    if ex.source not in ("preset", "ai"):
        problems.append(f"source must be 'preset' or 'ai', not {ex.source!r}")
    return problems


def grade(user_code: str, exercise: Exercise, timeout: float = 5.0) -> GradeResult:
    tests, expected = exercise.test_inputs, exercise.expected_outputs
    # P0-1: an exercise without expected results must never "pass everything".
    if not tests or not isinstance(expected, list) or len(expected) != len(tests):
        return GradeResult(False, "error", BROKEN_EXERCISE)

    try:
        tree = ast.parse(user_code)
    except SyntaxError as e:
        return GradeResult(False, "error", f"Syntax error on line {e.lineno}: {e.msg}")

    target = exercise.target_function
    if not _uses_target(tree, target):
        return GradeResult(False, "target_not_used",
                           f"This exercise is about '{target}'. Try using it.")

    res = run_in_sandbox(user_code, exercise.function_name, target, tests, timeout,
                         allowed_imports=exercise.allowed_imports)

    if res["status"] == "timeout":
        return GradeResult(False, "timeout", "Your code ran too long (possible infinite loop).")
    if res["status"] == "error":
        return GradeResult(False, "error", res.get("message", "Unknown error."))

    outputs = res.get("outputs", [])
    if len(outputs) != len(expected):
        return GradeResult(False, "error", "Your code did not return a result for every test.")
    for out, want in zip(outputs, expected):
        if "error" in out or not values_equal(out["value"], want):
            return GradeResult(False, "wrong_output", "Not quite right yet.")

    # -1 = this kind of target can't be counted (methods like str.join): static check only.
    if res["target_calls"] == 0:
        return GradeResult(False, "target_not_used",
                           f"Your function never actually called '{target}'.")
    return GradeResult(True, "ok", "Correct!")
