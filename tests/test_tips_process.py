"""Tips in real processes: a script with `import pygym`, the global hook of `pygym --tips on`
(without any import), `--tips off / status`, and pygym's sandbox children (never a tip).

Every path is a temporary folder: PYGYM_HOME (conftest), PYGYM_TIPS_SITE_DIR for the .pth and
IPYTHONDIR for the startup file. The real site-packages and ~/.ipython are never touched.
"""
import io
import json
import os
import subprocess
import sys

import pytest
from rich.console import Console

from pygym import config, menu, sandbox, tips

STUDENT = """\
names = ["Ana", "Bo", "Cy"]
scores = [90, 80]
pairs = list(zip(names, 5))
"""


def _env(tmp_path, **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYGYM_NO_TIPS", "NO_COLOR", "PYTHONSTARTUP", "PYGYM_TIPS_SITE_DIR")}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    env.update(PYGYM_HOME=str(tmp_path / "home"), PYGYM_NO_AI="1",
               IPYTHONDIR=str(tmp_path / "ipython"), PATH=str(bin_dir))
    env.update(extra)
    return env


def _fake_pygym_on_path(tmp_path):
    exe = tmp_path / "bin" / ("pygym.exe" if os.name == "nt" else "pygym")
    exe.parent.mkdir(exist_ok=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)


def _run(args, env, cwd):
    return subprocess.run([sys.executable] + args, capture_output=True, text=True, env=env,
                          cwd=str(cwd), timeout=60)


def _save_setting(tmp_path, **values):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / "config.json").write_text(json.dumps(values), encoding="utf-8")


# ----- a script with import pygym --------------------------------------------------------------


def test_script_traceback_then_tip(tmp_path):
    _fake_pygym_on_path(tmp_path)
    script = tmp_path / "student.py"
    script.write_text("import pygym\n" + STUDENT)
    proc = _run([str(script)], _env(tmp_path), tmp_path)
    lines = proc.stderr.splitlines()
    assert proc.returncode == 1
    assert lines[0] == "Traceback (most recent call last):"
    assert lines[-2].startswith("TypeError")
    assert lines[-1] == ("pygym: this line uses zip(); 30-second exercise? pygym zip  "
                         "(turn off: pygym --tips off)")


def test_script_without_pygym_on_path_shows_python_m(tmp_path):
    script = tmp_path / "student.py"
    script.write_text("from pygym import exercise\n" + STUDENT)
    proc = _run([str(script)], _env(tmp_path), tmp_path)
    # A Python path with a space (a Windows user name like "Jason Choy") is shown in quotes.
    exe = f'"{sys.executable}"' if " " in sys.executable else sys.executable
    assert proc.stderr.splitlines()[-1].startswith(
        f"pygym: this line uses zip(); 30-second exercise? {exe} -m pygym zip")


def test_script_with_the_setting_off_has_no_tip(tmp_path):
    _save_setting(tmp_path, tips=False)
    script = tmp_path / "student.py"
    script.write_text("import pygym\n" + STUDENT)
    proc = _run([str(script)], _env(tmp_path), tmp_path)
    assert "TypeError" in proc.stderr and "pygym:" not in proc.stderr


def test_script_with_pygym_no_tips_has_no_tip(tmp_path):
    script = tmp_path / "student.py"
    script.write_text("import pygym\n" + STUDENT)
    proc = _run([str(script)], _env(tmp_path, PYGYM_NO_TIPS="1"), tmp_path)
    assert "TypeError" in proc.stderr and "pygym:" not in proc.stderr


def test_the_pygym_command_itself_shows_no_tip_for_its_own_errors(tmp_path):
    proc = _run(["-m", "pygym", "--list", "no_such_library_xyz"], _env(tmp_path), tmp_path)
    assert "pygym: this line uses" not in proc.stdout + proc.stderr


# ----- pygym --tips on / off / status --------------------------------------------------------


@pytest.fixture
def global_dirs(tmp_path, monkeypatch):
    site_dir = tmp_path / "site"
    monkeypatch.setenv("PYGYM_TIPS_SITE_DIR", str(site_dir))
    monkeypatch.setenv("IPYTHONDIR", str(tmp_path / "ipython"))
    return site_dir, tmp_path / "ipython" / "profile_default" / "startup" / tips.STARTUP_NAME


def _cli(*args):
    out = io.StringIO()
    code = menu.main(list(args), console=Console(file=out, width=200, color_system=None))
    return code, out.getvalue()


def _has_ipython():
    import importlib.util
    return importlib.util.find_spec("IPython") is not None


def test_tips_on_writes_both_files_and_saves_the_setting(global_dirs):
    site_dir, startup = global_dirs
    config.save(config.Settings(tips=False))
    code, out = _cli("--tips", "on")
    assert code == 0
    pth = site_dir / tips.PTH_NAME
    assert pth.read_text(encoding="utf-8") == tips.PTH_TEXT
    assert str(pth) in out
    assert config.load().tips is True
    if _has_ipython():
        assert startup.read_text(encoding="utf-8") == tips.STARTUP_TEXT and str(startup) in out
    code, out = _cli("--tips", "status")
    assert code == 0 and "on (import pygym" in out and str(pth) in out
    code, out = _cli("--status")
    assert "tips hook" in out and str(pth) in out


def test_tips_off_removes_both_files_and_saves_off(global_dirs):
    site_dir, startup = global_dirs
    _cli("--tips", "on")
    code, out = _cli("--tips", "off")
    assert code == 0
    assert not (site_dir / tips.PTH_NAME).exists() and not startup.exists()
    assert config.load().tips is False
    assert "removed" in out
    code, out = _cli("--tips", "status")
    assert "off (saved" in out and "not global" in out


def test_the_pth_line_is_one_import_line_that_does_not_import_pygym():
    code_lines = [ln for ln in tips.PTH_TEXT.splitlines() if not ln.startswith("#")]
    assert len(code_lines) == 1 and code_lines[0].startswith("import sys; exec(")
    assert "import pygym" not in code_lines[0].split("exec(")[0]


def _install_global(tmp_path):
    env = _env(tmp_path, PYGYM_TIPS_SITE_DIR=str(tmp_path / "site"))
    proc = _run(["-m", "pygym", "--tips", "on"], env, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "site" / tips.PTH_NAME).exists()
    return env


SITE_SCRIPT = """\
import site, sys
site.addsitedir({site!r})
print("pygym imported before the error:", "pygym" in sys.modules, flush=True)
""" + STUDENT


def test_global_hook_shows_the_tip_without_importing_pygym(tmp_path):
    env = _install_global(tmp_path)
    script = tmp_path / "student.py"
    script.write_text(SITE_SCRIPT.format(site=str(tmp_path / "site")))
    proc = _run([str(script)], env, tmp_path)
    assert "pygym imported before the error: False" in proc.stdout
    lines = proc.stderr.splitlines()
    assert lines[0] == "Traceback (most recent call last):"
    assert lines[-2].startswith("TypeError")
    assert lines[-1].startswith("pygym: this line uses zip(); 30-second exercise? ")
    assert "-m pygym zip" in lines[-1]
    assert proc.stderr.count("pygym:") == 1


def test_global_hook_plus_import_pygym_is_still_one_tip(tmp_path):
    env = _install_global(tmp_path)
    script = tmp_path / "student.py"
    script.write_text(SITE_SCRIPT.format(site=str(tmp_path / "site")) .replace(
        "names = ", "import pygym\nnames = "))
    proc = _run([str(script)], env, tmp_path)
    assert proc.stderr.count("pygym:") == 1


def test_global_hook_without_pygym_installed_is_harmless(tmp_path):
    env = _install_global(tmp_path)
    script = tmp_path / "student.py"
    script.write_text(SITE_SCRIPT.format(site=str(tmp_path / "site")))
    # -S: the virtual environment's site-packages (where pygym is) is not on the path
    proc = _run(["-S", str(script)], env, tmp_path)
    assert proc.returncode == 1
    assert proc.stderr.splitlines()[-1].startswith("TypeError")
    assert "pygym:" not in proc.stderr and "excepthook" not in proc.stderr


def test_global_hook_respects_the_setting_off(tmp_path):
    env = _install_global(tmp_path)
    _save_setting(tmp_path, tips=False)
    script = tmp_path / "student.py"
    script.write_text(SITE_SCRIPT.format(site=str(tmp_path / "site")))
    proc = _run([str(script)], env, tmp_path)
    assert "TypeError" in proc.stderr and "pygym:" not in proc.stderr


def test_startup_file_is_silent_without_pygym(tmp_path):
    path = tmp_path / tips.STARTUP_NAME
    path.write_text(tips.STARTUP_TEXT, encoding="utf-8")
    proc = _run(["-S", "-c", f"exec(open({str(path)!r}).read()); print(sorted(k for k in globals() "
                             f"if 'pygym' in k))"], _env(tmp_path), tmp_path)
    assert proc.returncode == 0 and proc.stderr == "" and proc.stdout.strip() == "[]"


# ----- the sandbox -----------------------------------------------------------------------------


def test_sandbox_children_are_marked():
    assert sandbox.child_env()["PYGYM_NO_TIPS"] == "1"


def test_sandbox_child_environment_never_shows_a_tip_with_the_global_hook(tmp_path):
    _install_global(tmp_path)
    script = tmp_path / "student.py"
    script.write_text(SITE_SCRIPT.format(site=str(tmp_path / "site")))
    child = dict(sandbox.child_env(), PYGYM_HOME=str(tmp_path / "home"))
    proc = _run(["-E", "-B", str(script)], child, tmp_path)
    assert "TypeError" in proc.stderr and "pygym:" not in proc.stderr
    control = {k: v for k, v in child.items() if k != "PYGYM_NO_TIPS"}
    proc = _run(["-E", "-B", str(script)], control, tmp_path)
    assert "pygym: this line uses zip()" in proc.stderr     # the hook is really active


def test_a_real_sandbox_run_is_unaffected(tmp_path, monkeypatch):
    from pygym import grader
    res = grader.run_in_sandbox("def f(a, b):\n    return list(zip(a, b))\n", "f", "zip",
                                [[[1, 2], [3, 4]], [[1], 5]], 10.0, level="audit-hook")
    assert res["status"] == "ok"
    assert res["outputs"][0] == {"value": [[1, 3], [2, 4]]}
    assert "TypeError" in res["outputs"][1]["error"] and "pygym:" not in json.dumps(res)
