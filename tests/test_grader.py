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


# ----- AI mode support: P0-1, allowed_imports, dotted targets --------------------------------

from pygym import grader  # noqa: E402
from pygym.models import Exercise  # noqa: E402


def _ex(target, reference, inputs, outputs, name="f", imports=()):
    return Exercise(id="t-1", target_function=target, difficulty=1, question_text="q",
                    function_name=name, function_signature=f"def {name}(x):",
                    reference_solution=reference, test_inputs=inputs, expected_outputs=outputs,
                    allowed_imports=list(imports))


MEDIAN = "import statistics\ndef f(x):\n    return statistics.median(x)"


@pytest.mark.parametrize("outputs", [[], [2], [2, 4.5, 9]])
def test_p0_1_missing_expected_outputs_never_pass(outputs):
    ex = _ex("statistics.median", MEDIAN, [[[1, 2, 3]], [[4, 5]]], outputs,
             imports=["statistics"])
    r = grade("def f(x):\n    return 'anything'  # statistics.median", ex)
    assert not r.passed and r.reason == "error" and "broken" in r.message
    assert grader.check_exercise(ex)


def test_p0_1_no_tests_never_pass():
    r = grade(MEDIAN, _ex("statistics.median", MEDIAN, [], [], imports=["statistics"]))
    assert not r.passed and "broken" in r.message


def test_allowed_imports_extend_the_sandbox():
    ex = _ex("statistics.median", MEDIAN, [[[1, 2, 3]], [[4, 5]]], [2, 4.5],
             imports=["statistics"])
    assert grade(MEDIAN, ex).passed
    ex.allowed_imports = []
    r = grade(MEDIAN, ex)
    assert not r.passed and "not allowed" in r.message


def test_allowed_imports_can_not_unlock_unsafe_modules():
    code = "import os\ndef f(x):\n    return len(x)"
    ex = _ex("len", code, [[[1]], [[1, 2]]], [1, 2], imports=["os"])
    r = grade(code, ex)
    assert not r.passed and "not allowed" in r.message
    assert any("os" in p for p in grader.check_exercise(ex))


def test_short_name():
    assert grader.short_name("statistics.median") == "median"
    assert grader.short_name("str.join") == "join" and grader.short_name("zip") == "zip"


@pytest.mark.parametrize("code", [
    MEDIAN,
    "from statistics import median\ndef f(x):\n    return median(x)",
    "import statistics as st\ndef f(x):\n    return st.median(x)",
])
def test_module_target_called_in_every_import_style(code):
    ex = _ex("statistics.median", MEDIAN, [[[1, 2, 3]], [[4, 5]]], [2, 4.5],
             imports=["statistics"])
    assert grade(code, ex).passed


def test_module_target_dead_code_is_rejected():
    ex = _ex("statistics.median", MEDIAN, [[[1, 2, 3]], [[4, 5]]], [2, 4.5],
             imports=["statistics"])
    code = ("import statistics\ndef f(x):\n    if False:\n        statistics.median(x)\n"
            "    s = sorted(x)\n    n = len(s)\n"
            "    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2")
    r = grade(code, ex)
    assert not r.passed and r.reason == "target_not_used" and "statistics.median" in r.message


def test_module_target_static_check_names_the_full_target():
    ex = _ex("statistics.median", MEDIAN, [[[1, 2, 3]], [[4, 5]]], [2, 4.5],
             imports=["statistics"])
    r = grade("def f(x):\n    return sorted(x)[len(x) // 2]", ex)
    assert r.reason == "target_not_used" and "'statistics.median'" in r.message


def test_module_class_target_is_counted():
    ref = "import itertools\ndef f(x):\n    return list(itertools.chain(*x))"
    ex = _ex("itertools.chain", ref, [[[[1], [2, 3]]], [[[], [4]]]], [[1, 2, 3], [4]],
             imports=["itertools"])
    assert grade(ref, ex).passed
    via_attr = "from itertools import chain\ndef f(x):\n    return list(chain.from_iterable(x))"
    assert grade(via_attr, ex).passed
    dead = ("import itertools\ndef f(x):\n    if False:\n        itertools.chain(x)\n"
            "    return [i for part in x for i in part]")
    assert grade(dead, ex).reason == "target_not_used"


def test_method_target_static_check_only():
    # str.join can't be wrapped (built-in types are read-only): only the static check applies
    ref = "def f(x):\n    return '-'.join(x)"
    ex = _ex("str.join", ref, [[["a", "b"]], [[]]], ["a-b", ""])
    assert grade(ref, ex).passed
    assert grade("def f(x):\n    return str.join('-', x)", ex).passed
    r = grade("def f(x):\n    out = ''\n    for i, w in enumerate(x):\n"
              "        out += ('-' if i else '') + w\n    return out", ex)
    assert r.reason == "target_not_used" and "'str.join'" in r.message


def test_check_exercise_accepts_the_bundled_exercises():
    for ex in loader.load_all():
        assert grader.check_exercise(ex) == [], ex.id
