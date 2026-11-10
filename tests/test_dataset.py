"""The bundled exercises are valid, unique, solvable and cover enough built-ins."""
import builtins
import importlib.util
import json
from collections import defaultdict
from pathlib import Path

import pytest

from pygym import loader
from pygym.grader import grade
from pygym.models import Exercise

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "src" / "pygym" / "data" / "exercises.json"

# Built-ins that must have a hand-written exercise for every level.
FULL_COVERAGE = ["zip", "sum", "sorted", "map", "filter", "enumerate", "len", "max", "min",
                 "range", "any", "all"]
# The first exercises were written before question texts had to name their target.
OLD_QUESTIONS = {"zip-01", "zip-02", "sum-01", "sum-02", "sorted-01"}


def _validator():
    spec = importlib.util.spec_from_file_location("validate_exercises",
                                                  ROOT / "tools" / "validate_exercises.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RAW = json.loads(DATA.read_text(encoding="utf-8"))
EXERCISES = loader.load_all()
IDS = [e.id for e in EXERCISES]


def test_ids_are_unique():
    assert len(IDS) == len(set(IDS))


def test_ids_follow_target_dash_number():
    for e in EXERCISES:
        prefix, _, number = e.id.rpartition("-")
        assert prefix == e.target_function and number.isdigit(), e.id


@pytest.mark.parametrize("raw", RAW, ids=IDS)
def test_fields_are_valid(raw):
    assert _validator().field_problems(raw) == []


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_expected_outputs_match_tests(ex):
    assert len(ex.expected_outputs) == len(ex.test_inputs) >= 3


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_reference_solution_passes_grade(ex):
    result = grade(ex.reference_solution, ex)
    assert result.passed, (ex.id, result)


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_code_that_skips_the_target_is_rejected(ex):
    code = f"{ex.function_signature}\n    return None\n"
    assert grade(code, ex).reason == "target_not_used"


@pytest.mark.parametrize("ex", EXERCISES, ids=IDS)
def test_target_is_a_real_builtin_and_text_names_it(ex):
    assert hasattr(builtins, ex.target_function)
    assert ex.source == "preset"
    assert ex.hint.strip()
    if ex.id not in OLD_QUESTIONS:
        assert ex.target_function in ex.question_text, ex.id


def test_every_target_has_at_least_two_levels():
    levels = defaultdict(set)
    for e in EXERCISES:
        levels[e.target_function].add(e.difficulty)
    for target, found in levels.items():
        assert len(found) >= 2, f"{target} has only levels {sorted(found)}"


def test_main_functions_cover_all_three_levels():
    levels = defaultdict(set)
    for e in EXERCISES:
        levels[e.target_function].add(e.difficulty)
    for target in FULL_COVERAGE:
        assert levels[target] == {1, 2, 3}, target


def test_at_least_twelve_built_ins_are_covered():
    assert len(loader.available_functions()) >= 12


def test_sandbox_cannot_run_these_targets():
    unsupported = {"input", "open", "print", "exec", "eval", "compile", "breakpoint", "help"}
    assert not unsupported & set(loader.available_functions())


# ---- the validator itself ------------------------------------------------------------------

def _good():
    return json.loads(json.dumps(RAW[0]))


def test_validator_accepts_a_good_exercise():
    assert _validator().field_problems(_good()) == []


def test_validator_fails_missing_expected_outputs():
    d = _good()
    del d["expected_outputs"]
    assert any("expected_outputs" in p for p in _validator().field_problems(d))


def test_validator_fails_empty_expected_outputs():
    d = _good()
    d["expected_outputs"] = []
    assert any("expected_outputs" in p for p in _validator().field_problems(d))


def test_validator_fails_wrong_length_of_expected_outputs():
    d = _good()
    d["expected_outputs"] = d["expected_outputs"][:-1]
    assert any("expected_outputs" in p for p in _validator().field_problems(d))


def test_validator_skips_expected_outputs_when_filling():
    d = _good()
    del d["expected_outputs"]
    assert _validator().field_problems(d, need_expected=False) == []


@pytest.mark.parametrize("level", [0, 4, "2", None, True])
def test_validator_fails_bad_difficulty(level):
    d = _good()
    d["difficulty"] = level
    assert any("difficulty" in p for p in _validator().field_problems(d))


def test_validator_fails_missing_required_fields():
    d = _good()
    del d["question_text"]
    assert any("missing" in p for p in _validator().field_problems(d))


def test_validator_fails_unknown_target_and_builtin_function_name():
    d = _good()
    d["target_function"] = "not_a_builtin"
    assert any("target_function" in p for p in _validator().field_problems(d))
    d = _good()
    d["function_name"] = "sum"
    d["function_signature"] = "def sum(keys, values):"
    assert any("function_name" in p for p in _validator().field_problems(d))


def test_validator_fails_signature_that_does_not_match_the_name():
    d = _good()
    d["function_signature"] = "def other(keys, values):"
    assert any("function_signature" in p for p in _validator().field_problems(d))


def test_validator_main_reports_duplicate_ids(tmp_path, monkeypatch, capsys):
    module = _validator()
    data = [json.loads(json.dumps(RAW[0])), json.loads(json.dumps(RAW[0]))]
    path = tmp_path / "exercises.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "PATH", path)
    monkeypatch.setattr("sys.argv", ["validate_exercises.py"])
    assert module.main() == 1
    assert "duplicate id" in capsys.readouterr().out


def test_empty_expected_outputs_cannot_make_wrong_code_pass():
    """The grader must never accept anything when there is nothing to compare with."""
    ex = Exercise.from_dict(_good())
    ex.expected_outputs = []
    assert not grade("def combine(keys, values):\n    return dict(zip(keys, values))", ex).passed
