"""The editor's results window (like Neovim's quickfix window): the tests of the last :w."""
import re
import time

import pytest

from pygym.editor import (INSERT, NO_RESULTS_WINDOW, NORMAL, PAGER, PROMPT, RESULTS_HELP,
                          RESULTS_MAX_SHARE, Editor, render)
from pygym.models import Exercise
from pygym.session import NOTHING_CHECKED, OLD_RESULTS, Session

WRONG = "return dict(zip(keys, keys))"      # 1 of 3 tests pass
RIGHT = "return dict(zip(keys, values))"


@pytest.fixture(autouse=True)
def _private_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))


@pytest.fixture
def ed():
    s = Session("zip", 1)
    assert s.exercise.id == "zip-01"
    return Editor(s, 80, 24)


def many_tests_editor(n=12, width=80, height=24):
    """An exercise with n tests whose code prints: more rows than the window can show."""
    ex = Exercise(id="len-many", target_function="len", difficulty=1,
                  question_text="Return the length of x.", function_name="f",
                  function_signature="def f(x):", reference_solution="def f(x):\n    return len(x)",
                  test_inputs=[["a" * i] for i in range(n)], expected_outputs=list(range(n)))
    s = Session("zip", 1)
    s.exercise = ex
    return Editor(s, width, height)


def feed(ed, *keys):
    for k in keys:
        if len(k) > 1 and not k.isupper():
            for ch in k:
                ed.feed(ch)
        else:
            ed.feed(k)


def command(ed, cmd):
    feed(ed, ":", cmd[1:], "ENTER")


def screen(ed, width=None, height=None):
    """The frame as plain rows (the final cursor positioning removed first) and the cursor."""
    width, height = width or ed.width, height or ed.height
    frame = render(ed, width, height, color_system=None)
    cursor = tuple(map(int, re.search(r"\x1b\[(\d+);(\d+)H\x1b\[\?25h$", frame).groups()))
    frame = re.sub(r"\x1b\[\d+;\d+H\x1b\[\?25h$", "", frame)
    parts = re.split(r"\x1b\[(\d+);1H", frame)
    rows = {int(n): re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", t) for n, t in zip(parts[1::2],
                                                                           parts[2::2])}
    return [rows[k] for k in sorted(rows)], cursor


def window_rows(rows):
    """The results window's rows: from its title bar to the status line."""
    start = next(i for i, r in enumerate(rows) if r.startswith(" results "))
    return rows[start:-2]


# ----- opening, content, refreshing ----------------------------------------------------------------

def test_window_opens_after_a_failing_check(ed):
    feed(ed, "i", "print('pairs', keys)", "ENTER", WRONG, "ESC")
    command(ed, ":w")
    assert ed.results_open and ed.mode == NORMAL and not ed.results_focus
    rows, _ = screen(ed)
    win = window_rows(rows)
    assert win[0].startswith(" results  1/3 passed ") and win[0].rstrip().endswith(":out close")
    # too long for one row: expected / got go on a row of their own under the call
    assert win[1:4] == ["✗ 1  combine(['a', 'b', 'c'], [1, 2, 3])",
                        "     expected {'a': 1, 'b': 2, 'c': 3}  got {'a': 'a', 'b': 'b', 'c': 'c'}",
                        "    │ pairs ['a', 'b', 'c']"]
    assert "✓ 3  combine([], [])  →  {}" in win
    assert rows[-1] == "✗ Failed: Not quite right yet: 1 of 3 tests passed."
    assert "NORMAL" in rows[-2]


def test_window_opens_after_a_passing_check_too(ed):
    feed(ed, "i", RIGHT, "ESC")
    command(ed, ":w")
    win = window_rows(screen(ed)[0])
    assert win[0].startswith(" results  3/3 passed ")
    assert [r[:4] for r in win[1:4]] == ["✓ 1 ", "✓ 2 ", "✓ 3 "]


def test_window_refreshes_on_the_next_check(ed):
    feed(ed, "i", WRONG, "ESC")
    command(ed, ":w")
    first = ed.results
    feed(ed, "0")  # unknown NORMAL key: ignored; the window stays while the student edits
    feed(ed, "dd", "o", "    " + RIGHT, "ESC")
    assert ed.results_open and ed.results is first
    command(ed, ":w")
    assert ed.results is not first and window_rows(screen(ed)[0])[0].startswith(
        " results  3/3 passed ")


def test_syntax_error_shows_its_message_in_the_window(ed):
    feed(ed, "i", "return (", "ESC")
    command(ed, ":w")
    win = window_rows(screen(ed)[0])
    assert win[0].startswith(" results  no tests ran ")
    assert win[1].startswith("✗ Syntax error on line 2")


def test_setup_prints_come_first(ed):
    feed(ed, "i", RIGHT, "ESC")
    ed.buffer.lines.insert(0, "print('loading')")
    command(ed, ":w")
    win = window_rows(screen(ed)[0])
    assert win[1:3] == ["printed while loading:", "  │ loading"]


def test_long_prints_are_cut_with_a_count(ed):
    feed(ed, "i", "for i in range(10): print('line', i)", "ENTER", RIGHT, "ESC")
    command(ed, ":w")
    lines = [r.plain for r in ed.results_lines(80)]
    assert lines[1:5] == ["    │ line 0", "    │ line 1", "    │ line 2", "    │ … 7 more lines"]


# ----- :out, closing, :s ---------------------------------------------------------------------------

def test_out_toggles_the_window(ed):
    command(ed, ":out")
    assert not ed.results_open and ed.message == NOTHING_CHECKED
    feed(ed, "i", WRONG, "ESC")
    command(ed, ":w")
    command(ed, ":out")
    assert not ed.results_open and ed.results is not None
    assert not any(r.startswith(" results ") for r in screen(ed)[0])
    command(ed, ":out")
    assert ed.results_open and ed.message.startswith("Last check: Not quite right yet")
    feed(ed, "o", "# changed", "ESC")
    command(ed, ":out")
    command(ed, ":out")  # closed, then opened again for code that changed since
    assert ed.results_open and ed.message == OLD_RESULTS
    assert ed.mode == NORMAL


def test_skip_closes_the_window(ed):
    feed(ed, "i", WRONG, "ESC")
    command(ed, ":w")
    command(ed, ":s")
    assert not ed.results_open and ed.results is None
    command(ed, ":out")
    assert ed.message == NOTHING_CHECKED


def test_failed_wq_keeps_the_window_under_the_prompt(ed):
    feed(ed, "i", WRONG, "ESC")
    command(ed, ":wq")
    rows, cursor = screen(ed)
    assert ed.mode == PROMPT and rows[-1].startswith("Reveal the solution?")
    assert any(r.startswith(" results  1/3 passed") for r in rows)
    assert cursor[0] == 24


def test_pager_then_window(ed):
    ed.resize(40, 16)  # the failure message does not fit: the pager comes first
    feed(ed, "i", WRONG, "ESC")
    command(ed, ":w")
    assert ed.mode == PAGER and ed.results_open
    feed(ed, "q")
    rows, _ = screen(ed)
    assert any(r.startswith(" results ") for r in rows)


# ----- size, layout, editing with the window open -----------------------------------------------

@pytest.mark.parametrize("width, height", [(80, 24), (40, 10), (120, 50), (20, 8), (20, 5),
                                           (60, 14)])
def test_frame_fits_every_size(width, height):
    ed = many_tests_editor(width=width, height=height)
    feed(ed, "i", "print(x)", "ENTER", "return len(x)", "ESC")
    command(ed, ":w")
    frame = render(ed, width, height)
    rows, cursor = screen(ed, width, height)
    assert len(rows) == height and all(len(r) <= width for r in rows) and "\n" not in frame
    window = ed.results_height(width, height)
    assert window <= max(2, int(height * RESULTS_MAX_SHARE))
    code_rows = ed.body_rows()
    assert code_rows >= 1 and 1 <= cursor[0] <= height


def test_80x24_layout(ed):
    feed(ed, "i", "print('pairs', keys)", "ENTER", WRONG, "ESC")
    command(ed, ":w")
    rows, cursor = screen(ed)
    assert len(rows) == 24 and all(len(r) <= 80 for r in rows)
    win = window_rows(rows)
    assert len(win) == 9  # title bar + 8 rows: 40% of 24 rows, and everything fits
    assert ed.body_rows() == 24 - 3 - 2 - 9 and not any("of 8" in r for r in rows)
    assert rows[cursor[0] - 1].startswith("  3 ")  # the cursor is still on the code


def test_editing_and_scrolling_the_code_with_the_window_open():
    ed = many_tests_editor()
    feed(ed, "i", "return len(x)", "ESC")
    command(ed, ":w")
    assert ed.results_open
    body = ed.body_rows()
    feed(ed, "o")
    for n in range(20):
        feed(ed, f"# line {n}", "ENTER")
    assert ed.mode == INSERT and ed.top <= ed.buffer.row < ed.top + body
    rows, cursor = screen(ed)
    title = next(i for i, r in enumerate(rows) if r.startswith(" results "))
    assert cursor[0] - 1 < title  # the cursor never goes into the window
    assert rows[cursor[0] - 1].lstrip().startswith(str(ed.buffer.row + 1) + " ")
    feed(ed, "ESC", "k", "HOME", "x")
    assert ed.buffer.lines[ed.buffer.row] == "   # line 19"


def test_typing_keeps_working_with_the_window_open(ed):
    feed(ed, "i", WRONG, "ESC")
    command(ed, ":w")
    feed(ed, "dd", "o", "    " + RIGHT, "ESC")
    assert ed.buffer.lines[-1] == "    " + RIGHT and ed.results_open
    command(ed, ":w")
    assert ed.message.startswith("Passed!") and ed.results.passed


# ----- focus and scrolling (Ctrl-W w, like Neovim) ------------------------------------------------

def test_ctrl_w_moves_the_focus_and_j_k_scroll():
    ed = many_tests_editor()
    feed(ed, "i", "print('x is', x)", "ENTER", "return len(x)", "ESC")
    command(ed, ":w")
    total, page = ed._results_page()
    assert total == 24 and page == 8  # 12 tests + their prints, in a 9-row window
    row_before = ed.buffer.row
    feed(ed, "CTRL_W", "w")
    assert ed.results_focus and ed.mode == NORMAL
    rows, cursor = screen(ed)
    title = next(i for i, r in enumerate(rows) if r.startswith(" results "))
    assert cursor == (title + 2, 1) and rows[-1] == RESULTS_HELP
    assert "1-8 of 24" in rows[title] and "q close · Esc back" in rows[title]
    feed(ed, "j", "j", "DOWN")
    assert ed.results_top == 3 and ed.buffer.row == row_before  # j scrolls, the code stays
    assert window_rows(screen(ed)[0])[1] == ed.results_lines(80)[3].plain
    feed(ed, "k")
    assert ed.results_top == 2
    feed(ed, " ")
    assert ed.results_top == 10
    feed(ed, "G")
    assert ed.results_top == total - page
    feed(ed, "j")
    assert ed.results_top == total - page  # clamped at the end
    feed(ed, "g")
    assert ed.results_top == 0
    feed(ed, "PAGEDOWN", "PAGEUP")
    assert ed.results_top == 0
    feed(ed, "ESC")
    assert not ed.results_focus and ed.results_open
    feed(ed, "j")
    assert ed.results_top == 0  # back in the code: j moves the cursor


def test_focus_keys_back_to_code_close_and_commands():
    ed = many_tests_editor()
    feed(ed, "i", "return len(x)", "ESC")
    command(ed, ":w")
    feed(ed, "CTRL_W", "CTRL_W")
    assert ed.results_focus
    feed(ed, "CTRL_W", "k")
    assert not ed.results_focus
    feed(ed, "CTRL_W", "j")
    assert ed.results_focus
    feed(ed, "q")  # closes the window, like :cclose
    assert not ed.results_open and not ed.results_focus
    command(ed, ":out")
    feed(ed, "CTRL_W", "w", ":")  # a command from the window
    assert ed.mode == "COMMAND" and not ed.results_focus
    feed(ed, "out", "ENTER")
    assert not ed.results_open
    command(ed, ":out")
    feed(ed, "CTRL_W", "w", "i")  # typing goes to the code
    assert ed.mode == INSERT and not ed.results_focus


def test_ctrl_w_without_a_window_says_so(ed):
    feed(ed, "CTRL_W", "w")
    assert not ed.results_focus and ed.message == NO_RESULTS_WINDOW
    feed(ed, "CTRL_W", "x")  # any other key cancels Ctrl-W
    assert ed.mode == NORMAL and ed.buffer.text.startswith("def combine")


def test_ctrl_c_in_the_window_only_explains(ed):
    feed(ed, "i", WRONG, "ESC")
    command(ed, ":w")
    feed(ed, "CTRL_W", "w", "CTRL_C")
    assert not ed.done and ed.results_focus


def test_scroll_position_is_kept_valid_on_resize():
    ed = many_tests_editor()
    feed(ed, "i", "print(x)", "ENTER", "return len(x)", "ESC")
    command(ed, ":w")
    feed(ed, "CTRL_W", "w", "G")
    ed.resize(80, 80)  # everything fits now
    assert ed.results_top == 0
    rows, _ = screen(ed)
    assert len(rows) == 80


# ----- colours and speed ------------------------------------------------------------------------

def test_window_colours(ed, monkeypatch):
    feed(ed, "i", WRONG, "ESC")
    command(ed, ":w")
    frame = render(ed, 80, 24)
    assert "\x1b[1;37;41m 1/3 passed " in frame  # bold white on red block
    assert "\x1b[1;31m✗ 1\x1b[0m" in frame and "\x1b[1;32m✓ 3\x1b[0m" in frame
    monkeypatch.setenv("NO_COLOR", "1")
    plain = render(ed, 80, 24)
    nums = [int(p) for ps in re.findall(r"\x1b\[([0-9;]*)m", plain) for p in ps.split(";") if p]
    assert not [n for n in nums if 30 <= n <= 49 or 90 <= n <= 107]
    assert "\x1b[7m results " in plain  # reverse video title bar


def test_render_stays_fast_with_the_window_open():
    ed = many_tests_editor(n=30, width=120, height=50)
    feed(ed, "i", "print('x is', x)", "ENTER", "return len(x)", "ESC")
    command(ed, ":w")
    render(ed, 120, 50)  # warm the caches, like the first frame
    start = time.perf_counter()
    for i in range(50):
        ed.feed("CTRL_W"), ed.feed("w"), ed.feed("j"), ed.feed("ESC")
        render(ed, 120, 50)
    per_frame = (time.perf_counter() - start) / 50
    assert per_frame < 0.05, per_frame  # a few ms in practice
