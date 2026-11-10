"""The text front-end: per-line command isolation, intro, reveal prompt, clean exit."""
import re

import pytest

from pygym.session import Session
from pygym.ui import run_text


@pytest.fixture(autouse=True)
def _private_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))


def drive(lines):
    """Run the CLI with scripted input; return everything it printed and the prompts it showed."""
    it = iter(lines)
    out, prompts = [], []

    def fake_input(prompt):
        prompts.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    run_text(Session("zip", 1), input_fn=fake_input, print_fn=lambda *a: out.append(" ".join(map(str, a))))
    return "\n".join(out), prompts


def test_intro_lists_every_command():
    out, _ = drive([":q"])
    for cmd in (":w", ":wq", ":q", ":q!", ":h", ":help", ":s", ":solution"):
        assert cmd in out


def test_command_after_code_lines_is_isolated():
    out, _ = drive(["def combine(keys, values):", ":h", "    return dict(zip(keys, values))", ":wq"])
    assert "─ hint ─" in out and "Passed!" in out  # the :h line did not end up inside the code


def test_wq_fail_reveal_yes_then_fix():
    out, prompts = drive(["def combine(keys, values):", "    return {}", ":wq", "y",
                          "def combine(keys, values):", "    return dict(zip(keys, values))", ":wq"])
    assert "Failed:" in out and "Reveal the solution? [y/N] " in prompts
    assert "Solution:\ndef combine" in out and "Passed!" in out


def test_wq_fail_reveal_default_no():
    out, _ = drive(["def combine(keys, values):", "    return {}", ":wq", "", ":q"])
    assert "Solution:" not in out and "Bye!" in out


def test_q_refused_then_q_bang():
    out, _ = drive(["def combine(keys, values):", ":q", ":q!"])
    assert "Unchecked changes" in out and "code not checked" in out


def test_w_again_rechecks_saved_code():
    out, _ = drive(["def combine(keys, values):", "    return {}", ":w", ":w", ":q!"])
    assert out.count("Failed:") == 2


def test_q_after_failed_check_refuses_and_offers_solution():
    out, prompts = drive(["def combine(keys, values):", "    return {}", ":w", ":q", "y", ":q!"])
    assert "didn't pass" in out and "Reveal the solution? [y/N] " in prompts
    assert "Solution:\ndef combine" in out and "code not checked" in out


def test_unknown_colon_word_is_not_code():
    out, _ = drive([":wqq", "def combine(keys, values):", "    return dict(zip(keys, values))", ":wq"])
    assert "Unknown command ':wqq'" in out and "Passed!" in out


def test_colon_continuation_line_is_code():
    # A dict continuation line starting with ":" (legal Python) must stay code.
    code = ["def combine(keys, values):", "    d = {'a'", "         : 1}",
            "    return dict(zip(keys, values))", ":wq"]
    out, _ = drive(code)
    assert "Passed!" in out


def test_eof_exits_cleanly():
    out, _ = drive(["def combine(keys, values):"])  # then input ends (Ctrl-D)
    assert out.endswith("Bye!")


def test_skip_shows_new_header():
    out, _ = drive([":s", ":q"])
    assert len(re.findall(r"zip  [●○]{3}  level \d", out)) == 2  # one exercise panel each


def test_intro_command_reprints_command_list():
    out, _ = drive(["def combine(keys, values):", ":intro", "    return dict(zip(keys, values))", ":wq"])
    assert out.count(":solution") >= 2 and "Passed!" in out  # intro at start + :intro; code kept
