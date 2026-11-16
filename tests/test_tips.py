"""pygym.tips: which functions a failing line uses, the command shown, and the hooks.

The sources run here are registered in linecache under "<tips-test-N>" names, as IPython and the
3.13+ REPL do with their cells, so the analysis reads them like real student code.
"""
import io
import itertools
import linecache
import os
import sys

import pytest

import pygym
from pygym import config, tips

PY311 = sys.version_info >= (3, 11)
_counter = itertools.count()


def _register(src: str) -> str:
    name = f"<tips-test-{next(_counter)}>"
    linecache.cache[name] = (len(src), None, src.splitlines(True), name)
    return name


def run(src: str, ns=None, registered=None):
    """Run `src` as student code; return the exception it raised."""
    name = _register(src)
    if registered is not None:            # what linecache shows differs from what ran
        linecache.cache[name] = (len(registered), None, registered.splitlines(True), name)
    code = compile(src, name, "exec")
    namespace = {"__name__": "__main__"} if ns is None else ns
    try:
        exec(code, namespace)
    except BaseException as e:  # noqa: BLE001
        return e
    raise AssertionError("the code did not fail")


def found(src, mode="preset", **kw):
    e = run(src, **kw)
    return tips.find(type(e), e, e.__traceback__, mode=mode)


def names(src, mode="preset", **kw):
    return [t for t, _k in found(src, mode, **kw)]


@pytest.fixture
def state(monkeypatch):
    """A clean tips state, restored afterwards (also sys.excepthook)."""
    saved = dict(tips._S)
    hook = sys.excepthook
    tips._S.update(override=True, noted=True, last=None)
    monkeypatch.setattr(tips, "_shell", lambda: None)   # other tests may leave an IPython shell
    yield tips._S
    sys.excepthook = hook
    tips._S.clear()
    tips._S.update(saved)


# ----- 1. analysis ---------------------------------------------------------------------------


def test_presets_come_from_the_dataset():
    from pygym import loader
    assert tips._presets() == frozenset(loader.available_functions())
    assert {"zip", "max", "sum", "sorted", "len", "range"} <= tips._presets()


def test_zip_length_mismatch():
    assert found("a = [1, 2]\nb = [1]\npairs = list(zip(a, b, strict=True))\n") == [("zip", "preset")]


def test_zip_with_a_number():
    assert names("pairs = dict(zip(['a', 'b'], 5))\n") == ["zip"]


def test_max_of_empty_list():
    assert names("scores = []\nbest = max(scores)\n") == ["max"]


def test_sum_of_strings():
    assert names("words = ['a', 'b']\ntotal = sum(words)\n") == ["sum"]


def test_multi_line_sum_reported_on_the_first_line():
    assert names("data = ['a', 'b']\ntotal = sum(\n    x for x in data\n)\n") == ["sum"]


def test_multi_line_sum_reported_on_an_inner_line():
    # int('a') fails inside the generator: the frame's line is line 3, inside sum( ... )
    assert names("data = ['a', 'b']\ntotal = sum(\n    int(x) for x in data\n)\n") == ["sum"]


def test_for_header_uses_only_the_header():
    src = "for a, b in zip([1, 2], [3], strict=True):\n    n = len(str(a))\n"
    assert names(src) == ["zip"]


def test_error_in_a_loop_body_ignores_the_header():
    src = "data = [[1], 'ab']\nfor row in sorted(data, key=len):\n    total = sum(row)\n"
    assert names(src) == ["sum"]


def test_error_inside_a_sorted_key_lambda():
    assert names("people = [('a', 1), ('b', 'x')]\nout = sorted(people, key=lambda p: p[1] + 1)\n") \
        == ["sorted"]


def test_error_inside_a_user_function_analyses_that_line():
    src = "def avg(xs):\n    return sum(xs) / len(xs)\n\nprint(round(avg([])))\n"
    assert names(src) == ["sum", "len"]


def test_shadowed_builtin_is_not_suggested():
    assert names("sum = 0\ntotal = sum([1, 2])\n") == []
    assert names("def max(*a):\n    raise ValueError('mine')\nbest = max([1])\n") == []
    assert names("def f():\n    len = 3\n    return len([1])\nf()\n") == []


def test_builtin_imported_from_builtins_still_counts():
    assert names("from builtins import zip\nlist(zip([1], 5))\n") == ["zip"]


def test_range_len_ranks_the_failing_call_first():
    # len(5) fails: on 3.11+ by its position, on 3.9/3.10 by the message ("... has no len()")
    assert names("x = 5\nfor i in range(len(x)):\n    pass\n") == ["len", "range"]


@pytest.mark.skipif(not PY311, reason="needs the error position (3.11+); 3.10's message names no function")
def test_nested_calls_rank_the_innermost_failing_call_first():
    assert names("out = sorted(zip([1], 5))\n") == ["zip", "sorted"]


def test_at_most_three_names():
    src = "x = sum(map(abs, [1])) + len(sorted(zip([1], 5)))\n"
    assert len(names(src)) == 3


def test_no_builtin_on_the_line():
    assert names("d = {}\nvalue = d['missing']\n") == []


def test_syntax_errors_and_exits_are_skipped():
    for exc in (SyntaxError("bad", ("<x>", 1, 1, "zip(")), IndentationError("bad"),
                KeyboardInterrupt(), SystemExit(1), GeneratorExit()):
        try:
            raise exc
        except BaseException as e:  # noqa: BLE001
            assert tips.find(type(e), e, e.__traceback__, mode="preset") == []


def test_pygym_exercise_unavailable_is_skipped():
    from pygym.loader import ExerciseUnavailable
    e = run("raise ExerciseUnavailable(str(len('x')))\n",
            ns={"__name__": "__main__", "ExerciseUnavailable": ExerciseUnavailable})
    assert tips.find(type(e), e, e.__traceback__, mode="preset") == []


def test_no_user_frame_no_tip():
    try:
        import json
        json.loads("{")
    except ValueError as e:
        tb = e.__traceback__.tb_next      # only json's own frames
        assert tips.find(type(e), e, tb, mode="preset") == []


def test_no_source_no_tip():
    e = None
    try:
        exec(compile("list(zip([1], 5))", "<string>", "exec"), {})
    except TypeError as err:
        e = err
    assert tips.find(type(e), e, e.__traceback__, mode="preset") == []


def test_error_deep_in_the_standard_library_analyses_the_user_line():
    src = "import statistics\nvalues = []\nm = round(statistics.mean(values))\n"
    assert found(src, "preset") == [("round", "preset")]
    assert found(src, "ai") == [("statistics.mean", "ai"), ("round", "preset")]


@pytest.mark.parametrize("src", [
    "import statistics\nm = statistics.median([])\n",
    "import statistics as st\nm = st.median([])\n",
    "from statistics import median\nm = median([])\n",
    "from statistics import median as med\nm = med([])\n",
])
def test_library_functions_only_in_ai_mode(src):
    assert found(src, "ai") == [("statistics.median", "ai")]
    assert found(src, "preset") == []


def test_ai_mode_follows_the_saved_setting():
    src = "import statistics\nm = statistics.median([])\n"
    e = run(src)
    config.save(config.Settings(mode="ai"))
    assert tips.find(type(e), e, e.__traceback__) == [("statistics.median", "ai")]
    config.save(config.Settings(mode="preset"))
    assert tips.find(type(e), e, e.__traceback__) == []


def test_methods_are_never_suggested():
    assert names("''.join([1])\n", "ai") == []
    assert names("data = [3]\ndata.index(5)\n", "ai") == []
    assert names("import collections\nc = collections.Counter()\nc.most_common('x')\n", "ai") == []
    assert names("import collections\ncollections.Counter.most_common(None)\n", "ai") == []


def test_refused_libraries_are_not_suggested_in_ai_mode():
    # os is a file library: never suggested (the strictest sandbox level is assumed)
    assert names("import os\nos.path.join(1)\n", "ai") == []


def test_user_modules_are_not_library_functions(tmp_path, monkeypatch):
    (tmp_path / "helpers_tips_test.py").write_text("def compute(x):\n    return x + 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert names("import helpers_tips_test\nhelpers_tips_test.compute('a')\n", "ai") == []
    assert names("from helpers_tips_test import compute\ncompute('a')\n", "ai") == []
    sys.modules.pop("helpers_tips_test", None)


def test_the_tokenizer_is_used_when_the_file_does_not_parse():
    ran = "values = []\nbest = max(values)\n"
    shown = "values = [\nbest = max(values) + len(x.y(1))\n"     # line 1 is broken now
    e = run(ran, registered=shown)
    assert [t for t, _ in tips.find(type(e), e, e.__traceback__, mode="preset")] == ["max", "len"]


def test_tokenizer_skips_methods_and_definitions():
    calls = tips._calls_from_tokens("def zip(a): x().len(1) + ''.join(b) + max(c)\n", 1)
    assert [c.chain for c in calls] == [("x",), ("max",)]      # not zip (def), len, join
    calls = tips._calls_from_tokens("y = np.linalg.norm(v)\n", 1)
    assert [c.chain for c in calls] == [("np", "linalg", "norm")]


def test_same_error_same_tip_every_time():
    src = "list(zip([1], 5))\n"
    results = {tuple(names(src)) for _ in range(5)}
    assert results == {("zip",)}


def test_user_file_rules():
    assert tips.is_user_file("<stdin>") and tips.is_user_file("<python-input-3>")
    assert tips.is_user_file("<pyshell#2>") and tips.is_user_file("/tmp/ipykernel_1/123.py")
    assert not tips.is_user_file("<frozen runpy>")
    assert not tips.is_user_file(os.__file__)
    assert not tips.is_user_file(tips.__file__)
    assert not tips.is_user_file("/x/lib/python3.12/site-packages/numpy/core.py")
    assert not tips.is_user_file("/usr/lib/python3/dist-packages/foo.py")


def test_classic_repl_source_comes_from_readline_history(monkeypatch):
    class FakeReadline:
        items = ["x = 1", "for a in zip([1], 5):", "    print(a)"]

        def get_current_history_length(self):
            return len(self.items)

        def get_history_item(self, i):
            return self.items[i - 1]

    monkeypatch.setitem(sys.modules, "readline", FakeReadline())
    assert tips._readline_lines(1) == ["for a in zip([1], 5):\n", "    print(a)\n"]
    FakeReadline.items = ["values = []", "max(values)"]
    assert tips._readline_lines(1) == ["max(values)\n"]
    monkeypatch.delitem(sys.modules, "readline")
    assert tips._readline_lines(1) == []


def test_idle_source_comes_from_the_gui_process(monkeypatch):
    import types

    class Handler:
        def remotecall(self, oid, method, args, kwargs):
            assert (oid, method, args) == ("linecache", "getlines", ("<pyshell#4>",))
            return ["best = max([])\n"]

    run_mod = types.ModuleType("idlelib.run")
    run_mod.rpc = types.SimpleNamespace(
        objecttable={"exec": types.SimpleNamespace(rpchandler=Handler())})
    monkeypatch.setitem(sys.modules, "idlelib.run", run_mod)
    assert tips._idle_lines("<pyshell#4>") == ["best = max([])\n"]


# ----- 2. the command ------------------------------------------------------------------------


def test_interactive_command_uses_what_is_in_the_namespace():
    cmd = tips.command_for
    assert cmd("zip", interactive=True, namespace={"exercise": pygym.exercise}) == "exercise('zip')"
    assert cmd("zip", interactive=True, namespace={"ex": pygym.exercise}) == "ex('zip')"
    assert cmd("zip", interactive=True, namespace={"pygym": pygym}) == "pygym.exercise('zip')"
    assert cmd("zip", interactive=True, namespace={"pg": pygym}) == "pg.exercise('zip')"
    assert cmd("zip", interactive=True, namespace={"pg": pygym, "pygym": pygym}) \
        == "pygym.exercise('zip')"
    assert cmd("zip", interactive=True, namespace={}) == "from pygym import exercise; exercise('zip')"
    assert cmd("zip", interactive=True, namespace={"exercise": print, "_": pygym}) \
        == "from pygym import exercise; exercise('zip')"
    assert cmd("statistics.median", interactive=True, namespace={"exercise": pygym.exercise}) \
        == "exercise('statistics.median')"


def _fake_exe(folder, name):
    folder.mkdir(exist_ok=True)
    path = folder / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_script_command_with_pygym_on_path(tmp_path, monkeypatch):
    _fake_exe(tmp_path / "bin", "pygym.exe" if os.name == "nt" else "pygym")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    assert tips.command_for("zip", interactive=False) == "pygym zip"


def test_script_command_without_pygym_on_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    exe = sys.executable
    expected = f'"{exe}"' if " " in exe else exe
    assert tips.command_for("zip", interactive=False) == f"{expected} -m pygym zip"


@pytest.mark.skipif(os.name == "nt", reason="symlinks")
def test_script_command_uses_the_short_name_when_it_is_this_python(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    base = os.path.basename(sys.executable)
    os.symlink(sys.executable, bin_dir / base)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert tips.command_for("zip", interactive=False) == f"{base} -m pygym zip"


def test_interactive_detection(monkeypatch):
    monkeypatch.delattr(sys, "ps1", raising=False)
    monkeypatch.delitem(sys.modules, "idlelib.run", raising=False)
    if not sys.flags.interactive:
        assert not tips._interactive(None)
    monkeypatch.setattr(sys, "ps1", ">>> ", raising=False)
    assert tips._interactive(None)
    monkeypatch.delattr(sys, "ps1")
    monkeypatch.setitem(sys.modules, "idlelib.run", object())
    assert tips._interactive(None)
    assert tips._interactive(object())       # any IPython shell


def test_tip_text():
    tip = tips.Tip([("zip", "preset"), ("sorted", "preset")], "exercise('zip')")
    assert tip.line() == "pygym: this line uses zip() and sorted(); 30-second exercise? exercise('zip')"
    assert tip.line(note=True).endswith("(turn off: pygym --tips off)")
    tip = tips.Tip([("statistics.median", "ai")], "exercise('statistics.median')")
    assert tip.text == ("this line uses statistics.median(); AI exercise (local model)? "
                        "exercise('statistics.median')")
    three = tips.Tip([("a", "preset"), ("b", "preset"), ("c", "preset")], "x")
    assert "a(), b() and c()" in three.text
    assert "<code>a()</code>, <code>b()</code> and <code>c()</code>" in three.html()
    html = tip.html()
    assert "background" not in html and "color" not in html          # follows the theme
    assert "<code style=\"font-weight:bold\">exercise(&#x27;statistics.median&#x27;)</code>" in html


# ----- 3. hooks ------------------------------------------------------------------------------


def _error():
    return run("pairs = list(zip([1, 2], 3))\n")


def _call_hook(hook, e, capsys):
    hook(type(e), e, e.__traceback__)
    return capsys.readouterr().err


def test_hook_chains_to_the_hook_that_was_there(state, capsys, monkeypatch):
    monkeypatch.delattr(sys, "ps1", raising=False)
    seen = []

    def theirs(t, v, tb):
        seen.append(t)
        sys.stderr.write("THEIR TRACEBACK\n")

    sys.excepthook = theirs
    tips.install(shell=None)
    assert sys.excepthook is not theirs and sys.excepthook._pygym_previous is theirs
    err = _call_hook(sys.excepthook, _error(), capsys)
    assert seen == [TypeError]
    assert err.startswith("THEIR TRACEBACK\n")
    assert err.splitlines()[-1].startswith("pygym: this line uses zip(); 30-second exercise? ")


def test_install_twice_is_one_hook_and_one_tip(state, capsys):
    sys.excepthook = sys.__excepthook__
    tips.install(shell=None)
    first = sys.excepthook
    tips.install(shell=None)
    pygym.load_ipython_extension  # noqa: B018  (exists)
    assert sys.excepthook is first
    err = _call_hook(sys.excepthook, _error(), capsys)
    assert err.count("pygym:") == 1


def test_uninstall_restores_what_was_there(state):
    def theirs(t, v, tb):
        pass
    sys.excepthook = theirs
    tips.install(shell=None)
    tips.uninstall(shell=None)
    assert sys.excepthook is theirs
    tips.uninstall(shell=None)               # twice: still fine
    assert sys.excepthook is theirs


def test_tips_false_and_true_from_the_package(state, capsys):
    sys.excepthook = sys.__excepthook__
    pygym.tips(False)
    assert sys.excepthook is sys.__excepthook__ and not tips.enabled()
    pygym.tips(True)
    assert getattr(sys.excepthook, "_pygym_tips", None) == "import" and tips.enabled()
    out = capsys.readouterr().out
    assert "off for this session" in out and "on for this session" in out
    import pygym.tips as module               # the module and the function are one object
    assert module is pygym.tips and callable(pygym.tips)


def test_an_internal_failure_is_swallowed_and_the_traceback_stays(state, capsys, monkeypatch):
    sys.excepthook = sys.__excepthook__
    tips.install(shell=None)

    def broken(*a, **k):
        raise RuntimeError("tips bug")
    monkeypatch.setattr(tips, "make_tip", broken)
    err = _call_hook(sys.excepthook, _error(), capsys)
    assert "TypeError" in err and "Traceback" in err
    assert "pygym:" not in err and "tips bug" not in err


def test_output_order_traceback_first(state, capsys):
    sys.excepthook = sys.__excepthook__
    tips.install(shell=None)
    err = _call_hook(sys.excepthook, _error(), capsys)
    lines = err.splitlines()
    assert lines[0] == "Traceback (most recent call last):"
    assert lines[-2].startswith("TypeError")
    assert lines[-1].startswith("pygym: ")


def test_one_tip_per_error_even_with_two_hooks(state, capsys):
    e = _error()
    tips._from_global_hook(lambda *a: None, type(e), e, e.__traceback__)
    tips._after(type(e), e, e.__traceback__)
    assert capsys.readouterr().err.count("pygym:") == 1
    e2 = _error()                               # a new error: a new tip
    tips._after(type(e2), e2, e2.__traceback__)
    assert capsys.readouterr().err.count("pygym:") == 1


def test_turn_off_note_only_on_the_first_tip(state, capsys):
    state["noted"] = False
    for _ in range(3):
        e = _error()
        tips._after(type(e), e, e.__traceback__)
    err = capsys.readouterr().err
    assert err.count("pygym --tips off") == 1
    assert err.splitlines()[0].endswith("(turn off: pygym --tips off)")


def test_an_encoding_hostile_console_does_not_crash(state):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    tip = tips.Tip([("zip", "preset")], "пygym.exercise('zip') → 中")
    tips.show(tip, stream=stream)
    stream.flush()
    text = raw.getvalue().decode("cp1252")
    assert text.startswith("pygym: this line uses zip(); ") and "?ygym.exercise('zip')" in text


class _Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


@pytest.mark.skipif(os.name == "nt", reason="ANSI depends on the Windows console mode")
def test_colour_only_on_a_terminal_without_no_color(state, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delitem(sys.modules, "idlelib.run", raising=False)
    tip = tips.Tip([("zip", "preset")], "exercise('zip')")
    tty = _Stream(True)
    tips.show(tip, stream=tty)
    assert "\x1b[2mpygym:\x1b[0m" in tty.getvalue() and "\x1b[1mexercise('zip')\x1b[0m" in tty.getvalue()
    pipe = _Stream(False)
    tips.show(tip, stream=pipe)
    assert "\x1b" not in pipe.getvalue()
    monkeypatch.setenv("NO_COLOR", "1")
    tty = _Stream(True)
    tips.show(tip, stream=tty)
    assert "\x1b" not in tty.getvalue()
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.setitem(sys.modules, "idlelib.run", object())      # IDLE's files say isatty()
    tty = _Stream(True)
    tips.show(tip, stream=tty)
    assert "\x1b" not in tty.getvalue()


def test_idle_shows_its_own_traceback_before_the_tip(state, monkeypatch, capsys):
    import types
    calls = []
    run_mod = types.ModuleType("idlelib.run")
    run_mod.print_exception = lambda: calls.append("idle traceback")
    monkeypatch.setitem(sys.modules, "idlelib.run", run_mod)
    e = _error()
    try:
        raise e
    except TypeError:
        tips._call_previous(sys.__excepthook__, type(e), e, e.__traceback__)
    assert calls == ["idle traceback"]
    assert capsys.readouterr().err == ""


def test_exercise_after_a_tip_in_idle_does_not_pick_the_terminal_editor(monkeypatch):
    from pygym import editor
    monkeypatch.setitem(sys.modules, "idlelib.run", object())
    monkeypatch.setattr(sys, "stdin", _Stream(True))     # IDLE's files say isatty() == True
    monkeypatch.setattr(sys, "stdout", _Stream(True))
    assert editor.editor_available() is False


def test_enabled_rules(state, monkeypatch):
    state["override"] = None
    assert tips.enabled()                                   # the default setting is on
    config.save(config.Settings(tips=False))
    assert not tips.enabled()
    state["override"] = True
    assert tips.enabled()
    monkeypatch.setenv("PYGYM_NO_TIPS", "1")
    assert not tips.enabled()


def test_import_does_not_install_when_the_setting_is_off(state):
    sys.excepthook = sys.__excepthook__
    state["override"] = None
    config.save(config.Settings(tips=False))
    tips._auto()
    assert sys.excepthook is sys.__excepthook__
    config.save(config.Settings(tips=True))
    tips._auto()
    assert getattr(sys.excepthook, "_pygym_tips", None) == "import"


def test_the_global_hook_counts_as_installed(state):
    def global_hook(t, v, tb):
        pass
    global_hook._pygym_tips = "global"
    sys.excepthook = global_hook
    tips.install(shell=None)
    assert sys.excepthook is global_hook                   # not wrapped a second time
    tips.uninstall(shell=None)
    assert sys.excepthook is global_hook                   # and not removed by tips(False)


# ----- config ---------------------------------------------------------------------------------


def test_tips_setting_is_validated_like_the_others():
    assert config.Settings().tips is True
    with pytest.raises(ValueError):
        config.save(config.Settings(tips="yes"))
    path = config._path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"tips": "no", "mode": "ai"}', encoding="utf-8")
    loaded = config.load()
    assert loaded.tips is True and loaded.mode == "ai"
    path.write_text('{"tips": false}', encoding="utf-8")
    assert config.load().tips is False
