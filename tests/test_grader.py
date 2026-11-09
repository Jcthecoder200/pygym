import pytest

from pygym import loader
from pygym.grader import grade


@pytest.fixture
def zip01():
    return next(e for e in loader.load_all() if e.id == "zip-01")


def test_reference_passes(zip01):
    assert grade(zip01.reference_solution, zip01).passed


def test_loop_without_zip_is_rejected(zip01):
    code = ("def combine(keys, values):\n    d = {}\n"
            "    for i in range(min(len(keys), len(values))):\n        d[keys[i]] = values[i]\n    return d")
    r = grade(code, zip01)
    assert not r.passed and r.reason == "target_not_used"


def test_zip_plus_loop_is_ok(zip01):
    code = ("def combine(keys, values):\n    d = {}\n"
            "    for k, v in zip(keys, values):\n        d[k] = v\n    return d")
    assert grade(code, zip01).passed


def test_dead_code_zip_is_rejected(zip01):
    code = ("def combine(keys, values):\n    if False:\n        zip(keys, values)\n"
            "    return {keys[i]: values[i] for i in range(min(len(keys), len(values)))}")
    r = grade(code, zip01)
    assert not r.passed and r.reason == "target_not_used"


def test_wrong_output(zip01):
    r = grade("def combine(keys, values):\n    return dict(zip(keys, keys))", zip01)
    assert r.reason == "wrong_output"


def test_infinite_loop_times_out(zip01):
    code = "def combine(keys, values):\n    zip\n    while True:\n        pass"
    assert grade(code, zip01, timeout=1.5).reason == "timeout"


def test_syntax_error(zip01):
    assert grade("def combine(:", zip01).reason == "error"


def test_forbidden_import(zip01):
    code = "import os\ndef combine(keys, values):\n    return dict(zip(keys, values))"
    r = grade(code, zip01)
    assert not r.passed and "not allowed" in r.message


def test_missing_function(zip01):
    r = grade("x = zip([1],[2])", zip01)
    assert r.reason == "error"


def test_print_does_not_break_grading(zip01):
    code = "def combine(keys, values):\n    print('hi')\n    return dict(zip(keys, values))"
    assert grade(code, zip01).passed


def test_unavailable():
    with pytest.raises(loader.ExerciseUnavailable):
        loader.pick("not_a_builtin")
