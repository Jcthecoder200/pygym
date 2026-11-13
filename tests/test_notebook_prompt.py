"""The notebook front-end tells the student WHERE to type: there is no input box in the output.

Seen on Colab: after exercise("zip") a student looked for a place to enter code. The answer goes in
a new cell that starts with %%pygym :w; that is said in the intro, and now also under every exercise.
No IPython needed: a stand-in shell is enough for start() and the skip command.
"""
import re

import pytest

from pygym import jupyter
from pygym.session import Session


class StandInShell:
    def register_magic_function(self, function, magic_kind=None, magic_name=None):
        self.registered = (function, magic_kind, magic_name)


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setattr(jupyter, "_active", None)


def words(out):
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", out).split())


def test_the_exercise_is_followed_by_where_to_write_the_answer(capsys):
    jupyter.start(Session("zip", 1), StandInShell())
    out = capsys.readouterr().out
    assert "def combine(keys, values):" in out
    assert " ".join(jupyter.ANSWER_HERE.split()) in words(out)
    # it comes AFTER the exercise panel, where the eye ends up
    assert out.rindex("def combine(keys, values):") < out.index("Your answer goes in a NEW cell")


def test_the_pointer_comes_back_after_skipping_to_another_exercise(capsys):
    jupyter.start(Session("zip", 1), StandInShell())
    capsys.readouterr()
    jupyter.pygym_magic(":s")
    out = capsys.readouterr().out
    assert "level 1" in out and "Your answer goes in a NEW cell" in out


def test_a_failed_check_does_not_repeat_the_pointer(capsys):
    jupyter.start(Session("zip", 1), StandInShell())
    capsys.readouterr()
    jupyter.pygym_magic(":w", "def combine(keys, values):\n    return {}")
    assert "Your answer goes in a NEW cell" not in capsys.readouterr().out
