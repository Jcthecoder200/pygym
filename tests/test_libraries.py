"""Exercises about any installed library: which libraries are allowed, how results of numpy /
pandas / ... are compared, grading, and the AI engine end to end with a fake model.

numpy, pandas and requests are optional: tests that need them are skipped without them.
"""
import dataclasses
import json
import math
import sys
import urllib.request
from decimal import Decimal
from fractions import Fraction

import pytest
from rich.console import Console

from pygym import ai, grader, menu, sandbox, style
from pygym._runner import import_permitted, norm
from pygym.ai import engine, targets
from pygym.ai.providers import FakeProvider
from pygym.ai.targets import UnknownTarget, resolve
from pygym.models import Exercise

OS_SANDBOX = sandbox.level() != "audit-hook"
needs_os_sandbox = pytest.mark.skipif(not OS_SANDBOX, reason="no OS sandbox on this machine")


# ----- which libraries ----------------------------------------------------------------------


@pytest.mark.parametrize("target, words", [
    ("tkinter.Tk", "needs a screen"), ("turtle.forward", "needs a screen"),
    ("subprocess.run", "starts or controls other programs"), ("ctypes.CDLL", "memory"),
    ("sys.exit", "running Python"), ("pip.main", "installs"), ("webbrowser.open", "browser"),
    ("pygym.grader.grade", "pygym itself"), ("random.choice", "change on every run"),
    ("pygame.display.set_mode", "needs a screen"), ("PyQt5.QtWidgets.QApplication", "screen"),
])
def test_deny_list_is_refused_with_a_reason(target, words):
    with pytest.raises(UnknownTarget) as e:
        resolve(target)
    assert "is not allowed in pygym" in str(e.value) and words in str(e.value)


@needs_os_sandbox
def test_process_functions_of_os_are_refused_even_with_an_os_sandbox():
    with pytest.raises(UnknownTarget, match="'os.system' is not allowed"):
        resolve("os.system")
    assert resolve("os.listdir").kind == "module"
    assert resolve("os.path.join").allowed_imports == ["os"]


def test_file_and_network_libraries_need_an_os_sandbox(monkeypatch):
    monkeypatch.setenv("PYGYM_SANDBOX", "audit-hook")
    for target in ("os.listdir", "pathlib.Path", "shutil.copyfile", "urllib.request.urlopen"):
        with pytest.raises(UnknownTarget, match="no operating-system sandbox"):
            resolve(target)
    assert resolve("statistics.median").kind == "module"      # pure modules still work
    assert resolve("csv.reader").kind == "module"


def test_not_installed_library_gets_a_pip_hint(monkeypatch):
    monkeypatch.setattr(targets, "_installed", lambda name: False)
    with pytest.raises(UnknownTarget) as e:
        resolve("numpy.cumsum")
    assert "'numpy' is not installed in this Python" in str(e.value)
    assert f"{sandbox.python()} -m pip install numpy" in str(e.value)
    with pytest.raises(UnknownTarget, match="no library called 'nosuchlib_xyz'"):
        resolve("nosuchlib_xyz.f")
    assert sandbox.install_hint("yaml").endswith("-m pip install pyyaml")
    assert sandbox.install_hint("nosuchlib_xyz") == ""


def test_library_that_does_not_import_in_the_sandbox(monkeypatch):
    pytest.importorskip("numpy")
    monkeypatch.setattr(sandbox, "importable", lambda top: (False, "ImportError: boom"))
    with pytest.raises(UnknownTarget, match="does not import inside the pygym sandbox"):
        resolve("numpy.cumsum")


def test_numpy_target_resolves():
    pytest.importorskip("numpy")
    info = resolve("numpy.cumsum")
    assert (info.kind, info.owner, info.package, info.short) == ("module", "numpy", "numpy",
                                                                "cumsum")
    assert info.allowed_imports == ["numpy"] and info.signature.startswith("cumsum(")
    assert "np.cumsum" in info.call_form and info.doc
    assert not info.needs_http and not info.needs_files
    assert "numpy.cumsum" in targets.functions("numpy")
    assert "numpy.cumsum" in targets.suggest("cumsun", library="numpy")
    with pytest.raises(UnknownTarget, match="methods of 'numpy.ndarray' are not supported"):
        resolve("numpy.ndarray.cumsum")
    with pytest.raises(UnknownTarget) as e:
        resolve("numpy.cumsun")
    assert "numpy.cumsum" in e.value.suggestions


def test_needs_http_and_files():
    assert not resolve("statistics.median").needs_files
    assert resolve("csv.reader").needs_files
    if OS_SANDBOX:
        assert resolve("urllib.request.urlopen").needs_http
        assert resolve("os.listdir").needs_files


def test_import_policy():
    assert import_permitted("numpy", "numpy.cumsum") and import_permitted("json", "zip")
    assert import_permitted("os", "os.listdir") and not import_permitted("os", "zip")
    assert not import_permitted("subprocess", "subprocess.run")
    assert not import_permitted("tkinter", "tkinter.Tk")
    assert not import_permitted("pygame.display", "pygame.math.Vector2")


def test_menu_refuses_denied_and_explains_missing_libraries(monkeypatch):
    with pytest.raises(menu.LibraryError, match="not a library pygym can use: it needs a screen"):
        menu.load_library("tkinter")
    real = menu.importlib.util.find_spec
    monkeypatch.setattr(menu.importlib.util, "find_spec",
                        lambda name, *a: None if name.split(".")[0] == "numpy" else real(name, *a))
    with pytest.raises(menu.LibraryError) as e:
        menu.load_library("numpy")
    assert "'numpy' is not installed in this Python. Install it with:" in str(e.value)
    assert "-m pip install numpy" in str(e.value)
    with pytest.raises(menu.LibraryError, match="No library called 'nosuchlib_xyz'"):
        menu.load_library("nosuchlib_xyz")


def test_menu_command_messages_for_missing_and_denied_libraries(monkeypatch):
    console = Console(record=True, width=200)
    assert menu.main(["tkinter.Tk"], console=console) == 1
    out = console.export_text()
    assert "not a library pygym can use" in out and "Did you mean" not in out
    real = menu.importlib.util.find_spec
    monkeypatch.setattr(menu.importlib.util, "find_spec",
                        lambda name, *a: None if name.split(".")[0] == "numpy" else real(name, *a))
    console = Console(record=True, width=200)
    assert menu.main(["numpy.cumsum"], console=console) == 1
    out = console.export_text()
    assert "-m pip install numpy" in out and "Did you mean" not in out
    console = Console(record=True, width=200)
    assert menu.main(["--list", "statistcs"], console=console) == 1
    assert "Did you mean: statistics" in console.export_text()


@needs_os_sandbox
def test_menu_hides_process_functions_of_os():
    names = menu.functions_of("os")
    assert "listdir" in names and "getcwd" in names
    assert not {"system", "popen", "fork", "kill", "execv"} & set(names)
    assert menu.check_target("os.system")[0] is None


def test_menu_and_status_show_the_sandbox_level(monkeypatch):
    from pygym import config
    text = style.to_plain(menu.home_panel(config.Settings(), False))
    assert "Sandbox" in text and sandbox.label() in text
    monkeypatch.setenv("PYGYM_SANDBOX", "audit-hook")
    text = style.to_plain(menu.home_panel(config.Settings(), False))
    assert "basic (Python audit hook only)" in text
    console = Console(record=True, width=100)
    monkeypatch.setattr(menu, "_ai_ready", lambda: False)
    assert menu.main(["--status"], console=console) == 0
    out = console.export_text()
    assert "sandbox" in out and "basic (Python audit hook only)" in out


@pytest.mark.parametrize("lv, label", [("bwrap", "bubblewrap"),
                                       ("sandbox-exec", "macOS sandbox-exec")])
def test_menu_labels(monkeypatch, lv, label):
    monkeypatch.delenv("PYGYM_SANDBOX", raising=False)
    monkeypatch.setattr(sandbox, "_best", {"level": lv})
    from pygym import config
    assert label in style.to_plain(menu.home_panel(config.Settings(), False))


def test_check_exercise_new_fields():
    ex = Exercise(id="x", target_function="numpy.cumsum", difficulty=1, question_text="q",
                  function_name="f", function_signature="def f(x):", reference_solution="r",
                  test_inputs=[[1]], expected_outputs=[1], allowed_imports=["numpy", "json"])
    assert grader.check_exercise(ex) == []
    for change in ({"allowed_imports": ["subprocess"]}, {"allowed_imports": ["os"]},
                   {"files": {"../a": "x"}}, {"http_fixtures": {"nope": {"text": "x"}}}):
        assert grader.check_exercise(dataclasses.replace(ex, **change)), change
    os_ex = dataclasses.replace(ex, target_function="os.listdir", allowed_imports=["os"],
                                files={"a.txt": ""})
    assert grader.check_exercise(os_ex) == []


def test_old_cached_exercises_still_load():
    old = {"id": "a", "target_function": "zip", "difficulty": 1, "question_text": "q",
           "function_name": "f", "function_signature": "def f(x):", "reference_solution": "r",
           "test_inputs": [[1]], "expected_outputs": [1], "source": "ai", "future_field": 1}
    ex = Exercise.from_dict(old)
    assert ex.files == {} and ex.http_fixtures == {} and grader.check_exercise(ex) == []
    assert Exercise.from_dict(dataclasses.asdict(ex)) == ex


# ----- results -> comparable values ---------------------------------------------------------


@dataclasses.dataclass
class Point:
    x: int
    y: tuple


class Plain:
    def __repr__(self):
        return "plain!"


def test_norm_python_values():
    assert norm((1, (2, 3))) == [1, [2, 3]]
    assert norm({3, 1, 2}) == [1, 2, 3] and norm(frozenset({"b", "a"})) == ["a", "b"]
    assert norm({1: (1,)}) == {"1": [1]}
    assert norm(Decimal("1.50")) == "Decimal('1.50')" and norm(Fraction(1, 2)) == "Fraction(1, 2)"
    assert norm(1 + 2j) == "(1+2j)"
    assert norm(Point(1, (2,))) == {"x": 1, "y": [2]}
    assert norm(Plain()) == "<Plain> plain!"
    assert norm(x * 2 for x in range(3)) == [0, 2, 4] and norm(b"hi") == [104, 105]
    assert math.isnan(norm(float("nan")))
    assert json.loads(json.dumps(norm([Point(1, (2,)), Decimal("2"), {1, 2}])))


def test_norm_numpy():
    np = pytest.importorskip("numpy")
    for value, want, kind in [(np.int64(3), 3, int), (np.float32(0.5), 0.5, float),
                              (np.bool_(True), True, bool), (np.str_("a"), "a", str)]:
        got = norm(value)
        assert got == want and type(got) is kind
    assert norm(np.array([[1, 2], [3, 4]])) == [[1, 2], [3, 4]]
    assert norm(np.array(7)) == 7
    assert norm({"a": np.arange(3)}) == {"a": [0, 1, 2]}
    nan = norm(np.array([1.0, np.nan]))
    assert nan[0] == 1.0 and math.isnan(nan[1])
    assert grader.values_equal(nan, [1.0, float("nan")])
    assert not grader.values_equal(nan, [1.0, 2.0])


def test_norm_pandas():
    pd = pytest.importorskip("pandas")
    assert norm(pd.Series([3, 4], index=["a", "b"])) == {"a": 3, "b": 4}
    assert norm(pd.DataFrame({"x": [1, 2]})) == {"x": {"0": 1, "1": 2}}
    assert norm(pd.Index([1, 2])) == [1, 2]


def test_nan_equals_nan_only_for_nan():
    assert grader.values_equal(float("nan"), float("nan"))
    assert not grader.values_equal(float("nan"), 1.0)
    assert grader.values_equal({"a": [float("nan")]}, {"a": [float("nan")]})


# ----- grading a numpy exercise -------------------------------------------------------------


NUMPY_REF = "import numpy as np\ndef running(xs):\n    return np.cumsum(xs)"


@pytest.fixture
def cumsum_ex():
    pytest.importorskip("numpy")
    return Exercise(id="np-1", target_function="numpy.cumsum", difficulty=1, question_text="q",
                    function_name="running", function_signature="def running(xs):",
                    reference_solution=NUMPY_REF, test_inputs=[[[1, 2, 3]], [[]], [[5, -5]]],
                    expected_outputs=[[1, 3, 6], [], [5, 0]], allowed_imports=["numpy"],
                    source="ai")


@pytest.mark.parametrize("code", [
    NUMPY_REF,
    "import numpy\ndef running(xs):\n    return numpy.cumsum(xs).tolist()",
    "from numpy import cumsum\ndef running(xs):\n    return list(cumsum(xs))",
    "import numpy as np\ndef running(xs):\n    return np.array(xs).cumsum()",   # method form
])
def test_numpy_reference_and_aliases_pass(cumsum_ex, code):
    r = grader.grade(code, cumsum_ex)
    assert r.passed, r.message


def test_numpy_wrong_answer_and_missing_target(cumsum_ex):
    wrong = grader.grade("import numpy as np\ndef running(xs):\n    return np.cumsum(xs) * 2",
                         cumsum_ex)
    assert wrong.reason == "wrong_output"
    loop = ("def running(xs):\n    out, t = [], 0\n    for x in xs:\n        t += x\n"
            "        out.append(t)\n    return out")
    assert grader.grade(loop, cumsum_ex).reason == "target_not_used"
    dead = ("import numpy as np\ndef running(xs):\n    if False:\n        np.cumsum(xs)\n"
            "    out, t = [], 0\n    for x in xs:\n        t += x\n        out.append(t)\n"
            "    return out")
    r = grader.grade(dead, cumsum_ex)
    assert r.reason == "target_not_used" and "never actually called" in r.message
    other = grader.grade("import pandas\ndef running(xs):\n    cumsum = None\n    return xs",
                         cumsum_ex)
    assert not other.passed and "not allowed" in other.message


def test_numpy_ufunc_target_is_checked_statically():
    pytest.importorskip("numpy")
    ref = "import numpy as np\ndef roots(xs):\n    return np.sqrt(xs)"
    res = grader.run_in_sandbox(ref, "roots", "numpy.sqrt", [[[4, 9]]], 10.0,
                                allowed_imports=["numpy"])
    assert res["outputs"] == [{"value": [2.0, 3.0]}] and res["target_calls"] == -1
    ex = Exercise(id="np-2", target_function="numpy.sqrt", difficulty=1, question_text="q",
                  function_name="roots", function_signature="def roots(xs):",
                  reference_solution=ref, test_inputs=[[[4, 9]], [[1]]],
                  expected_outputs=[[2.0, 3.0], [1.0]], allowed_imports=["numpy"])
    assert grader.grade(ref, ex).passed
    r = grader.grade("import numpy as np\ndef roots(xs):\n    return np.add.reduce(xs)", ex)
    assert r.reason == "target_not_used"


def test_pandas_results_are_compared_as_dicts():
    pytest.importorskip("pandas")
    ref = ("import pandas as pd\ndef counts(words):\n"
           "    return pd.Series(words).value_counts().sort_index()")
    res = grader.run_in_sandbox(ref, "counts", "pandas.Series", [[["a", "b", "a"]]], 20.0,
                                allowed_imports=["pandas"])
    # pandas itself also calls pd.Series inside value_counts: those calls are counted too
    assert res["outputs"] == [{"value": {"a": 2, "b": 1}}] and res["target_calls"] >= 1


# ----- the AI engine end to end with a fake model --------------------------------------------


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)
    monkeypatch.setattr(engine, "_status_cache", {})

    def no_network(*a, **k):
        raise AssertionError("unit tests must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)

    def install(*replies):
        provider = FakeProvider(replies)
        monkeypatch.setattr(engine, "get_provider", lambda settings: provider)
        return provider
    return install


def test_engine_numpy_exercise(fake):
    pytest.importorskip("numpy")
    reply = json.dumps({
        "question_text": "Write running(sales) that returns the running totals of daily sales "
                         "with np.cumsum. Example: running([1, 2]) returns [1, 3].",
        "function_name": "running", "function_signature": "def running(sales):",
        "reference_solution": "def running(sales):\n    return np.cumsum(np.array(sales))",
        "tests": [{"sales": [1, 2, 3]}, {"sales": []}, {"sales": [4]}, {"sales": [2.5, -1]}],
        "hint": "cumsum adds as it goes."})
    provider = fake(reply)
    ex = ai.generate_exercise("numpy.cumsum", 1)
    assert ex.reference_solution.startswith("import numpy as np\n")
    assert ex.expected_outputs == [[1, 3, 6], [], [4], [2.5, 1.5]]
    assert ex.allowed_imports == ["numpy"] and ex.import_lines == ["import numpy"]
    assert ex.starter_code.startswith("import numpy\n\ndef running(sales):")
    assert grader.grade(ex.reference_solution, ex).passed
    prompt = provider.calls[0]["messages"][1]["content"]
    assert "plain JSON" in prompt and "np.array(values)" in prompt and "numpy (as np)" in prompt


def test_engine_rejects_a_reference_that_touches_the_real_system(fake, tmp_path):
    pytest.importorskip("numpy")
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    bad = json.dumps({
        "question_text": "Running totals with np.cumsum.", "function_name": "running",
        "function_signature": "def running(sales):",
        "reference_solution": (f"import numpy as np\nimport shutil\ndef running(sales):\n"
                               f"    np.save({str(tmp_path / 'out.npy')!r}, np.array(sales))\n"
                               "    return np.cumsum(sales)"),
        "tests": [{"sales": [1, 2]}, {"sales": [3]}, {"sales": []}], "hint": "h"})
    provider = fake(*[bad] * engine.MAX_ATTEMPTS)
    with pytest.raises(ai.GenerationUnavailable):
        ai.generate_exercise("numpy.cumsum", 1, timeout=60)
    repair = provider.calls[1]["messages"][-1]["content"]
    assert "shutil is not allowed" in repair
    no_shutil = bad.replace("import shutil\\n", "")
    provider = fake(*[no_shutil] * engine.MAX_ATTEMPTS)
    with pytest.raises(ai.GenerationUnavailable) as e:
        ai.generate_exercise("numpy.cumsum", 1, timeout=60)
    assert "blocked: writing to" in str(e.value)
    assert "blocked: writing to" in provider.calls[1]["messages"][-1]["content"]
    assert not (tmp_path / "out.npy").exists() and victim.read_text() == "keep"


@needs_os_sandbox
def test_engine_requests_exercise_with_http_fixtures(fake):
    pytest.importorskip("requests")
    reply = json.dumps({
        "question_text": "Write user_name(user_id) that GETs BASE_URL + '/users/<id>' with "
                         "requests and returns the user's name, or None if the server says 404.",
        "function_name": "user_name", "function_signature": "def user_name(user_id):",
        "reference_solution": ("import requests\ndef user_name(user_id):\n"
                               "    r = requests.get(BASE_URL + '/users/' + str(user_id))\n"
                               "    if r.status_code != 200:\n        return None\n"
                               "    return r.json()['name']"),
        "tests": [{"user_id": 1}, {"user_id": 2}, {"user_id": 99}],
        "http_fixtures": [{"path": "/users/1", "status": 200, "json": {"name": "Ana"}},
                          {"path": "/users/2", "status": 200, "json": {"name": "Bo"}}],
        "hint": "requests.get(url).json() parses the answer."})
    provider = fake(reply)
    ex = ai.generate_exercise("requests.get", 2, timeout=60)
    assert ex.expected_outputs == ["Ana", "Bo", None]
    assert ex.http_fixtures["/users/1"] == {"status": 200, "json": {"name": "Ana"}}
    assert grader.grade(ex.reference_solution, ex).passed
    assert not grader.grade("import requests\ndef user_name(user_id):\n"
                            "    return requests.get(BASE_URL).status_code", ex).passed
    schema = provider.calls[0]["schema"]
    assert "http_fixtures" in schema["required"]
    assert "BASE_URL" in provider.calls[0]["messages"][1]["content"]


@needs_os_sandbox
def test_engine_file_exercise_with_files(fake):
    reply = json.dumps({
        "question_text": "Write text_files(folder) that returns the sorted names of the .txt "
                         "files in folder, using os.listdir.",
        "function_name": "text_files", "function_signature": "def text_files(folder):",
        "reference_solution": ("import os\ndef text_files(folder):\n"
                               "    return sorted(n for n in os.listdir(folder) "
                               "if n.endswith('.txt'))"),
        "tests": [{"folder": "."}, {"folder": "notes"}, {"folder": "empty"}],
        "files": {"a.txt": "a", "b.md": "b", "notes/c.txt": "c", "notes/d.txt": "",
                  "empty/x.csv": ""},
        "hint": "os.listdir gives names; filter them."})
    provider = fake(reply)
    ex = ai.generate_exercise("os.listdir", 1, timeout=60)
    assert ex.expected_outputs == [["a.txt"], ["c.txt", "d.txt"], []]
    assert ex.files["notes/c.txt"] == "c" and "a.txt" in ex.question_text
    assert grader.grade(ex.reference_solution, ex).passed
    assert "files" in provider.calls[0]["schema"]["properties"]
    assert "EMPTY temporary folder" in provider.calls[0]["messages"][1]["content"]


@needs_os_sandbox
def test_engine_rejects_a_reference_that_redefines_base_url(fake):
    pytest.importorskip("requests")
    reply = json.dumps({
        "question_text": "Use BASE_URL and requests.get.", "function_name": "fetch",
        "function_signature": "def fetch(path):",
        "reference_solution": ("import requests\ndef fetch(path):\n"
                               "    BASE_URL = 'http://127.0.0.1:8000'\n"
                               "    return requests.get(BASE_URL + path).text"),
        "tests": [{"path": "/a"}, {"path": "/b"}, {"path": "/c"}],
        "http_fixtures": {"/a": {"text": "1"}, "/b": {"text": "2"}, "/c": {"text": "3"}},
        "hint": "h"})
    provider = fake(*[reply] * engine.MAX_ATTEMPTS)
    with pytest.raises(ai.GenerationUnavailable, match="do not assign BASE_URL"):
        ai.generate_exercise("requests.get", 1, timeout=60)
    assert "do not assign BASE_URL" in provider.calls[1]["messages"][-1]["content"]


@needs_os_sandbox
def test_engine_asks_for_files_when_the_reference_opens_one(fake):
    reply = json.dumps({
        "question_text": "Total of the numbers in the first file, with os.listdir.",
        "function_name": "total", "function_signature": "def total(folder):",
        "reference_solution": ("import os\ndef total(folder):\n"
                               "    name = os.listdir(folder)[0]\n"
                               "    return sum(int(x) for x in open(name))"),
        "tests": [{"folder": "a"}, {"folder": "b"}, {"folder": "c"}], "hint": "h"})
    provider = fake(*[reply] * engine.MAX_ATTEMPTS)
    with pytest.raises(ai.GenerationUnavailable, match="\"files\" is empty"):
        ai.generate_exercise("os.listdir", 1, timeout=60)
    assert len(provider.calls) == engine.MAX_ATTEMPTS


def test_engine_rejects_bad_fixtures(fake):
    pytest.importorskip("numpy")
    reply = json.dumps({
        "question_text": "q np.cumsum", "function_name": "running",
        "function_signature": "def running(xs):",
        "reference_solution": "import numpy as np\ndef running(xs):\n    return np.cumsum(xs)",
        "tests": [{"xs": [1]}, {"xs": [1, 2]}, {"xs": []}], "files": {"/etc/passwd": "x"},
        "hint": "h"})
    provider = fake(*[reply] * engine.MAX_ATTEMPTS)
    with pytest.raises(ai.GenerationUnavailable, match="simple relative path"):
        ai.generate_exercise("numpy.cumsum", 1, timeout=60)
    assert len(provider.calls) == engine.MAX_ATTEMPTS
