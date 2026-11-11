"""A generated exercise must not ask for one function while the code defines another."""
import json

import pytest

from pygym.ai import cache
from pygym.ai.targets import resolve
from pygym.ai.validate import InvalidExercise, names_other_function, to_exercise
from pygym.models import Exercise

ZIP = resolve("zip")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))


def reply(**overrides):
    d = {
        "question_text": "Write a function 'pair_up' that pairs each name with its score using "
                         "zip and returns a list of pairs.",
        "function_name": "pair_up",
        "function_signature": "def pair_up(names, scores):",
        "reference_solution": "def pair_up(names, scores):\n    return list(zip(names, scores))",
        "tests": [{"names": ["ana", "bo"], "scores": [3, 7]}, {"names": [], "scores": []},
                  {"names": ["x"], "scores": [1, 2]}, {"names": ["a", "b"], "scores": [1, 2]}],
    }
    d.update(overrides)
    return json.dumps(d)


@pytest.mark.parametrize("question,name,target,other", [
    ("Write a function 'best_plan' that returns the cumulative sum.", "cumulative_sum",
     "numpy.cumsum", "best_plan"),
    ("Write a function `cumulative_sum` that ...", "cumulative_sum", "numpy.cumsum", ""),
    ("Write a function that adds numbers.", "total", "sum", ""),
    ("Write a function named total(nums) using sum.", "add_all", "sum", "total"),
    ("Use the function `cumsum()` from numpy in cumulative_sum.", "cumulative_sum",
     "numpy.cumsum", ""),
    ("Use the function 'sorted' inside rank.", "rank", "sorted", ""),
    ("Write a function's docstring", "f", "len", ""),
])
def test_names_other_function(question, name, target, other):
    assert names_other_function(question, name, target) == other


def test_name_field_disagrees_with_the_code_the_question_is_renamed():
    # The reported case: the question used the name field, the code another name.
    ex = to_exercise(reply(function_name="best_pairs",
                           question_text="Write a function 'best_pairs' that pairs names and "
                                         "scores with zip."), ZIP, 1, "fake:fake")
    assert ex.function_name == "pair_up"
    assert "'pair_up'" in ex.question_text and "best_pairs" not in ex.question_text
    assert ex.starter_code.startswith("def pair_up(")


def test_question_naming_a_third_function_is_rejected_and_repairable():
    with pytest.raises(InvalidExercise) as e:
        to_exercise(reply(question_text="Write a function 'plant_schedule' that pairs names "
                                        "and scores with zip."), ZIP, 1, "fake:fake")
    assert "plant_schedule" in str(e.value) and "pair_up" in str(e.value)
    assert e.value.repairable


def test_cache_skips_saved_exercises_that_contradict_themselves():
    good = to_exercise(reply(), ZIP, 1, "fake:fake")
    bad = Exercise.from_dict({**good.__dict__, "id": "ai-zip-1-bad",
                              "question_text": "Write a function 'other_name' using zip."})
    cache.add(bad)
    assert cache.pick("zip", 1) is None
    cache.add(good)
    assert cache.pick("zip", 1).id == good.id
