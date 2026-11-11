"""What each level means (ai/levels.py), checked on the reference solution; the prompt that says
it; the validator that enforces it; the report on the hand-written exercises; which targets are
too small for an advanced exercise on their own (TargetInfo.simple); signatures without type
hints. No LLM: fake replies only."""
import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from pygym import grader
from pygym.ai import cache, engine, levels
from pygym.ai.engine import short_reason
from pygym.ai.prompt import LEVEL_RULES, build_messages
from pygym.ai.providers import FakeProvider
from pygym.ai.targets import resolve
from pygym.ai.validate import InvalidExercise, to_exercise
from pygym.models import Exercise, plain_signature

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _rules_on(real_levels):
    """Every test here uses the real level rules (conftest turns them off by default)."""


def check(src, level, name=None):
    tree = ast.parse(src)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    fn = next(f for f in fns if f.name == name) if name else fns[-1]
    return levels.check_level(tree, fn, level)


def markers(src, name=None):
    tree = ast.parse(src)
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    fn = next(f for f in fns if f.name == name) if name else fns[-1]
    return levels.analyse(tree, fn).markers


def fits(src):
    """The levels a solution fits: e.g. (1,) or (2, 3)."""
    return tuple(lv for lv in (1, 2, 3) if check(src, lv) is None)


ONE_LINER = "def to_bits(n):\n    return bin(n).replace('0b', '')"
STRAIGHT = ("def label(n, width):\n    bits = bin(n)[2:]\n    padded = bits.zfill(width)\n"
            "    return 'b:' + padded")
AND_OR = "def ok(a, b):\n    return bool(a and b or not a)"
FOR_IF = ("def evens(nums):\n    out = []\n    for n in nums:\n        if n % 2 == 0:\n"
          "            out.append(n)\n    return out")
COMP_IF = "def evens(nums):\n    return [n for n in nums if n % 2 == 0]"
COMP_IFEXP = "def signs(nums):\n    return ['+' if n > 0 else '-' for n in nums]"
COMP_ONLY = "def squares(nums):\n    return [n * n for n in nums]"
IFEXP_ONLY = "def sign(n):\n    return 'pos' if n > 0 else 'neg'"
IF_ONLY = "def sign(n):\n    if n > 0:\n        return 'pos'\n    return 'neg'"
WHILE_IF = ("def halves(n):\n    steps = 0\n    while n > 1:\n        if n % 2:\n"
            "            n += 1\n        n //= 2\n        steps += 1\n    return steps")
TOP_WORDS = ("def top_words(text, k):\n    counts = {}\n    for w in text.lower().split():\n"
             "        if w.isalpha():\n            counts[w] = counts.get(w, 0) + 1\n"
             "    return sorted(counts, key=lambda w: (-counts[w], w))[:k]")
GROUP_BY_ONES = ("def group_by_ones(numbers):\n    groups = {}\n    for n in numbers:\n"
                 "        if n < 0:\n            continue\n        ones = bin(n).count('1')\n"
                 "        groups.setdefault(ones, []).append(n)\n    return groups")
EVEN_ODD = ("def count_even(nums):\n    counts = {'even': 0, 'odd': 0}\n    for n in nums:\n"
            "        if n % 2 == 0:\n            counts['even'] += 1\n        else:\n"
            "            counts['odd'] += 1\n    return counts")
BALANCED = ("def balanced(text):\n    pairs = {')': '(', ']': '['}\n    stack = []\n"
            "    for ch in text:\n        if ch in '([':\n            stack.append(ch)\n"
            "        elif ch in pairs:\n            if not stack or stack.pop() != pairs[ch]:\n"
            "                return False\n    return not stack")


# ----- each level ---------------------------------------------------------------------------------

@pytest.mark.parametrize("src, expected", [
    (ONE_LINER, (1,)),
    (STRAIGHT, (1,)),
    (AND_OR, (1,)),                 # and / or are not if/else
    (FOR_IF, (2,)),
    (COMP_IF, (1, 2)),              # one line: beginner; a loop with a branch: intermediate
    (COMP_IFEXP, (1, 2)),           # a conditional expression is a branch
    (COMP_ONLY, (1,)),              # one line; a loop without a condition is not intermediate
    (IFEXP_ONLY, (1,)),             # one line; a condition without a loop
    (IF_ONLY, ()),                  # an if statement over several lines: not beginner
    (WHILE_IF, (2,)),
    (TOP_WORDS, (2, 3)),            # dict + sorted(key=)
    (GROUP_BY_ONES, (2, 3)),        # one marker, 7 lines: the threshold
    (EVEN_ODD, (2,)),               # one marker, 6 lines: still intermediate
    (BALANCED, (2, 3)),             # dict + stack
])
def test_levels_a_solution_fits(src, expected):
    assert fits(src) == expected


def test_the_bin_one_liner_is_not_advanced():
    problem = check(ONE_LINER, 3)
    assert problem.startswith("too simple for advanced")
    assert "no loop" in problem and "no condition" in problem
    assert "no data structure or algorithm" in problem
    assert "make the task bigger, with the target as one step" in problem.lower()


@pytest.mark.parametrize("src, words", [
    ("def f(xs):\n    for x in xs:\n        pass\n    return 1", "a for loop"),
    ("def f(n):\n    while n:\n        n -= 1\n    return n", "a while loop"),
    ("def f(xs):\n    ys = [x * 2 for x in xs]\n    return ys", "a comprehension"),
    ("def f(xs):\n    t = sum(x for x in xs)\n    return t", "a comprehension"),    # generator
    ("def f(xs):\n    d = {x: 1 for x in xs}\n    return d", "a comprehension"),    # dict comp
    ("def f(n):\n    s = 'pos' if n > 0 else 'neg'\n    return s", "a conditional expression"),
    (IF_ONLY, "an if statement"),
    ("def f(xs):\n    k = lambda x: 0 if x else 1\n    return sorted(xs, key=k)",
     "a conditional expression"),
])
def test_beginner_rejects_branches_and_loops_beyond_one_line(src, words):
    problem = check(src, 1)
    assert problem.startswith("too complex for beginner") and words in problem
    assert "ONE line" in problem


@pytest.mark.parametrize("src", [
    COMP_ONLY, COMP_IF, COMP_IFEXP, IFEXP_ONLY,
    "def f(xs):\n    return sum(x for x in xs)",
    "def f(xs):\n    return {x: 1 for x in xs}",
    "def f(xs):\n    return sorted(xs, key=lambda x: 0 if x else 1)",
])
def test_beginner_allows_anything_in_one_line(src):
    # the owner's rule: "one liner OR no if/else, no loop"
    assert check(src, 1) is None


@pytest.mark.skipif(sys.version_info < (3, 10), reason="match needs Python 3.10")
def test_match_is_a_branch():
    src = ("def f(x):\n    match x:\n        case 1:\n            return 'one'\n"
           "        case _:\n            return 'other'")
    assert "a match statement" in check(src, 1)


def test_intermediate_messages_name_what_is_missing():
    assert "it has no loop and no condition" in check(ONE_LINER, 2)
    assert "it has no condition" in check(COMP_ONLY, 2)
    assert "it has no loop" in check(IF_ONLY, 2)
    assert check(ONE_LINER, 2).startswith("too simple for intermediate")


def test_intermediate_is_at_most_20_lines():
    body = "".join(f"            x{i} = n + {i}\n" for i in range(17))
    ok = f"def f(nums):\n    total = 0\n    for n in nums:\n        if n:\n{body}    return 0"
    assert levels.analyse(ast.parse(ok), ast.parse(ok).body[0]).lines == 21
    problem = check(ok, 2)
    assert problem.startswith("too long for intermediate") and "21 lines" in problem
    shorter = ok.replace("            x16 = n + 16\n", "")
    assert check(shorter, 2) is None
    assert check(ok, 3) is not None   # advanced has no line limit, but needs a marker


# ----- the advanced markers -----------------------------------------------------------------------

LOOP_IF = "    for x in xs:\n        if x:\n            pass\n"


@pytest.mark.parametrize("body, marker", [
    ("    d = {}\n", "dict_set"),
    ("    d = dict()\n", "dict_set"),
    ("    s = set()\n", "dict_set"),
    ("    s = {1, 2}\n", "dict_set"),
    ("    s = {x for x in xs}\n", "dict_set"),
    ("    d = {x: 1 for x in xs}\n", "dict_set"),
    ("    xs[0] = 1\n", "dict_set"),                      # item assignment
    ("    xs[0] += 1\n", "dict_set"),
    ("    seen.add(1)\n", "dict_set"),
    ("    d.setdefault(1, [])\n", "dict_set"),
    ("    c = collections.Counter(xs)\n", "collections"),
    ("    c = Counter(xs)\n", "collections"),             # from collections import Counter
    ("    q = deque(xs)\n", "collections"),
    ("    heapq.heapify(xs)\n", "heapq"),
    ("    heappush(xs, 1)\n", "heapq"),
    ("    i = bisect.bisect_left(xs, 3)\n", "bisect"),
    ("    st = []\n    st.append(1)\n    st.pop()\n", "stack"),
    ("    xs.sort(key=len)\n", "sort_key"),
    ("    ys = sorted(xs, key=abs)\n", "sort_key"),
    ("    for a in xs:\n        for b in xs:\n            pass\n", "nested"),
    ("    ys = [a * b for a in xs for b in xs]\n", "nested"),
    ("    ys = [[b for b in a] for a in xs]\n", "nested"),
    ("    for a in xs:\n        t = sum(b for b in a)\n", "nested"),
    ("    f([])\n", "recursion"),
])
def test_each_marker_is_found(body, marker):
    head = ("import collections\nimport heapq\nimport bisect\n"
            "from collections import Counter, deque\nfrom heapq import heappush\n")
    src = f"{head}def f(xs, seen=None, d=None):\n{body}{LOOP_IF}    return xs"
    assert marker in markers(src)


@pytest.mark.parametrize("body", [
    "    ys = sorted(xs)\n",                 # sorting without key=
    "    st = []\n    st.append(1)\n",       # append without pop is not a stack
    "    np.add(xs, 1)\n",                   # .add of a module is not set.add
    "    return xs[0]\n",                    # reading an item is not building a dict
    "    for a in [b for b in xs]:\n        pass\n",   # a comprehension as the iterable: not nested
])
def test_not_markers(body):
    src = f"import numpy as np\ndef f(xs):\n{body}    return xs"
    found = markers(src) - {"dict_set"} if "st = []" in body else markers(src)
    assert found == set()


def test_two_markers_or_one_marker_and_7_lines():
    assert levels.MARKERS_NEEDED == 2 and levels.LINES_WITH_ONE_MARKER == 7
    short_two = ("def f(xs):\n    d = {}\n    for x in xs:\n        if x:\n"
                 "            d[x] = 1\n    return sorted(d, key=str)")
    assert check(short_two, 3) is None
    short_one = "def f(xs):\n    return {x for x in xs if x}"
    problem = check(short_one, 3)
    assert "only one data structure or algorithm (a dict or set it fills) in 1 lines" in problem
    assert "add a second one or make the algorithm longer (7+ lines)" in problem
    assert check(GROUP_BY_ONES, 3) is None and check(EVEN_ODD, 3) is not None


def test_recursion_needs_the_function_to_call_itself():
    flatten = ("def flatten(items):\n    out = []\n    for item in items:\n"
               "        if isinstance(item, list):\n            out.extend(flatten(item))\n"
               "        else:\n            out.append(item)\n    return out")
    assert "recursion" in markers(flatten)
    assert fits(flatten) == (2,)         # recursion alone in 6 lines: just below the threshold
    nested_sum = ("def sum_nested(data):\n    total = 0\n    for item in data:\n"
                  "        if isinstance(item, list):\n            total += sum_nested(item)\n"
                  "        elif isinstance(item, (int, float)) and not isinstance(item, bool):\n"
                  "            total += item\n    return total")       # the preset isinstance-03
    assert markers(nested_sum) == {"recursion"} and fits(nested_sum) == (2, 3)   # 7 lines
    assert "recursion" not in markers("def f(xs):\n    return g(xs)")


def test_helpers_count_and_imports_and_other_top_level_code_do_not():
    helper = ("import collections\nTABLE = {1: 2}\n"
              "def count(xs):\n    c = {}\n    for x in xs:\n        if x:\n"
              "            c[x] = c.get(x, 0) + 1\n    return c\n"
              "def best(xs):\n    return sorted(count(xs), key=str)")
    assert markers(helper, "best") == {"dict_set", "sort_key"}
    assert check(helper, 3, "best") is None
    # the import and the module-level dict alone do not make a one-liner advanced
    lone = "import collections\nTABLE = {1: 2}\ndef f(xs):\n    return len(xs)"
    assert markers(lone) == set() and check(lone, 1) is None
    # a helper that is defined but never called does not count
    unused = ("def helper(xs):\n    for x in xs:\n        if x:\n            return x\n"
              "def f(xs):\n    return len(xs)")
    assert check(unused, 1, "f") is None


def test_docstrings_and_the_def_line_are_not_lines():
    src = 'def f(xs):\n    """Doc."""\n    return xs'
    tree = ast.parse(src)
    assert levels.analyse(tree, tree.body[0]).lines == 1


# ----- the prompt -----------------------------------------------------------------------------------

def test_level_rules_come_from_levels():
    for level in (1, 2, 3):
        assert LEVEL_RULES[level].startswith(levels.describe(level))
        assert levels.example_idea(level) in LEVEL_RULES[level]
    assert "NO if/else" in levels.describe(1) and "NO for/while loop" in levels.describe(1)
    assert "MUST have a loop" in levels.describe(2) and "condition" in levels.describe(2)
    assert "data structures or algorithms" in levels.describe(3)
    assert "sorting with key=" in levels.describe(3) and "7+ lines" in levels.describe(3)


@pytest.mark.parametrize("level, name", [(1, "beginner"), (2, "intermediate"), (3, "advanced")])
def test_prompt_contains_the_level_rule(level, name):
    user = build_messages(resolve("sorted"), level)[1]["content"]
    assert f"Level: {name} ({level}/3): {LEVEL_RULES[level]}" in user
    assert f"It MUST follow the {name} rule above." in user
    assert "BIGGER" not in user and "Write one exercise that teaches `sorted`." in user


def test_bigger_task_prompt():
    user = build_messages(resolve("bin"), 3, bigger=True)[1]["content"]
    assert "Write one exercise that practises `bin` inside a bigger task." in user
    assert "`bin` alone is too small for an advanced exercise, so this is a BIGGER task" in user
    assert "`bin` is one necessary step" in user and "MUST call `bin(...)`" in user
    assert LEVEL_RULES[3] in user
    # bigger only means something at advanced
    assert "BIGGER" not in build_messages(resolve("bin"), 2, bigger=True)[1]["content"]


# ----- the validator ----------------------------------------------------------------------------------

BIN = resolve("bin")
SORTED = resolve("sorted")


def reply(reference, name, tests, question=None):
    return json.dumps({
        "question_text": question or f"Write {name}. Use bin for each number.",
        "function_name": name, "function_signature": f"def {name}(numbers):",
        "reference_solution": reference, "tests": tests, "hint": "bin() gives the bits.",
    })


NUMBERS = [{"numbers": [1, 2, 3]}, {"numbers": []}, {"numbers": [7, 8]}, {"numbers": [5, -1, 4]}]
BIG_REF = ("def group_by_ones(numbers):\n    groups = {}\n    for n in numbers:\n"
           "        if n < 0:\n            continue\n        ones = bin(n).count('1')\n"
           "        groups.setdefault(ones, []).append(n)\n    return groups")


def test_validator_rejects_an_advanced_one_liner_as_repairable():
    raw = reply("def to_bits(numbers):\n    return [bin(n) for n in numbers]", "to_bits", NUMBERS)
    with pytest.raises(InvalidExercise) as e:
        to_exercise(raw, BIN, 3, "fake:fake")
    assert e.value.repairable and e.value.problem.startswith("too simple for advanced")
    assert short_reason(e.value.problem) == "too simple for the level"


def test_validator_accepts_a_real_advanced_solution():
    ex = to_exercise(reply(BIG_REF, "group_by_ones", NUMBERS), BIN, 3, "fake:fake")
    assert ex.difficulty == 3 and grader.grade(ex.reference_solution, ex).passed


@pytest.mark.parametrize("level, reference, start", [
    (1, "def to_bits(numbers):\n    bits = [bin(n) for n in numbers]\n    return bits",
     "too complex for beginner"),
    (2, "def to_bits(numbers):\n    return [bin(n) for n in numbers]", "too simple for intermediate"),
    (3, "def to_bits(numbers):\n    return [bin(n) for n in numbers if n > 0]", "too simple for advanced"),
])
def test_validator_repair_message_for_each_level(level, reference, start):
    with pytest.raises(InvalidExercise) as e:
        to_exercise(reply(reference, "to_bits", NUMBERS), BIN, level, "fake:fake")
    assert e.value.repairable and e.value.problem.startswith(start)


def test_engine_sends_the_level_problem_back_to_the_model(monkeypatch):
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)
    one_liner = reply("def to_bits(numbers):\n    return [bin(n) for n in numbers]",
                      "to_bits", NUMBERS)
    fake = FakeProvider([one_liner, reply(BIG_REF, "group_by_ones", NUMBERS)])
    monkeypatch.setattr(engine, "get_provider", lambda settings: fake)
    events = []
    ex = engine.generate_exercise("bin", 3, bigger=True, on_progress=events.append)
    assert ex.function_name == "group_by_ones" and len(fake.calls) == 2
    first, second = fake.calls[0]["messages"], fake.calls[1]["messages"]
    assert "BIGGER task" in first[1]["content"]
    assert second[-1]["role"] == "user" and "too simple for advanced" in second[-1]["content"]
    assert "Make the task bigger, with the target as one step" in second[-1]["content"]
    assert any(e["stage"] == "rejected" and e["detail"] == "too simple for the level"
               for e in events)


def test_cache_fallback_skips_saved_exercises_that_do_not_fit_their_level():
    good = to_exercise(reply(BIG_REF, "group_by_ones", NUMBERS), BIN, 3, "fake:fake")
    old = Exercise.from_dict({**good.__dict__, "id": "ai-bin-3-old", "function_name": "to_bits",
                              "function_signature": "def to_bits(numbers):",
                              "reference_solution": "def to_bits(numbers):\n"
                                                    "    return [bin(n) for n in numbers]",
                              "expected_outputs": [["0b1", "0b10", "0b11"], [],
                                                   ["0b111", "0b1000"], ["0b101", "-0b1", "0b100"]]})
    cache.add(old)
    assert not cache.fits_level(old) and cache.pick("bin", 3) is None
    cache.add(good)
    assert cache.fits_level(good) and cache.pick("bin", 3).id == good.id


# ----- signatures without type hints ---------------------------------------------------------------

def test_signature_never_shows_type_hints_even_without_typing():
    ref = ("def label(number: int, width: int = 8) -> str:\n"
           "    padded: str = bin(number)[2:].zfill(width)\n    return padded")
    raw = json.dumps({"question_text": "Write label(number, width) using bin.",
                      "function_name": "label", "function_signature": "def label(number: int):",
                      "reference_solution": ref,
                      "tests": [{"number": 1, "width": 4}, {"number": 5, "width": 4},
                                {"number": 2, "width": 2}, {"number": 9, "width": 8}],
                      "hint": "bin"})
    ex = to_exercise(raw, BIN, 1, "fake:fake")
    assert ex.function_signature == "def label(number, width=8):"
    assert ":" not in ex.reference_solution.splitlines()[0][:-1] and "->" not in ex.reference_solution
    assert ex.starter_code == "def label(number, width=8):"


@pytest.mark.parametrize("given, shown", [
    ("def f(number: int) -> str:", "def f(number):"),
    ("def f(a, b=2):", "def f(a, b=2):"),
    ("def f(*xs: int, key: str = 'a', **kw: int) -> None:", "def f(*xs, key='a', **kw):"),
    ("def f(d={'a': 1}):", "def f(d={'a': 1}):"),
    ("not python", "not python"),
])
def test_plain_signature(given, shown):
    assert plain_signature(given) == shown


def test_starter_code_of_an_old_saved_exercise_drops_type_hints():
    ex = Exercise(id="x", target_function="bin", difficulty=1, question_text="q",
                  function_name="f", function_signature="def f(number: int) -> str:",
                  reference_solution="def f(number: int) -> str:\n    return bin(number)",
                  test_inputs=[[1], [2], [3]])
    assert ex.starter_code == "def f(number):"


# ----- TargetInfo.simple -----------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["bin", "hex", "oct", "abs", "len", "chr", "ord", "str.upper",
                                  "str.isdigit", "reversed", "any", "math.sqrt",
                                  "statistics.median"])
def test_simple_targets(name):
    assert resolve(name).simple is True


@pytest.mark.parametrize("name", ["sorted", "max", "min", "round", "str.join", "zip", "sum",
                                  "divmod", "enumerate", "map", "range", "iter", "int",
                                  "str.split", "str.strip", "math.gcd", "pow"])
def test_targets_with_enough_features(name):
    assert resolve(name).simple is False


def test_numpy_cumsum_is_not_simple():
    pytest.importorskip("numpy")
    assert resolve("numpy.cumsum").simple is False


def test_unknown_signature_is_not_simple():
    from pygym.ai.targets import TargetInfo
    assert TargetInfo("x", "x", "builtin", "", "", "").simple is False
    assert TargetInfo("x", "x", "builtin", "", "x(a)", "x(a, b) -> y").simple is False
    assert TargetInfo("x", "x", "builtin", "", "x(a)", "e.g. x([1, 2]) -> 3").simple is True


# ----- the report on the hand-written exercises -----------------------------------------------------

def _validator():
    spec = importlib.util.spec_from_file_location("validate_exercises",
                                                  ROOT / "tools" / "validate_exercises.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_levels_report_lists_presets_and_never_fails(tmp_path, monkeypatch, capsys):
    module = _validator()
    data = [
        {"id": "a-1", "difficulty": 1, "function_name": "f",
         "reference_solution": "def f(xs):\n    ys = [x for x in xs]\n    return ys"},
        {"id": "a-2", "difficulty": 2, "function_name": "f", "reference_solution": FOR_IF.replace(
            "evens", "f")},
        {"id": "a-3", "difficulty": 3, "function_name": "f",
         "reference_solution": "def f(xs):\n    return max(xs)"},
    ]
    assert [(i, lv) for i, lv, _ in module.level_report(data)] == [("a-1", 1), ("a-3", 3)]
    path = tmp_path / "exercises.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(module, "PATH", path)
    monkeypatch.setattr("sys.argv", ["validate_exercises.py", "--levels"])
    assert module.main() == 0
    out = capsys.readouterr().out
    assert "LEVEL a-1 (1 beginner): uses a comprehension in 2 lines" in out
    assert "LEVEL a-3 (3 advanced): no loop; no condition; no data structure or algorithm" in out
    assert "a-2" not in out and "2 of 3 exercises do not follow the level rules" in out


def test_levels_report_on_the_bundled_exercises(monkeypatch, capsys):
    module = _validator()
    monkeypatch.setattr("sys.argv", ["validate_exercises.py", "--levels"])
    assert module.main() == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert last.endswith("(report only: hand-written exercises are reviewed by people; "
                         "see docs/AI_MODE.md).")
