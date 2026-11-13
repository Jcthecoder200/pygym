"""in_notebook() without IPython installed: a fake IPython module stands in for it.

Colab's shell is a subclass of ZMQInteractiveShell with another name (google.colab._shell.Shell),
so the check must follow the class hierarchy.
"""
import sys
import types

from pygym import jupyter


class InteractiveShell:                      # the IPython terminal / a plain shell
    pass


class ZMQInteractiveShell(InteractiveShell):  # Jupyter, JupyterLab, VS Code, Kaggle
    pass


class Shell(ZMQInteractiveShell):            # Colab (module name google.colab._shell)
    pass


def _fake_ipython(monkeypatch, shell):
    module = types.ModuleType("IPython")
    module.get_ipython = lambda: shell
    monkeypatch.setitem(sys.modules, "IPython", module)


def test_plain_python_has_no_ipython(monkeypatch):
    _fake_ipython(monkeypatch, None)
    assert jupyter.in_notebook() is False


def test_terminal_ipython_is_not_a_notebook(monkeypatch):
    _fake_ipython(monkeypatch, InteractiveShell())
    assert jupyter.in_notebook() is False


def test_jupyter_kernel_is_a_notebook(monkeypatch):
    _fake_ipython(monkeypatch, ZMQInteractiveShell())
    assert jupyter.in_notebook() is True


def test_colab_shell_is_a_notebook(monkeypatch):
    _fake_ipython(monkeypatch, Shell())
    assert Shell.__name__ != "ZMQInteractiveShell"      # the old name check failed here
    assert jupyter.in_notebook() is True


def test_colab_by_module_name_alone(monkeypatch):
    class ColabShell(InteractiveShell):                 # no ZMQ in its parents at all
        pass
    ColabShell.__module__ = "google.colab._shell"
    _fake_ipython(monkeypatch, ColabShell())
    assert jupyter.in_notebook() is True


# ----- the progress line in a notebook (_wait): display handle, in-place update, clearing ---------

class FakeHandle:
    def __init__(self, log):
        self.log = log

    def update(self, data, raw=False):
        self.log.append(("update", data["text/plain"], raw))


class FakeJob:
    """What jupyter._wait reads from a session.Job: wait(), progress, elapsed, cancel()."""

    def __init__(self, ticks, interrupt_at=None):
        self.ticks, self.interrupt_at, self.n = ticks, interrupt_at, 0
        self.progress = {"attempt": 1, "max_attempts": 4, "elapsed": 0.5, "budget": 20.0,
                         "stage": "asking", "detail": ""}
        self.elapsed = 0.5
        self.cancelled = False

    def wait(self, refresh=0.25):
        self.n += 1
        if self.interrupt_at == self.n:
            raise KeyboardInterrupt       # the kernel's ■ button
        self.elapsed += 0.25
        return self.n >= self.ticks

    def cancel(self):
        self.cancelled = True


def _notebook(monkeypatch):
    """A fake notebook kernel: IPython.display.display() records what it is asked to show."""
    log = []
    ipython = types.ModuleType("IPython")
    ipython.get_ipython = lambda: ZMQInteractiveShell()
    display_mod = types.ModuleType("IPython.display")

    def display(data, raw=False, display_id=None):
        log.append(("display", data["text/plain"], raw, display_id))
        return FakeHandle(log)

    display_mod.display = display
    ipython.display = display_mod
    monkeypatch.setitem(sys.modules, "IPython", ipython)
    monkeypatch.setitem(sys.modules, "IPython.display", display_mod)
    return log


def test_notebook_progress_is_one_line_updated_in_place_then_cleared(monkeypatch):
    log = _notebook(monkeypatch)
    jupyter._wait(FakeJob(ticks=3), refresh=0)
    shown = [e for e in log if e[0] == "display"]
    updates = [e for e in log if e[0] == "update"]
    assert len(shown) == 1 and shown[0][2] is True and shown[0][3] is True   # raw, display_id
    assert "■ interrupts" in shown[0][1] and len(shown[0][1]) <= jupyter.PROGRESS_WIDTH
    assert "\r" not in shown[0][1] and "\x1b" not in shown[0][1]            # plain text, no ANSI
    assert len(updates) == 3                      # two more refreshes, then the clearing update
    assert updates[-1][1] == ""                   # the line is gone at the end


def test_notebook_interrupt_cancels_the_job_and_clears_the_line(monkeypatch):
    log = _notebook(monkeypatch)
    job = FakeJob(ticks=99, interrupt_at=3)
    jupyter._wait(job, refresh=0)                 # must not raise KeyboardInterrupt
    assert job.cancelled is True
    assert log[-1][0] == "update" and log[-1][1] == ""


def test_colab_gets_the_same_in_place_line(monkeypatch):
    log = _notebook(monkeypatch)
    sys.modules["IPython"].get_ipython = lambda: Shell()       # Colab's shell class
    jupyter._wait(FakeJob(ticks=2), refresh=0)
    assert any(e[0] == "display" for e in log)    # not the plain "… text" lines of a terminal


def test_terminal_ipython_prints_plain_lines_instead(monkeypatch, capsys):
    log = _notebook(monkeypatch)
    sys.modules["IPython"].get_ipython = lambda: InteractiveShell()
    jupyter._wait(FakeJob(ticks=2), refresh=0)
    assert not log                                # no display handle outside a notebook
    assert "… " in capsys.readouterr().out
