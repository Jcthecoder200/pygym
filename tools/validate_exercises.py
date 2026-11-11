"""Content owner's tool.

  python tools/validate_exercises.py          check every exercise (also: expected_outputs must exist)
  python tools/validate_exercises.py --fill   recompute expected_outputs from reference solutions
  python tools/validate_exercises.py --levels report the exercises whose reference solution does
                                              not follow the level rules (pygym/ai/levels.py);
                                              report only, always exits 0

An exercise fails when a required field is missing, an id is used twice, the difficulty is not 1-3,
`expected_outputs` is missing or has a different length than `test_inputs` (normal mode only; --fill
recomputes it), the reference solution crashes, or the reference solution does not pass `grade()`.

The level rules (beginner: no if/else or loop; intermediate: a loop and a condition; advanced:
plus a data structure or algorithm) are enforced on AI-generated exercises only. Hand-written
exercises are reviewed by people, so --levels only lists the ones that differ, for review.
"""
import ast
import builtins
import json
import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pygym.grader import grade, run_in_sandbox  # noqa: E402
from pygym.models import Exercise  # noqa: E402
from pygym.ai import levels  # noqa: E402

PATH = Path(__file__).resolve().parents[1] / "src" / "pygym" / "data" / "exercises.json"
REQUIRED = ["id", "target_function", "difficulty", "question_text", "function_name",
            "function_signature", "reference_solution", "test_inputs", "hint"]
TEXT_FIELDS = ["id", "target_function", "question_text", "function_name",
               "function_signature", "reference_solution", "hint"]
MIN_TESTS = 3


def field_problems(d: dict, need_expected: bool = True) -> List[str]:
    """Cheap checks on one exercise dict (no code is run). Empty list = fine."""
    problems = []
    missing = [k for k in REQUIRED if k not in d]
    if missing:
        return [f"missing fields: {', '.join(missing)}"]
    for key in TEXT_FIELDS:
        if not isinstance(d[key], str) or not d[key].strip():
            problems.append(f"'{key}' must be a non-empty string")
    if problems:
        return problems
    level = d["difficulty"]
    if isinstance(level, bool) or level not in (1, 2, 3):
        problems.append(f"difficulty must be 1, 2 or 3 (got {level!r})")
    if not hasattr(builtins, d["target_function"]):
        problems.append(f"target_function '{d['target_function']}' is not a built-in")
    name = d["function_name"]
    if not name.isidentifier() or hasattr(builtins, name):
        problems.append(f"function_name '{name}' must be an identifier and not a built-in name")
    if not d["function_signature"].startswith(f"def {name}("):
        problems.append(f"function_signature must start with 'def {name}('")
    if d.get("source", "preset") not in ("preset", "ai"):
        problems.append(f"source must be 'preset' or 'ai' (got {d['source']!r})")
    tests = d["test_inputs"]
    if not isinstance(tests, list) or len(tests) < MIN_TESTS:
        problems.append(f"need at least {MIN_TESTS} test_inputs")
    elif not all(isinstance(t, list) for t in tests):
        problems.append("every test input must be a list of arguments")
    else:
        try:
            json.dumps(tests)
        except (TypeError, ValueError):
            problems.append("test_inputs must be JSON-serialisable")
    if need_expected:
        expected = d.get("expected_outputs")
        if not isinstance(expected, list) or not isinstance(tests, list):
            problems.append("expected_outputs is missing (run with --fill)")
        elif len(expected) != len(tests):
            problems.append(f"expected_outputs has {len(expected)} items but test_inputs has "
                            f"{len(tests)} (run with --fill)")
    return problems


def level_report(data: List[dict]) -> List[Tuple[str, int, str]]:
    """(id, level, what is missing) for every exercise whose reference solution does not follow
    the rules of its level (levels.check_level; the words come from levels.gaps)."""
    found = []
    for d in data:
        label, level = d.get("id", "?"), d.get("difficulty")
        try:
            tree = ast.parse(d.get("reference_solution", ""))
        except SyntaxError as e:
            found.append((label, level, f"reference solution does not parse ({e.msg})"))
            continue
        fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == d.get("function_name")), None)
        if fn is None:
            found.append((label, level, "reference solution does not define function_name"))
            continue
        if levels.check_level(tree, fn, level):
            found.append((label, level, "; ".join(levels.gaps(tree, fn, level))))
    return found


def print_level_report() -> int:
    data = json.loads(PATH.read_text(encoding="utf-8"))
    found = level_report(data)
    for label, level, problem in found:
        name = levels.LEVEL_NAMES.get(level, "?")
        print(f"LEVEL {label} ({level} {name}): {problem}")
    print(f"{len(found)} of {len(data)} exercises do not follow the level rules "
          "(report only: hand-written exercises are reviewed by people; see docs/AI_MODE.md).")
    return 0


def main() -> int:
    if "--levels" in sys.argv:
        return print_level_report()
    fill = "--fill" in sys.argv
    data = json.loads(PATH.read_text(encoding="utf-8"))
    bad, seen = 0, set()
    for d in data:
        label = d.get("id", "?")
        problems = field_problems(d, need_expected=not fill)
        if label in seen:
            problems.append("duplicate id")
        seen.add(label)
        if problems:
            print(f"FAIL {label}: {'; '.join(problems)}")
            bad += 1
            continue
        res = run_in_sandbox(d["reference_solution"], d["function_name"],
                             d["target_function"], d["test_inputs"], timeout=10)
        if res["status"] != "ok" or any("error" in o for o in res["outputs"]):
            print(f"FAIL {label}: reference solution crashed: {res}")
            bad += 1
            continue
        if fill:
            d["expected_outputs"] = [o["value"] for o in res["outputs"]]
        ex = Exercise.from_dict(d)
        verdict = grade(ex.reference_solution, ex)
        print(f"{'ok  ' if verdict.passed else 'FAIL'} {label}: {verdict.reason}")
        bad += 0 if verdict.passed else 1
    if fill:
        PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("expected_outputs written.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
