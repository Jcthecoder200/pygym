"""Session.handle(): the command rules every front-end shares."""
import builtins

import pytest

from pygym.session import COMMANDS, Session

WRONG = "def combine(keys, values):\n    return {}"          # no zip -> fails
RIGHT = "def combine(keys, values):\n    return dict(zip(keys, values))"


@pytest.fixture(autouse=True)
def _private_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))  # never touch the real ~/.pygym


@pytest.fixture
def s():
    sess = Session("zip", 1)
    assert sess.exercise.id == "zip-01"  # the only level-1 zip exercise
    return sess


def test_q_with_no_code_quits(s):
    r = s.handle(":q", "")
    assert r.quit and r.kind == "quit"


def test_q_refuses_unchecked_changes(s):
    r = s.handle(":q", WRONG)
    assert not r.quit and r.kind == "refused" and not r.ask_reveal


def test_q_refused_after_failed_w_and_offers_solution(s):
    assert s.handle(":w", WRONG).kind == "fail"
    r = s.handle(":q", WRONG)
    assert not r.quit and r.kind == "refused" and r.ask_reveal


def test_q_after_pass_quits(s):
    s.handle(":w", RIGHT)
    assert s.handle(":q", RIGHT).quit


def test_q_after_pass_then_new_changes_is_refused(s):
    s.handle(":w", RIGHT)
    r = s.handle(":q", RIGHT + "\n# more")
    assert not r.quit and not r.ask_reveal


def test_changed_code_wins_over_failed_check(s):
    s.handle(":w", WRONG)
    r = s.handle(":q", RIGHT)  # new, unchecked code: don't offer the solution yet
    assert not r.quit and not r.ask_reveal and "Unchecked changes" in r.text


def test_skip_resets_failed_state(s):
    s.handle(":w", WRONG)
    s.handle(":s")
    assert s.last_passed is None and s.handle(":q", "").quit


def test_q_ignores_trailing_whitespace(s):
    s.handle(":w", RIGHT)
    assert s.handle(":q", RIGHT + "\n   \n").quit


def test_code_none_means_saved_code(s):
    s.handle(":w", WRONG)
    r = s.handle(":q")  # no new code -> judged on the saved (failed) code, not "changed"
    assert not r.quit and r.ask_reveal
    assert s.handle(":w").kind == "fail"  # re-checks the saved code
    s.handle(":w", RIGHT)
    assert s.handle(":q").quit


def test_q_bang_always_quits(s):
    assert s.handle(":q!", WRONG).quit


def test_w_pass_stays_open(s):
    r = s.handle(":w", RIGHT)
    assert r.kind == "pass" and r.result.passed and not r.quit


def test_wq_pass_quits(s):
    r = s.handle(":wq", RIGHT)
    assert r.kind == "pass" and r.quit


def test_wq_fail_asks_to_reveal_and_stays(s):
    r = s.handle(":wq", WRONG)
    assert r.kind == "fail" and r.ask_reveal and not r.quit


def test_w_fail_does_not_ask_to_reveal(s):
    assert not s.handle(":w", WRONG).ask_reveal


def test_hint_help_solution(s):
    assert s.handle(":h").text == s.exercise.hint
    assert s.handle(":help").text == builtins.zip.__doc__.strip()
    assert s.handle(":solution").text == s.exercise.reference_solution


def test_skip_resets_code_state(s):
    s.handle(":w", WRONG)
    r = s.handle(":s", WRONG)
    assert r.kind == "exercise" and r.clear_code and s.saved_code is None


def test_unknown_command(s):
    r = s.handle(":x", RIGHT)
    assert r.kind == "unknown" and not r.quit and ":wq" in r.text


def test_every_listed_command_is_handled(s):
    for name, _ in COMMANDS:
        if name in (":w", ":wq"):
            continue  # need code; covered above
        assert s.handle(name, "").kind != "unknown", name


def test_help_for_non_builtin(s):
    s.exercise.target_function = "not_a_builtin"
    assert "No documentation" in s.help()


def test_intro_lists_all_commands(s):
    r = s.handle(":intro", WRONG)
    assert r.kind == "commands" and not r.quit
    for name, _ in COMMANDS:
        assert name in r.text
    assert s.saved_code is None  # :intro never touches the code state


# ----- :out (the results of the last check) -----------------------------------------------------

from pygym.session import NOTHING_CHECKED, OLD_RESULTS, SHORT, commands_text  # noqa: E402


def test_out_before_any_check(s):
    r = s.handle(":out", WRONG)
    assert r.kind == "results" and r.result is None and r.text == NOTHING_CHECKED
    assert not r.quit and s.saved_code is None  # :out never touches the code state


def test_check_responses_carry_the_rows(s):
    r = s.handle(":w", RIGHT)
    assert len(r.result.tests) == 3 and all(t.passed for t in r.result.tests)
    r = s.handle(":wq", "def combine(keys, values):\n    return dict(zip(keys, keys))")
    assert r.kind == "fail" and [t.passed for t in r.result.tests] == [False, False, True]


def test_out_after_a_check_shows_the_last_result(s):
    checked = s.handle(":w", "def combine(keys, values):\n    return dict(zip(keys, keys))")
    r = s.handle(":out", "def combine(keys, values):\n    return dict(zip(keys, keys))")
    assert r.kind == "results" and r.result is checked.result
    assert r.text == "Last check: Not quite right yet: 1 of 3 tests passed."
    assert s.handle(":out").result is checked.result  # code=None: the saved code
    assert s.handle(":out", RIGHT).text == OLD_RESULTS  # the code changed since
    newer = s.handle(":w", RIGHT)
    assert s.handle(":out", RIGHT).result is newer.result


def test_out_is_forgotten_after_skip(s):
    s.handle(":w", RIGHT)
    s.handle(":s")
    assert s.handle(":out").text == NOTHING_CHECKED


def test_out_is_listed_everywhere(s):
    assert ":out" in dict(COMMANDS) and ":out" in SHORT and ":out" in commands_text()
    assert ":out" in s.handle(":intro").text
    assert ":out" in s.handle(":nope").text
    assert set(SHORT) == {name for name, _ in COMMANDS}
