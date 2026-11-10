"""ai.validate: a model reply -> a checked Exercise. The reference only runs in the sandbox."""
import json

import pytest

from pygym import grader
from pygym.ai.targets import resolve
from pygym.ai.validate import InvalidExercise, to_exercise

ZIP = resolve("zip")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))


def reply(**overrides):
    d = {
        "question_text": "Write pair_up(names, scores) that pairs each name with its score "
                         "using zip and returns a list of pairs.",
        "function_name": "pair_up",
        "function_signature": "def pair_up(names, scores):",
        "reference_solution": "def pair_up(names, scores):\n    return list(zip(names, scores))",
        "tests": [{"names": ["ana", "bo"], "scores": [3, 7]}, {"names": [], "scores": []},
                  {"names": ["x"], "scores": [1, 2]}, {"names": ["a", "b", "c"], "scores": [1, 2, 3]}],
        "hint": "zip pairs items from two lists.",
    }
    d.update(overrides)
    return json.dumps(d)


def check(raw, info=ZIP, level=2, **kw):
    return to_exercise(raw, info, level, "fake:fake", **kw)


def rejected(raw, info=ZIP, **kw):
    with pytest.raises(InvalidExercise) as e:
        check(raw, info, **kw)
    return e.value


def test_good_reply():
    ex = check(reply())
    assert ex.source == "ai" and ex.generator == "fake:fake" and ex.difficulty == 2
    assert ex.target_function == "zip" and ex.function_name == "pair_up"
    assert ex.function_signature == "def pair_up(names, scores):"
    assert ex.test_inputs[0] == [["ana", "bo"], [3, 7]]
    assert ex.expected_outputs == [[["ana", 3], ["bo", 7]], [], [["x", 1]],
                                   [["a", 1], ["b", 2], ["c", 3]]]
    assert ex.id.startswith("ai-zip-2-") and ex.allowed_imports == []
    assert grader.check_exercise(ex) == []
    assert grader.grade(ex.reference_solution, ex).passed


def test_expected_outputs_come_from_the_reference_not_the_model():
    ex = check(reply(expected_outputs=["nonsense", 1, 2, 3]))
    assert ex.expected_outputs[0] == [["ana", 3], ["bo", 7]]


def test_id_is_stable():
    assert check(reply()).id == check(reply()).id
    other = reply(reference_solution="def pair_up(names, scores):\n"
                                     "    return [list(p) for p in zip(names, scores)]")
    assert check(other).id != check(reply()).id


@pytest.mark.parametrize("raw, repairable", [("not json at all {", False), ("[1, 2]", False)])
def test_invalid_json(raw, repairable):
    e = rejected(raw)
    assert "JSON" in e.problem and e.repairable is repairable


@pytest.mark.parametrize("field, value, words", [
    ("question_text", "", "question_text"),
    ("reference_solution", "", "reference_solution"),
    ("tests", "nope", "tests"),
    ("tests", [{"names": ["a"], "scores": [1]}], "tests"),
])
def test_schema_violations(field, value, words):
    e = rejected(reply(**{field: value}))
    assert words in e.problem and e.repairable


def test_syntax_error_rejected():
    e = rejected(reply(reference_solution="def pair_up(names, scores:\n    return zip(names)"))
    assert "syntax error" in e.problem


def test_one_line_import_is_fixed():
    # a real probe failure: "import statistics; def f(...)" on one line
    raw = reply(function_name="middle", question_text="Return the median of the readings.",
                reference_solution="import statistics; def middle(readings):\n"
                                   "    return statistics.median(readings)",
                tests=[{"readings": [3, 1, 2]}, {"readings": [4, 1]}, {"readings": [7]}])
    ex = check(raw, resolve("statistics.median"), 1)
    assert ex.reference_solution.startswith("import statistics\ndef middle")
    assert ex.expected_outputs == [2, 2.5, 7]
    assert ex.allowed_imports == ["statistics"]


def test_missing_module_import_is_added():
    raw = reply(function_name="middle", question_text="Return the median.",
                reference_solution="def middle(xs):\n    return statistics.median(xs)",
                tests=[[[3, 1, 2]], [[4, 1]], [[7, 9, 8, 1]]])
    ex = check(raw, resolve("statistics.median"), 1)
    assert ex.reference_solution.startswith("import statistics\n")


def test_function_name_taken_from_the_code_when_it_disagrees():
    ex = check(reply(function_name="pairUp"))
    assert ex.function_name == "pair_up"


def test_function_name_must_not_hide_a_builtin():
    raw = reply(function_name="zip", reference_solution="def zip(a, b):\n    return 1")
    assert "hides" in rejected(raw).problem


def test_signature_rebuilt_from_the_reference():
    ex = check(reply(function_signature="def something_else(x):"))
    assert ex.function_signature == "def pair_up(names, scores):"


def test_reference_must_define_the_function_at_top_level():
    raw = reply(reference_solution="class A:\n    def pair_up(self, a, b):\n"
                                   "        return list(zip(a, b))\n"
                                   "def helper(a):\n    return a\n"
                                   "def other(a):\n    return a")
    assert "does not define" in rejected(raw).problem


def test_reference_must_use_the_target():
    # a real probe failure: zip_longest instead of zip
    raw = reply(reference_solution="import itertools\ndef pair_up(names, scores):\n"
                                   "    return list(itertools.zip_longest(names, scores))")
    assert "must call" in rejected(raw).problem


def test_dead_code_target_is_rejected():
    raw = reply(reference_solution="def pair_up(names, scores):\n    if False:\n"
                                   "        zip(names, scores)\n"
                                   "    return [[n, s] for n, s in map(lambda a, b: (a, b), names, scores)]")
    e = rejected(raw)
    assert "really call" in e.problem and e.repairable


def test_print_instead_of_return_is_rejected():
    # a real probe failure: print() instead of return
    raw = reply(reference_solution="def pair_up(names, scores):\n    print(list(zip(names, scores)))")
    assert "print" in rejected(raw).problem


def test_missing_return_is_rejected():
    raw = reply(reference_solution="def pair_up(names, scores):\n    x = list(zip(names, scores))")
    assert "return" in rejected(raw).problem


def test_forbidden_import_is_rejected():
    raw = reply(reference_solution="import os\ndef pair_up(names, scores):\n"
                                   "    return list(zip(names, scores))")
    assert "os" in rejected(raw).problem


def test_reference_that_crashes_is_rejected():
    raw = reply(reference_solution="def pair_up(names, scores):\n"
                                   "    return list(zip(names, scores))[10][0]")
    e = rejected(raw)
    assert "crashed" in e.problem and "IndexError" in e.problem and e.repairable


def test_reference_that_crashes_on_import_is_rejected():
    raw = reply(reference_solution="1 / 0\ndef pair_up(names, scores):\n"
                                   "    return list(zip(names, scores))")
    assert "ZeroDivisionError" in rejected(raw).problem


def test_tests_that_crash_are_dropped():
    raw = reply(tests=[{"names": ["a"], "scores": [1]}, {"names": ["a", "b"], "scores": [1, 2]},
                       {"names": [], "scores": []}, {"names": 5, "scores": 6}])
    ex = check(raw)
    assert len(ex.test_inputs) == 3 and [5, 6] not in ex.test_inputs


def test_reference_that_loops_is_rejected():
    raw = reply(reference_solution="def pair_up(names, scores):\n    zip\n    while True:\n"
                                   "        pass\n    return 1")
    assert "too long" in rejected(raw, sandbox_timeout=1.0).problem


def test_all_identical_outputs_are_rejected():
    raw = reply(reference_solution="def pair_up(names, scores):\n"
                                   "    return len(list(zip(names, scores))) >= 0")
    assert "same result" in rejected(raw).problem


def test_object_repr_outputs_are_rejected():
    raw = reply(reference_solution="def pair_up(names, scores):\n    z = zip(names, scores)\n"
                                   "    return [object(), len(names)]")
    assert "object" in rejected(raw).problem


def test_set_order_dependence_is_rejected():
    words = [f"word{i}" for i in range(12)]
    raw = reply(reference_solution="def pair_up(names, scores):\n"
                                   "    return [w for w, _ in zip(set(names), scores)]",
                tests=[{"names": words, "scores": list(range(12))},
                       {"names": words[:9], "scores": list(range(9))},
                       {"names": words[:10], "scores": list(range(10))},
                       {"names": words[:11], "scores": list(range(11))}])
    e = rejected(raw)
    assert "different runs" in e.problem and not e.repairable


def test_named_arguments_mapped_by_name_not_order():
    ex = check(reply(tests=[{"scores": [1, 2], "names": ["a", "b"]},
                            {"scores": [], "names": []}, {"names": ["q"], "scores": [9]}]))
    assert ex.test_inputs[0] == [["a", "b"], [1, 2]]


def test_positional_lists_and_flat_tests_are_repaired():
    # a real probe failure: def f(numbers) with [1, 2, 3] meaning ONE argument
    raw = reply(function_name="middle", question_text="Return the median.",
                reference_solution="import statistics\ndef middle(numbers):\n"
                                   "    return statistics.median(numbers)",
                tests=[[1, 2, 3], [5], [[4, 1]], [10, 20, 30, 40]])
    ex = check(raw, resolve("statistics.median"), 1)
    assert ex.test_inputs == [[[1, 2, 3]], [[5]], [[4, 1]], [[10, 20, 30, 40]]]
    assert ex.expected_outputs == [2, 5, 2.5, 25.0]


def test_answer_keys_inside_a_test_case_are_ignored():
    # a real reply (qwen2.5:1.5b, statistics.median): {"orders": [...], "expected_result": 3.5}
    raw = reply(function_name="middle", question_text="Return the median price.",
                reference_solution="import statistics\ndef middle(prices):\n"
                                   "    return statistics.median(prices)",
                tests=[{"prices": [1, 2, 3]}, {"prices": [5], "expected_result": 5},
                       {"prices": [4, 1], "Output": 99}, {"prices": [10, 20, 30, 40]}])
    ex = check(raw, resolve("statistics.median"), 1)
    assert ex.test_inputs == [[[1, 2, 3]], [[5]], [[4, 1]], [[10, 20, 30, 40]]]
    assert ex.expected_outputs == [2, 5, 2.5, 25.0]


def test_a_parameter_named_like_an_answer_key_is_kept():
    raw = reply(function_name="score", question_text="Add the bonus to the result using zip.",
                reference_solution="def score(result, bonus):\n"
                                   "    return [a + b for a, b in zip(result, bonus)]",
                tests=[{"result": [1], "bonus": [2]}, {"result": [], "bonus": []},
                       {"result": [5, 6], "bonus": [1, 1]}])
    assert check(raw).test_inputs[0] == [[1], [2]]


def test_tests_that_do_not_fit_the_parameters_are_dropped():
    raw = reply(tests=[{"names": ["a"], "scores": [1]}, {"names": ["a"]},
                       {"wrong": 1, "keys": 2, "here": 3}])
    assert "parameters" in rejected(raw).problem


def test_method_target_static_check_only():
    raw = reply(function_name="glue", question_text="Join the words with the separator.",
                reference_solution="def glue(words, sep):\n    return sep.join(words)",
                tests=[{"words": ["a", "b"], "sep": "-"}, {"words": [], "sep": ","},
                       {"words": ["x"], "sep": "+"}])
    ex = check(raw, resolve("str.join"), 1)
    assert ex.expected_outputs == ["a-b", "", "x"]
    assert "`str.join`" in ex.question_text  # the question now names the target


def test_module_class_target():
    raw = reply(function_name="flatten", question_text="Flatten the lists with chain.",
                reference_solution="from itertools import chain\ndef flatten(lists):\n"
                                   "    return list(chain.from_iterable(lists))",
                tests=[{"lists": [[1], [2, 3]]}, {"lists": []}, {"lists": [[], [4]]}])
    ex = check(raw, resolve("itertools.chain"), 2)
    assert ex.expected_outputs == [[1, 2, 3], [], [4]]


def test_missing_hint_gets_a_default():
    assert "zip" in check(reply(hint="")).hint


def test_type_hints_and_typing_import_are_removed():
    # a real failure from a live run: "from typing import Iterable" is not an allowed import
    raw = reply(reference_solution="from typing import List, Tuple\n"
                                   "def pair_up(names: List[str], scores: List[int]) "
                                   "-> List[Tuple[str, int]]:\n"
                                   "    result: list = list(zip(names, scores))\n"
                                   "    return result")
    ex = check(raw)
    assert "typing" not in ex.reference_solution and "List" not in ex.reference_solution
    assert ex.function_signature == "def pair_up(names, scores):"
    assert ex.expected_outputs[0] == [["ana", 3], ["bo", 7]]


def test_schema_asks_for_four_to_six_tests():
    from pygym.ai.prompt import SCHEMA
    tests = SCHEMA["properties"]["tests"]
    assert (tests["minItems"], tests["maxItems"]) == (4, 6)
