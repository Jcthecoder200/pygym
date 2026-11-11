"""The starter code shows the import a target needs, above the def line."""
import pytest

from pygym import editor, loader
from pygym.models import Exercise
from pygym.session import Session


def _ex(target):
    return Exercise("x-01", target, 1, "q", "f", "def f(a):", "def f(a):\n    return a", [[1]])


@pytest.mark.parametrize("target,imports", [
    ("zip", []),
    ("str.join", []),
    ("list.sort", []),
    ("statistics.median", ["import statistics"]),
    ("itertools.chain", ["import itertools"]),
    ("os.path.join", ["import os"]),
])
def test_import_lines(target, imports):
    assert _ex(target).import_lines == imports


def test_starter_code_puts_the_import_above_the_def():
    assert _ex("statistics.median").starter_code == "import statistics\n\ndef f(a):"
    assert _ex("zip").starter_code == "def f(a):"


class _FakeSession:
    def __init__(self, ex):
        self.exercise, self.notice, self.saved_code = ex, "", None


def test_editor_prefills_the_import_and_puts_the_cursor_in_the_body():
    ed = editor.Editor(_FakeSession(_ex("statistics.median")))
    ed.load_exercise()
    assert ed.buffer.lines[:3] == ["import statistics", "", "def f(a):"]
    assert ed.buffer.row == 3 and ed.buffer.col == len(editor.INDENT)
    assert ed.session.saved_code == ed.buffer.text  # the starter counts as saved


def test_editor_builtin_starter_unchanged():
    ed = editor.Editor(_FakeSession(_ex("zip")))
    ed.load_exercise()
    assert ed.buffer.lines[0] == "def f(a):" and ed.buffer.row == 1


def test_unavailable_error_says_why_the_ai_failed(monkeypatch, tmp_path):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    from pygym import ai

    def fail(*a, **k):
        raise ai.GenerationUnavailable("the model did not answer")
    monkeypatch.setattr(ai, "generate_exercise", fail)
    with pytest.raises(loader.ExerciseUnavailable) as err:
        Session("statistics.median", 1, mode="preset")
    assert "the model did not answer" in str(err.value)
