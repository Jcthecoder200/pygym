"""Checks a user's code against an Exercise. Never runs user code in the main process."""
from __future__ import annotations

import ast
import math
from typing import Any, Dict, Iterable, List, Optional

from . import sandbox
from ._runner import ALLOWED_IMPORTS, import_permitted
from .models import NOT_RUN, TIMED_OUT, Exercise, GradeResult, TestResult

BROKEN_EXERCISE = ("This exercise is broken (its expected results are missing). "
                   "Use :s to skip it.")


def sandbox_level() -> str:
    """"bwrap" | "sandbox-exec" | "audit-hook": how code is isolated on this computer."""
    return sandbox.level()


def short_name(target: str) -> str:
    """'statistics.median' -> 'median', 'str.join' -> 'join', 'zip' -> 'zip'."""
    return target.rpartition(".")[2]


def _uses_target(tree: ast.AST, target: str) -> bool:
    """Fast static check: is the target name mentioned at all? (friendly early feedback)

    `target` may be dotted ("str.join", "statistics.median"): then `x.join(...)`,
    `statistics.median(...)`, `import numpy as np; np.cumsum(...)`, `arr.cumsum()` and
    `from statistics import median; median(...)` all count.
    """
    name = short_name(target)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == name:
            return True
        if isinstance(node, ast.Attribute) and node.attr == name:
            return True
    return False


def _module_aliases(tree: ast.AST, top: str) -> set:
    """Names that refer to the target's library: {"numpy", "np"} for `import numpy as np`."""
    names = {top}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] == top:
                    names.add(a.asname or top)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == top:
            for a in node.names:
                if a.asname or a.name:
                    names.add(a.asname or a.name)
    return names


def method_form_used(tree: ast.AST, target: str) -> bool:
    """`arr.cumsum()` for the target "numpy.cumsum": a call of the method with the same name on a
    value, not on the library. Only for libraries outside the standard library (numpy, pandas):
    their values have methods that mirror the library functions, and those C methods can't be
    counted at run time."""
    owner, _, name = target.rpartition(".")
    top = owner.split(".")[0]
    if not owner or top in ALLOWED_IMPORTS or sandbox._is_stdlib(top):
        return False
    aliases = _module_aliases(tree, top)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == name):
            base = node.func.value
            while isinstance(base, ast.Attribute):
                base = base.value
            if not (isinstance(base, ast.Name) and base.id in aliases):
                return True
    return False


def target_called(target_calls, tree: ast.AST, target: str) -> bool:
    """The runtime check: -1 = can't be counted (static check only); 0 = never called, except
    for the method form of a library function (see method_form_used)."""
    if target_calls != 0:
        return True
    return method_form_used(tree, target)


def run_in_sandbox(code: str, function_name: str, target: str,
                   inputs: List[list], timeout: float = 5.0,
                   allowed_imports: Optional[Iterable[str]] = None, *,
                   files: Optional[Dict[str, str]] = None,
                   http_fixtures: Optional[Dict[str, Any]] = None,
                   level: Optional[str] = None) -> dict:
    """Run code in a child process in a new temporary folder. Returns the runner's JSON dict,
    or a synthetic one ({"status": "timeout"} / {"status": "error", "message": ...}).

    target: the full name ("zip", "str.join", "statistics.median", "numpy.cumsum").
    allowed_imports: extra modules for this run (the runner applies the import policy again).
    files: text files written into the folder before every test; open() is allowed then.
    http_fixtures: answers of the local test server; the code sees its address as BASE_URL.
    level: force a sandbox level (tests); default sandbox.level().
    """
    job = {"code": code, "function_name": function_name, "target": target, "inputs": inputs,
           "allowed_imports": sorted(allowed_imports or ()), "files": files or {},
           "http_fixtures": http_fixtures or {}, "allow_open": bool(files)}
    return sandbox.run(job, timeout, level)


def values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        try:
            if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
                return True                   # NaN == NaN when grading
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
    target = ex.target_function if isinstance(ex.target_function, str) else ""
    if not isinstance(ex.allowed_imports, list):
        problems.append("allowed_imports must be a list")
    else:
        bad = [m for m in ex.allowed_imports
               if not isinstance(m, str) or not import_permitted(m, target)]
        if bad:
            problems.append(f"allowed_imports has modules that are not allowed: {bad}")
    problems += sandbox.check_files(ex.files)
    problems += sandbox.check_fixtures(ex.http_fixtures)
    if ex.source not in ("preset", "ai"):
        problems.append(f"source must be 'preset' or 'ai', not {ex.source!r}")
    return problems


def _blocked_note(res: dict) -> str:
    blocked = res.get("blocked") or []
    return f" (pygym {blocked[0]})" if blocked else ""


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
    # The code runs even when the target is missing: the rows show what it does (the verdict
    # stays "use the target").
    res = run_in_sandbox(user_code, exercise.function_name, target, tests, timeout,
                         allowed_imports=exercise.allowed_imports, files=exercise.files,
                         http_fixtures=exercise.http_fixtures)
    status = res.get("status")
    outputs = res.get("outputs") or []
    complete = status == "ok" and len(outputs) == len(expected)
    rows = rows_from_run(tests, expected, res) if complete or status == "timeout" else []
    setup = str(res.get("setup_stdout") or "")

    def result(passed: bool, reason: str, message: str) -> GradeResult:
        return GradeResult(passed, reason, message, tests=rows, setup_stdout=setup)

    if not _uses_target(tree, target):
        return result(False, "target_not_used", f"This exercise is about '{target}'. Try using it.")
    if status == "timeout":
        return result(False, "timeout", _timeout_message(res))
    if status == "error":
        return result(False, "error", res.get("message", "Unknown error."))
    if not complete:
        return result(False, "error", "Your code did not return a result for every test.")
    passed = sum(1 for r in rows if r.passed)
    if passed < len(rows):
        word = "test" if len(rows) == 1 else "tests"
        return result(False, "wrong_output", f"Not quite right yet: {passed} of {len(rows)} "
                                             f"{word} passed." + _blocked_note(res))

    # -1 = this kind of target can't be counted (methods like str.join): static check only.
    if not target_called(res.get("target_calls"), tree, target):
        return result(False, "target_not_used",
                      f"Your function never actually called '{target}'.")
    return result(True, "ok", "Correct!")


def rows_from_run(tests: List[list], expected: List[Any], res: dict) -> List[TestResult]:
    """One TestResult per test from the runner's reply. After a timeout: the finished tests, the
    one that was running (TIMED_OUT) and the ones after it (NOT_RUN); no rows when the run was
    stopped while the code was loading or nobody knows where."""
    outputs = res.get("outputs") or []
    running = res.get("running") if res.get("status") == "timeout" else None
    if res.get("status") == "timeout" and not (isinstance(running, int) and running >= 0):
        return []
    rows: List[TestResult] = []
    for i, (args, want) in enumerate(zip(tests, expected)):
        out = outputs[i] if i < len(outputs) and isinstance(outputs[i], dict) else None
        if out is None:
            error = TIMED_OUT if i == running else NOT_RUN
            printed = str(res.get("stdout") or "") if i == running else ""
            rows.append(TestResult(i + 1, args, want, error=error, stdout=printed))
        elif "error" in out:
            rows.append(TestResult(i + 1, args, want, error=str(out["error"]),
                                   stdout=str(out.get("stdout") or "")))
        else:
            got = out.get("value")
            rows.append(TestResult(i + 1, args, want, got=got, passed=values_equal(got, want),
                                   stdout=str(out.get("stdout") or "")))
    return rows


def _timeout_message(res: dict) -> str:
    """Short enough for the editor's message line; the rows show the call."""
    running = res.get("running")
    if running == -1:
        return "Your code ran too long (possible infinite loop) while loading (top-level code)."
    if isinstance(running, int) and running >= 0:
        return f"Your code ran too long (possible infinite loop) in test {running + 1}."
    return "Your code ran too long (possible infinite loop)."
