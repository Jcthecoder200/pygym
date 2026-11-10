"""style.py: the Rich building blocks shared by the line CLI and Jupyter."""
import builtins
import io

import pytest
from rich.console import Console

from pygym import __version__, style
from pygym.models import Response
from pygym.session import COMMANDS, Session


@pytest.fixture(autouse=True)
def _private_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))  # never touch the real ~/.pygym


@pytest.fixture
def s():
    sess = Session("zip", 1)
    assert sess.exercise.id == "zip-01"
    return sess


def plain(resp, session):
    return style.to_plain(style.response(resp, session))


def coloured(renderable):
    """What a colour terminal would receive."""
    console = Console(file=io.StringIO(), force_terminal=True, color_system="truecolor", width=80)
    console.print(renderable)
    return console.file.getvalue()


@pytest.mark.parametrize("kind, text, expected", [
    ("pass", "Passed! Correct!", "✓ Passed! Correct!"),
    ("fail", "Failed: wrong output", "✗ Failed: wrong output"),
    ("refused", "Unchecked changes: :w to check them", "! Unchecked changes: :w to check them"),
    ("unknown", "Unknown command ':x'", "Unknown command ':x'"),
    ("exercise", "Skipped. Here is another one.", "Skipped. Here is another one."),
    ("quit", "Bye!", "Bye!"),
])
def test_one_line_kinds(s, kind, text, expected):
    assert plain(Response(text, kind), s) == expected


@pytest.mark.parametrize("kind, sgr", [
    ("pass", "\x1b[1;32m"), ("fail", "\x1b[1;31m"), ("refused", "\x1b[33m"),
    ("unknown", "\x1b[31m"), ("exercise", "\x1b[2m"), ("quit", "\x1b[2m"),
])
def test_one_line_kinds_are_coloured(s, kind, sgr):
    assert coloured(style.response(Response("x", kind), s)).startswith(sgr)


def test_hint_is_a_yellow_panel(s):
    out = plain(Response(s.hint(), "hint"), s).split("\n")
    assert out[0].startswith("╭") and "─ hint ─" in out[0] and out[-1].startswith("╰")
    assert out[1].startswith("│ " + s.hint())
    assert "\x1b[33m╭" in coloured(style.response(Response(s.hint(), "hint"), s))


def test_help_is_a_panel_titled_with_the_builtin(s):
    out = plain(Response(s.help(), "help"), s)
    first_line = builtins.zip.__doc__.strip().split("\n")[0]
    assert "─ Documentation: zip ─" in out.split("\n")[0]
    assert "│ " + first_line in out


def test_solution_is_plain_code_after_a_label(s):
    out = plain(Response(s.solution(), "solution"), s)
    assert out == "Solution:\n" + s.solution()  # no panel: the code can be copied as it is
    assert "\x1b[38;2;" in coloured(style.solution(s.solution()))  # syntax colours


def test_intro_lists_commands_and_version():
    out = style.to_plain(style.intro())
    assert "─ pygym ─" in out and f"v{__version__}" in out
    assert "Learn a Python built-in without leaving your code." in out
    assert "A line that is exactly a command runs it;" in out
    for name, desc in COMMANDS:
        assert f"{name}  " in out and desc in out


def test_exercise_header(s):
    out = style.to_plain(style.exercise_header(s.exercise))
    lines = out.split("\n")
    assert "─ zip-01 ─" in lines[0]
    assert "zip  ●○○  level 1" in out
    assert "Your function should look like:" in out and s.exercise.function_signature in out
    assert "\x1b[1;35mzip" in coloured(style.exercise_header(s.exercise))


def test_dots():
    assert [style.dots(n) for n in (0, 1, 2, 3, 9)] == ["○○○", "●○○", "●●○", "●●●", "●●●"]


def test_to_plain_has_no_codes_and_keeps_to_the_width(s):
    out = style.to_plain(style.exercise_header(s.exercise), width=40)
    assert "\x1b" not in out and all(len(line) <= 40 for line in out.split("\n"))


def test_highlight_lines_keeps_the_text():
    source = "def f():\n    s = '''\nx\n'''\n\n    "
    lines = style.highlight_lines(source)
    assert [t.plain for t in lines] == source.split("\n")
    assert lines[0].spans  # "def" and "f" have colours
