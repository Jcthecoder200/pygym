"""Tips in the IPython terminal shell (a real InteractiveShell, in a child process so this test
process keeps its own sys.excepthook / displayhook): the tip comes after IPython's traceback,
the traceback is unchanged, %tb / sys.last_* / error_in_exec behave as without pygym, another
custom exception handler keeps working, %load_ext pygym and pygym.tips(False)."""
import os
import subprocess
import sys

import pytest

pytest.importorskip("IPython")

SHELL = r'''
import sys
from IPython.core.interactiveshell import InteractiveShell
ip = InteractiveShell.instance(colors="nocolor")
def mark(text):
    print("=====" + text, flush=True)
'''


def _ipython(tmp_path, body, **env_extra):
    script = tmp_path / "shell.py"
    script.write_text(SHELL + body, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("PYGYM_NO_TIPS", "NO_COLOR")}
    env.update(PYGYM_HOME=str(tmp_path / "home"), PYGYM_NO_AI="1",
               IPYTHONDIR=str(tmp_path / "ipython"))
    env.update(env_extra)
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env,
                          cwd=str(tmp_path), timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    parts = {}
    for chunk in proc.stdout.split("=====")[1:]:
        name, _, text = chunk.partition("\n")
        parts[name] = text
    return parts


ZIP_CELLS = r'''
ip.run_cell("{first}")
mark("zip")
r = ip.run_cell("list(zip([1, 2], 3))")
mark("state")
print(type(r.error_in_exec).__name__, type(sys.last_value).__name__,
      type(ip.last_execution_result.error_in_exec).__name__)
mark("tb")
ip.run_cell("%tb")
mark("end")
'''


def test_traceback_then_tip_and_ipython_state_unchanged(tmp_path):
    with_tips = _ipython(tmp_path, ZIP_CELLS.format(first="import pygym"))
    without = _ipython(tmp_path, ZIP_CELLS.format(first="import pygym"), PYGYM_NO_TIPS="1")
    lines = with_tips["zip"].rstrip("\n").splitlines()
    assert lines[-1] == ("pygym: this line uses zip(); 30-second exercise? pygym.exercise('zip')  "
                         "(turn off: pygym --tips off)")
    assert "\n".join(lines[:-1]) == without["zip"].rstrip("\n")     # the same traceback
    assert "TypeError" in lines[-2]
    assert with_tips["state"] == without["state"] == "TypeError TypeError TypeError\n"
    assert with_tips["tb"] == without["tb"] and "TypeError" in with_tips["tb"]


def test_from_pygym_import_exercise_gives_exercise(tmp_path):
    out = _ipython(tmp_path, ZIP_CELLS.format(first="from pygym import exercise"))
    assert out["zip"].rstrip().splitlines()[-1].startswith(
        "pygym: this line uses zip(); 30-second exercise? exercise('zip')")


def test_load_ext_and_tips_off(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.json").write_text('{"tips": false}')           # import alone: no tips
    body = r'''
ip.run_cell("import pygym")
mark("off")
ip.run_cell("max([])")
ip.run_cell("%load_ext pygym")
mark("loaded")
ip.run_cell("max([])")
ip.run_cell("%load_ext pygym")
ip.run_cell("import importlib\nreloaded = importlib.reload(pygym)")
mark("twice")
ip.run_cell("max([])")
ip.run_cell("%unload_ext pygym")
mark("unloaded")
ip.run_cell("max([])")
print(ip.custom_exceptions)
mark("end")
'''
    out = _ipython(tmp_path, body)
    assert "ValueError" in out["off"] and "pygym:" not in out["off"]
    assert out["loaded"].count("pygym: this line uses max();") == 1
    assert out["loaded"].index("ValueError") < out["loaded"].index("pygym: this line uses max();")
    assert out["twice"].count("pygym:") == 1
    assert "pygym:" not in out["unloaded"] and out["unloaded"].rstrip().endswith("()")


def test_an_existing_custom_exception_handler_keeps_working(tmp_path):
    body = r'''
def custom(shell, etype, value, tb, tb_offset=None):
    print("CUSTOM HANDLER", etype.__name__)
ip.set_custom_exc((KeyError,), custom)
before = ip.CustomTB
ip.run_cell("import pygym")
mark("key")
ip.run_cell("d = {}\nlen(d['missing'])")
mark("type")
ip.run_cell("sum(['a'])")
ip.run_cell("pygym.tips(False)")
mark("restored")
print(ip.custom_exceptions == (KeyError,), ip.CustomTB is before)
mark("end")
'''
    out = _ipython(tmp_path, body)
    assert out["key"].splitlines()[0] == "CUSTOM HANDLER KeyError"
    assert out["key"].rstrip().splitlines()[-1].startswith("pygym: this line uses len();")
    assert "Traceback" not in out["key"]
    assert "TypeError" in out["type"] and "pygym: this line uses sum();" in out["type"]
    assert out["restored"].strip().endswith("True True")


def test_errors_inside_a_cell_function_and_syntax_errors(tmp_path):
    body = r'''
ip.run_cell("import pygym")
mark("func")
ip.run_cell("def avg(xs):\n    return sum(xs) / len(xs)\n\navg([])")
mark("syntax")
ip.run_cell("zip(1 2)")
mark("end")
'''
    out = _ipython(tmp_path, body)
    assert "pygym: this line uses sum() and len();" in out["func"]
    assert "SyntaxError" in out["syntax"] and "pygym:" not in out["syntax"]
