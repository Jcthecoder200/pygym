"""Test results for the student: every test's call, expected value, returned value and print()
output (runner -> grader -> GradeResult.tests -> the shared Rich / line renderers)."""
import json

import pytest

from pygym import grader, loader, style
from pygym._runner import (PROGRESS_MARK, STDOUT_CUT, STDOUT_LIMIT, Capture, Progress,
                           read_progress)
from pygym.grader import grade
from pygym.models import NOT_RUN, TIMED_OUT, Exercise, GradeResult, TestResult, short_repr


@pytest.fixture
def zip01():
    return next(e for e in loader.load_all() if e.id == "zip-01")


def _ex(target, reference, inputs, outputs, name="f", imports=()):
    return Exercise(id="t-1", target_function=target, difficulty=1, question_text="q",
                    function_name=name, function_signature=f"def {name}(x):",
                    reference_solution=reference, test_inputs=inputs, expected_outputs=outputs,
                    allowed_imports=list(imports))


def run(code, inputs, target="len", lv="audit-hook", timeout=5.0, imports=()):
    return grader.run_in_sandbox(code, "f", target, [list(a) for a in inputs], timeout,
                                 allowed_imports=imports, level=lv)


# ----- the runner: print() per test, at load time, limits, the JSON protocol ------------------

def test_prints_are_kept_per_test_and_at_load_time():
    code = ("print('loading', 1)\n"
            "def f(x):\n"
            "    print('got', x)\n"
            "    print('twice', end='')\n"
            "    return len(x)\n")
    res = run(code, [["ab"], [""]])
    assert res["status"] == "ok"
    assert res["setup_stdout"] == "loading 1\n"
    assert res["outputs"] == [{"value": 2, "stdout": "got ab\ntwice"},
                              {"value": 0, "stdout": "got \ntwice"}]


def test_no_stdout_key_when_nothing_was_printed():
    res = run("def f(x):\n    return len(x)", [["a"]])
    assert res["outputs"] == [{"value": 1}] and "setup_stdout" not in res


def test_printed_text_is_cut_per_test():
    code = ("def f(n):\n"
            "    for i in range(n):\n"
            "        print('x' * 99)\n"
            "    return len('x')\n")
    res = run(code, [[1000], [1]])
    first, second = (o["stdout"] for o in res["outputs"])
    assert first.endswith(STDOUT_CUT) and len(first) <= STDOUT_LIMIT + len(STDOUT_CUT) + 1
    assert second == "x" * 99 + "\n"  # the next test starts with an empty buffer


def test_huge_output_does_not_break_the_json_result():
    code = ("print('#' * 10_000_000, end='')\n"
            "def f(x):\n"
            "    print('{\"status\": \"fake\"}' * 1000)\n"
            "    for _ in range(100_000):\n"
            "        print('', end='')\n"
            "    return len(x)\n")
    res = run(code, [["abc"]])
    assert res["status"] == "ok" and res["outputs"][0]["value"] == 3
    assert res["setup_stdout"].endswith(STDOUT_CUT)
    assert res["outputs"][0]["stdout"].startswith('{"status": "fake"}')


def test_c_level_writes_to_stdout_do_not_break_the_protocol():
    code = ("import os\n"
            "os.write(1, b'{not json at load time')\n"
            "def f():\n"
            "    os.write(1, b'}} more garbage')\n"
            "    print('seen')\n"
            "    return len(os.getcwd()) > 0\n")
    res = run(code, [[]], target="os.getcwd", imports=["os"])
    assert res["status"] == "ok", res
    assert res["outputs"] == [{"value": True, "stdout": "seen\n"}]


def test_every_test_runs_after_one_raises():
    code = ("def f(x):\n"
            "    print('before', x)\n"
            "    if x == 2:\n"
            "        raise SystemExit('stop')\n"
            "    return len('a') / x\n")
    res = run(code, [[1], [0], [2], [4]])
    assert [sorted(o) for o in res["outputs"]] == [["stdout", "value"], ["error", "stdout"],
                                                   ["error", "stdout"], ["stdout", "value"]]
    assert res["outputs"][1]["error"] == "ZeroDivisionError: division by zero"
    assert res["outputs"][1]["stdout"] == "before 0\n"
    assert res["outputs"][2]["error"].startswith("SystemExit")
    assert res["outputs"][3]["value"] == 0.25


def test_odd_characters_in_prints_survive():
    code = "def f(x):\n    print('\\ud800', '\\x1b[2J', 'é中')\n    return len(x)"
    res = run(code, [["a"]])
    out = res["outputs"][0]["stdout"]
    assert "\\ud800" in out and "\x1b[2J" in out and "é中" in out


def test_load_error_keeps_what_was_printed():
    res = run("print('starting')\nraise ValueError('nope')", [[1]])
    assert res["status"] == "error" and res["setup_stdout"] == "starting\n"


def test_capture_unit():
    c = Capture()
    assert c.write("ab") == 2 and c.take() == "ab" and c.take() == ""
    c.write("x" * (STDOUT_LIMIT + 10))
    assert c.write("more") == 4  # write() always says it took everything
    assert c.take() == "x" * STDOUT_LIMIT + "\n" + STDOUT_CUT
    with pytest.raises(TypeError):
        c.write(b"bytes")
    assert not c.isatty() and c.writable()


# ----- the progress channel (timeouts) ------------------------------------------------------------

def _progress(*items, tail=""):
    return "".join(PROGRESS_MARK + json.dumps(i) + "\n" for i in items) + tail


def test_read_progress_running_test_and_prints():
    text = ("some library warning\n"
            + _progress({"stage": "setup"}, {"print": "hi\n"}, {"stage": "test", "i": 0},
                        {"print": "one\n"}, {"done": {"value": 1, "stdout": "one\n"}},
                        {"stage": "test", "i": 1}, {"print": "loop"}, {"print": "ing\n"})
            + PROGRESS_MARK + '{"print": "cut off by the ki')
    p = read_progress(text.encode())
    assert p == {"running": 1, "outputs": [{"value": 1, "stdout": "one\n"}],
                 "setup_stdout": "hi\n", "stdout": "looping\n"}


def test_read_progress_setup_and_nothing():
    assert read_progress(_progress({"stage": "setup"}, {"print": "x"}))["running"] == -1
    assert read_progress(_progress({"stage": "setup"}, {"print": "x"}))["setup_stdout"] == "x"
    assert read_progress(None) == {"running": None, "outputs": [], "setup_stdout": "",
                                   "stdout": ""}
    assert read_progress("garbage\n\x1epygym [1]\n")["running"] is None


def test_progress_never_raises_on_a_closed_stream():
    p = Progress(10_000)  # not an open file descriptor
    p.send(stage="setup")
    assert p.fd is None
    p.send(stage="setup")  # silently off


def test_timeout_reports_the_running_test_and_its_prints(zip01):
    code = ("def combine(keys, values):\n"
            "    print('start', len(keys))\n"
            "    while len(keys) == 2:\n"
            "        pass\n"
            "    return dict(zip(keys, values))\n")
    r = grade(code, zip01, timeout=1.5)
    assert r.reason == "timeout" and not r.passed
    assert r.message == "Your code ran too long (possible infinite loop) in test 2."
    assert [t.passed for t in r.tests] == [True, False, False]
    assert r.tests[0].stdout == "start 3\n"
    assert r.tests[1].error == TIMED_OUT and r.tests[1].stdout == "start 2\n"
    assert r.tests[2].error == NOT_RUN and not r.tests[2].ran and r.tests[1].ran


def test_timeout_while_loading(zip01):
    code = "print('before the loop')\nwhile True:\n    pass\ndef combine(k, v):\n    zip"
    r = grade(code, zip01, timeout=1.5)
    assert r.reason == "timeout" and "while loading" in r.message
    assert r.tests == [] and r.setup_stdout == "before the loop\n"


def test_timeout_without_progress_says_timed_out(monkeypatch, zip01):
    monkeypatch.setattr(grader, "run_in_sandbox", lambda *a, **k: {"status": "timeout"})
    r = grade("def combine(keys, values):\n    return zip", zip01)
    assert r.message == "Your code ran too long (possible infinite loop)." and r.tests == []


# ----- the grader: every row, messages ----------------------------------------------------------

def test_all_rows_after_a_wrong_answer(zip01):
    code = ("def combine(keys, values):\n"
            "    print('keys are', keys)\n"
            "    return dict(zip(keys, keys))\n")
    r = grade(code, zip01)
    assert r.reason == "wrong_output" and r.message == "Not quite right yet: 1 of 3 tests passed."
    assert [t.index for t in r.tests] == [1, 2, 3]
    assert [t.passed for t in r.tests] == [False, False, True]
    assert [t.expected for t in r.tests] == zip01.expected_outputs
    assert [t.args for t in r.tests] == zip01.test_inputs
    assert r.tests[0].got == {"a": "a", "b": "b", "c": "c"} and r.tests[0].error == ""
    assert r.tests[1].stdout == "keys are ['x', 'y']\n"
    assert r.summary() == "1/3 passed" and r.passed_count == 1


def test_error_rows(zip01):
    code = ("def combine(keys, values):\n"
            "    if not keys:\n"
            "        return {}[0]\n"
            "    return dict(zip(keys, values))\n")
    r = grade(code, zip01)
    assert r.message == "Not quite right yet: 2 of 3 tests passed."
    assert r.tests[2].error == "KeyError: 0" and r.tests[2].got is None


def test_passing_check_has_rows_and_prints(zip01):
    code = "print('hi')\ndef combine(keys, values):\n    return dict(zip(keys, values))"
    r = grade(code, zip01)
    assert r.passed and r.message == "Correct!" and r.setup_stdout == "hi\n"
    assert len(r.tests) == 3 and all(t.passed for t in r.tests)
    assert [t.got for t in r.tests] == zip01.expected_outputs


def test_target_not_used_still_fills_the_rows(zip01):
    code = ("def combine(keys, values):\n    d = {}\n"
            "    for i in range(min(len(keys), len(values))):\n        d[keys[i]] = values[i]\n"
            "    return d")
    r = grade(code, zip01)
    assert r.reason == "target_not_used" and r.message == "This exercise is about 'zip'. Try using it."
    assert len(r.tests) == 3 and all(t.passed for t in r.tests)  # right answers, wrong way


def test_dead_code_target_keeps_rows(zip01):
    code = ("def combine(keys, values):\n    if False:\n        zip(keys, values)\n"
            "    return {keys[i]: values[i] for i in range(min(len(keys), len(values)))}")
    r = grade(code, zip01)
    assert r.reason == "target_not_used" and "never actually called" in r.message
    assert all(t.passed for t in r.tests)


def test_no_expected_values_without_expected_results():
    ex = _ex("len", "def f(x):\n    return len(x)", [["a"], ["bc"]], [1])
    r = grade("def f(x):\n    print('x')\n    return len(x)", ex)
    assert r.reason == "error" and "broken" in r.message
    assert r.tests == [] and r.setup_stdout == ""


def test_syntax_error_and_missing_function_have_no_rows(zip01):
    assert grade("def combine(:", zip01).tests == []
    r = grade("print('oops')\nx = zip([1], [2])", zip01)
    assert r.reason == "error" and r.tests == [] and r.setup_stdout == "oops\n"


def test_one_test_wording():
    ex = _ex("len", "def f(x):\n    return len(x)", [["abc"]], [3])
    assert grade("def f(x):\n    return len(x) + 1", ex).message == \
        "Not quite right yet: 0 of 1 test passed."


def test_blocked_note_is_kept():
    ex = _ex("len", "def f(x):\n    return len(x)", [["abc"]], [3])
    ex.files = {"a.txt": "a"}
    code = "def f(x):\n    open('/tmp/../etc/pygym-x', 'w')\n    return len(x)"
    r = grade(code, ex)
    assert r.reason == "wrong_output" and "(pygym blocked:" in r.message
    assert "PermissionError" in r.tests[0].error


# ----- models -------------------------------------------------------------------------------------

def test_call_text_and_short_repr():
    t = TestResult(1, [[1, 2], [3]], {})
    assert t.call_text("f") == "f([1, 2], [3])"
    assert TestResult(2, [], 0).call_text("g") == "g()"
    long = TestResult(3, [list(range(100))], 0).call_text("f")
    assert len(long) == 80 and long.endswith("…") and long.startswith("f([0, 1, 2")
    assert short_repr("abc") == "'abc'" and short_repr("x" * 100, 10) == "'xxxxxxxx…"


def test_grade_result_defaults_and_old_construction():
    r = GradeResult(False, "error", "m")
    assert r.tests == [] and r.setup_stdout == "" and r.summary() == ""
    assert GradeResult(True, "ok").message == ""


# ----- the shared renderers ---------------------------------------------------------------------

def _result():
    return GradeResult(False, "wrong_output", "Not quite right yet: 1 of 4 tests passed.",
                       tests=[TestResult(1, [["a"], [1]], {"a": 1}, got={"a": 1}, passed=True,
                                         stdout="hello\n"),
                              TestResult(2, [[], []], {}, got=None,
                                         stdout="".join(f"line {i}\n" for i in range(9))),
                              TestResult(3, [[1], []], {}, error="ZeroDivisionError: x\ny\x1b[2J"),
                              TestResult(4, [[2], []], {}, error=NOT_RUN)],
                       setup_stdout="loading\n")


def test_rich_table():
    text = style.to_plain(style.results(_result(), "combine"))
    for needle in ("Printed while your code was loading:", "│ loading", "tests  1/4 passed",
                   "call", "expected", "got", "combine(['a'], [1])", "{'a': 1}", "None",
                   "ZeroDivisionError: x ↵ y\\x1b[2J", "not run", "✓", "✗",
                   "print() output of test 1", "│ hello", "│ line 8"):
        assert needle in text, needle
    assert "\x1b" not in text


def test_response_puts_the_table_above_the_verdict(zip01):
    class R:
        kind, text, result = "fail", "Failed: Not quite right yet.", _result()

    class S:
        exercise = zip01

    text = style.to_plain(style.response(R, S))
    assert text.index("tests  1/4 passed") < text.index("✗ Failed: Not quite right yet.")
    R.result = GradeResult(False, "error", "Syntax error on line 1: x")
    assert style.to_plain(style.response(R, S)) == "✗ Failed: Not quite right yet."


def test_result_lines():
    rows = [r.plain for r in style.result_lines(_result(), "combine", 60, print_lines=3)]
    assert rows[:2] == ["printed while loading:", "  │ loading"]
    assert rows[2] == "✓ 1  combine(['a'], [1])  →  {'a': 1}"
    assert rows[3] == "    │ hello"
    assert rows[4] == "✗ 2  combine([], [])  expected {}  got None"
    assert rows[5:9] == ["    │ line 0", "    │ line 1", "    │ line 2", "    │ … 6 more lines"]
    # too long for one row: the outcome goes on its own row, under the call
    assert rows[9:11] == ["✗ 3  combine([1], [])",
                          "     expected {}  raised ZeroDivisionError: x ↵ y\\x1b[2J"]
    assert rows[11:13] == ["· 4  combine([2], [])", "     " + NOT_RUN]
    assert all(len(r) <= 60 and "\x1b" not in r and "\n" not in r for r in rows)
    narrow = style.result_lines(_result(), "combine", 20)
    assert all(r.cell_len <= 20 for r in narrow) and narrow[2].plain.endswith("…")


def test_result_lines_without_tests_show_the_message():
    r = GradeResult(False, "error", "Syntax error on line 2: invalid syntax")
    assert [x.plain for x in style.result_lines(r, "f", 30)] == \
        ["✗ Syntax error on line 2:", "invalid syntax"]
    assert style.results_title(r) == "no tests ran" and not style.has_results(r)


def test_visible_escapes_control_characters():
    assert style.visible("a\tb\r\nc\x1b[2Jd\x07") == "a   b\nc\\x1b[2Jd\\x07"
    assert style.printed_lines("a\nb\nc\n", 2) == (["a", "b"], 1)
    assert style.printed_lines("", 2) == ([], 0)
