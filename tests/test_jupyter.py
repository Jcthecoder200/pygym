"""The %%pygym cell magic, run in a real (non-notebook) IPython shell."""
import pytest

import re

IPython = pytest.importorskip("IPython")

from pygym import jupyter  # noqa: E402
from pygym.session import Session  # noqa: E402

RIGHT = "def combine(keys, values):\n    return dict(zip(keys, values))"
WRONG = "def combine(keys, values):\n    return {}"


@pytest.fixture(scope="module")
def ip(tmp_path_factory):
    from IPython.core.interactiveshell import InteractiveShell
    from traitlets.config import Config

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("IPYTHONDIR", str(tmp_path_factory.mktemp("ipython")))  # keep ~/.ipython clean
        config = Config()
        config.HistoryManager.enabled = False
        yield InteractiveShell.instance(config=config)


@pytest.fixture(autouse=True)
def _private_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))  # never touch the real ~/.pygym
    monkeypatch.setenv("COLUMNS", "100")  # Rich's width when stdout is not a terminal
    monkeypatch.setattr(jupyter, "_active", None)


def flat(out):
    """The printed words without panel borders or line breaks (Rich wraps text inside panels)."""
    return " ".join(re.sub(r"[│╭╮╰╯─]", " ", out).split())


@pytest.fixture
def started(ip, capsys):
    s = Session("zip", 1)
    jupyter.start(s, ip)
    out = capsys.readouterr().out
    assert "zip  ●○○  level 1" in out and "def combine(keys, values):" in out
    assert " ".join(jupyter.INSTRUCTIONS.split()) in flat(out) and "%%pygym :w" in out
    return s


def cell(ip, text):
    result = ip.run_cell(text)
    assert result.success, result.error_in_exec
    return result


def test_not_a_notebook(ip):
    assert jupyter.in_notebook() is False  # a plain InteractiveShell is not ZMQInteractiveShell


def test_pass(ip, started, capsys):
    cell(ip, "%%pygym :w\n" + RIGHT)
    assert "Passed!" in capsys.readouterr().out


def test_fail(ip, started, capsys):
    cell(ip, "%%pygym :w\n" + WRONG)
    assert "Failed:" in capsys.readouterr().out


def test_empty_command_means_w(ip, started, capsys):
    cell(ip, "%%pygym\n" + RIGHT)
    assert "Passed!" in capsys.readouterr().out


def test_hint_line_magic(ip, started, capsys):
    cell(ip, "%pygym :h")
    out = capsys.readouterr().out
    assert "─ hint ─" in out and " ".join(started.exercise.hint.split()) in flat(out)


def test_help_and_solution(ip, started, capsys):
    cell(ip, "%pygym :help")
    cell(ip, "%pygym :solution")
    out = capsys.readouterr().out
    assert "zip(" in out and "Solution:\n" + started.exercise.reference_solution in out


def test_wq_fail_reveal_yes(ip, started, capsys, monkeypatch):
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "y")
    cell(ip, "%%pygym :wq\n" + WRONG)
    out = capsys.readouterr().out
    assert asked == ["Reveal the solution? [y/N] "]
    assert "Failed:" in out and "Solution:\n" + started.exercise.reference_solution in out
    assert jupyter._active is started  # a failed :wq does not end the session


def test_reveal_eof_means_no(ip, started, capsys, monkeypatch):
    def no_stdin(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_stdin)
    cell(ip, "%%pygym :wq\n" + WRONG)
    assert "Solution:" not in capsys.readouterr().out


def test_empty_q_after_pass_ends_session(ip, started, capsys):
    cell(ip, "%%pygym :w\n" + RIGHT)
    cell(ip, "%%pygym :q\n\n   \n")  # no code: Session judges the last saved (passed) code
    out = capsys.readouterr().out
    assert "Bye!" in out and "Session ended." in out
    assert jupyter._active is None
    cell(ip, "%pygym :h")
    assert jupyter.NO_SESSION in capsys.readouterr().out


def test_q_refused_with_unchecked_code(ip, started, capsys):
    cell(ip, "%%pygym :q\n" + RIGHT)
    assert "Unchecked changes" in capsys.readouterr().out and jupyter._active is started


def test_skip_prints_new_header(ip, started, capsys):
    cell(ip, "%pygym :s")
    out = capsys.readouterr().out
    assert "Skipped." in out and re.search(r"zip  [●○]{3}  level \d", out)


def test_no_active_session(ip, capsys):
    ip.register_magic_function(jupyter.pygym_magic, magic_kind="line_cell", magic_name="pygym")
    cell(ip, "%%pygym :w\n" + RIGHT)
    assert 'No active exercise. Run exercise("zip") first.' in capsys.readouterr().out


def test_errors_do_not_reach_the_notebook(ip, started, capsys, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(started, "handle", broken)
    cell(ip, "%%pygym :w\n" + RIGHT)
    assert "boom" in capsys.readouterr().out


def test_intro_line_magic(ip, started, capsys):
    cell(ip, "%pygym :intro")
    out = capsys.readouterr().out
    assert ":solution" in out and ":intro" in out and "commands" in out
