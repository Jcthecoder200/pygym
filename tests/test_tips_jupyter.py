"""Tips in a real Jupyter kernel (nbclient + ipykernel of this Python).

The error output of the cell is followed by the tip (text/plain + text/html), the error output
itself is the same as without pygym, the startup file of `pygym --tips on` works without any
import, and exercise('zip') in the next cell opens the notebook front-end.

Starting kernels takes a few seconds: skip with  pytest -m "not kernel".
Everything is in temporary folders: the kernel spec (JUPYTER_PATH), connection files
(JUPYTER_RUNTIME_DIR), IPYTHONDIR, PYGYM_HOME and the .pth folder (PYGYM_TIPS_SITE_DIR).
"""
import json
import re
import sys

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")
pytest.importorskip("ipykernel")

from pygym import tips  # noqa: E402

pytestmark = pytest.mark.kernel
KERNEL = "pygym-tips-test"
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def kernel_env(tmp_path, monkeypatch):
    spec = tmp_path / "jupyter" / "kernels" / KERNEL
    spec.mkdir(parents=True)
    (spec / "kernel.json").write_text(json.dumps({
        "argv": [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"],
        "display_name": "pygym tips test", "language": "python"}))
    monkeypatch.setenv("JUPYTER_PATH", str(tmp_path / "jupyter"))
    monkeypatch.setenv("JUPYTER_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("JUPYTER_DATA_DIR", str(tmp_path / "jupyter-data"))
    monkeypatch.setenv("JUPYTER_CONFIG_DIR", str(tmp_path / "jupyter-config"))
    monkeypatch.setenv("IPYTHONDIR", str(tmp_path / "ipython"))
    monkeypatch.setenv("PYGYM_TIPS_SITE_DIR", str(tmp_path / "site"))
    monkeypatch.delenv("PYGYM_NO_TIPS", raising=False)
    return tmp_path


def run_notebook(cells, folder):
    nb = nbformat.v4.new_notebook()
    nb.cells = [nbformat.v4.new_code_cell(c) for c in cells]
    client = nbclient.NotebookClient(nb, kernel_name=KERNEL, timeout=120, allow_errors=True,
                                     resources={"metadata": {"path": str(folder)}})
    client.execute()
    return [cell.outputs for cell in nb.cells]


def _error_and_tip(outputs):
    kinds = [o["output_type"] for o in outputs]
    assert kinds == ["error", "display_data"], kinds
    return outputs[0], outputs[1]["data"]


def test_tip_follows_the_error_output_in_a_notebook(kernel_env):
    outs = run_notebook(["from pygym import exercise", "list(zip([1, 2], 3))", "exercise('zip')"],
                        kernel_env)
    assert outs[0] == []
    error, tip = _error_and_tip(outs[1])
    assert error["ename"] == "TypeError"
    assert tip["text/plain"] == ("pygym: this line uses zip(); 30-second exercise? exercise('zip')  "
                                 "(turn off: pygym --tips off)")
    assert "<code" in tip["text/html"] and "exercise(&#x27;zip&#x27;)" in tip["text/html"]
    assert "background" not in tip["text/html"]
    # the next cell starts the exercise in the notebook front-end, without an error
    assert all(o["output_type"] != "error" for o in outs[2])
    shown = json.dumps([o.get("data", o.get("text", "")) for o in outs[2]])
    assert "%%pygym :w" in shown


def test_the_error_output_is_unchanged_by_pygym(kernel_env):
    with_tips = run_notebook(["import pygym", "list(zip([1, 2], 3))"], kernel_env)
    without = run_notebook(["import json", "list(zip([1, 2], 3))"], kernel_env)
    error, tip = _error_and_tip(with_tips[1])
    assert [o["output_type"] for o in without[1]] == ["error"]
    plain = without[1][0]
    assert (error["ename"], error["evalue"]) == (plain["ename"], plain["evalue"])
    assert [_ANSI.sub("", t) for t in error["traceback"]] == \
        [_ANSI.sub("", t) for t in plain["traceback"]]
    assert "pygym.exercise('zip')" in tip["text/plain"]


def test_startup_file_shows_tips_without_import(kernel_env):
    done = tips.enable_global()
    assert done["startup"] and done["startup"].startswith(str(kernel_env / "ipython"))
    outs = run_notebook(["list(zip([1, 2], 3))", "'pygym' in dir()"], kernel_env)
    _error, tip = _error_and_tip(outs[0])
    assert tip["text/plain"].startswith(
        "pygym: this line uses zip(); 30-second exercise? from pygym import exercise; exercise('zip')")
    assert outs[1][0]["data"]["text/plain"] == "False"       # nothing added to the namespace
