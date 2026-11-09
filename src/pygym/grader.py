"""Checks a user's code against an Exercise. Never runs user code in the main process."""
from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, List

from .models import Exercise, GradeResult

_RUNNER = str(Path(__file__).with_name("_runner.py"))


def _uses_target(tree: ast.AST, target: str) -> bool:
    """Fast static check: is the target name mentioned at all? (friendly early feedback)"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == target:
            return True
        if isinstance(node, ast.Attribute) and node.attr == target:
            return True
    return False


def run_in_sandbox(code: str, function_name: str, target: str,
                   inputs: List[list], timeout: float = 5.0) -> dict:
    """Run code in a child process. Returns the runner's JSON dict, or a synthetic one."""
    job = json.dumps({"code": code, "function_name": function_name,
                      "target": target, "inputs": inputs})
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


def grade(user_code: str, exercise: Exercise, timeout: float = 5.0) -> GradeResult:
    try:
        tree = ast.parse(user_code)
    except SyntaxError as e:
        return GradeResult(False, "error", f"Syntax error on line {e.lineno}: {e.msg}")

    if not _uses_target(tree, exercise.target_function):
        return GradeResult(False, "target_not_used",
                           f"This exercise is about '{exercise.target_function}'. Try using it.")

    res = run_in_sandbox(user_code, exercise.function_name, exercise.target_function,
                         exercise.test_inputs, timeout)

    if res["status"] == "timeout":
        return GradeResult(False, "timeout", "Your code ran too long (possible infinite loop).")
    if res["status"] == "error":
        return GradeResult(False, "error", res.get("message", "Unknown error."))

    for out, expected in zip(res["outputs"], exercise.expected_outputs):
        if "error" in out or not values_equal(out["value"], expected):
            return GradeResult(False, "wrong_output", "Not quite right yet.")

    if res["target_calls"] == 0:
        return GradeResult(False, "target_not_used",
                           f"Your function never actually called '{exercise.target_function}'.")
    return GradeResult(True, "ok", "Correct!")
