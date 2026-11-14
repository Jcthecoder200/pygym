"""The terminal editor without a terminal: Editor.feed(), parse_keys() and render()."""
import builtins
import re

import pytest

from pygym.editor import (COMMAND, CTRL_C_MESSAGE, INSERT, NORMAL, PAGER, PROMPT, START_HINT,
                          Buffer, Editor, _header_rows, parse_keys, render)
from pygym.session import Session

STARTER = "def combine(keys, values):\n    "
BODY = "return dict(zip(keys, values))"


@pytest.fixture(autouse=True)
def _private_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))  # never touch the real ~/.pygym


@pytest.fixture
def ed():
    s = Session("zip", 1)
    assert s.exercise.id == "zip-01"
    return Editor(s, 80, 24)


def feed(ed, *keys):
    """Feed key names; a plain string longer than one character is typed letter by letter."""
    for k in keys:
        if len(k) > 1 and not k.isupper():
            for ch in k:
                ed.feed(ch)
        else:
            ed.feed(k)


def command(ed, cmd):
    feed(ed, ":", cmd[1:], "ENTER")


def cursor(ed):
    return ed.buffer.row, ed.buffer.col


# ----- start state and commands --------------------------------------------------------------

def test_starts_in_normal_with_starter_text(ed):
    assert ed.mode == NORMAL and ed.buffer.text == STARTER
    assert cursor(ed) == (1, 4)
    assert ed.session.saved_code == STARTER
    assert ed.message == START_HINT


def test_q_right_away_quits(ed):
    command(ed, ":q")
    assert ed.done and ed.farewell == "Bye!"


def test_i_enters_insert_and_colon_is_text(ed):
    feed(ed, "i")
    assert ed.mode == INSERT and ed.message == ""  # the start hint is gone
    feed(ed, ":", "w")
    assert ed.mode == INSERT and ed.buffer.lines[1] == "    :w"
    feed(ed, "ESC")
    assert ed.mode == NORMAL


def test_w_with_wrong_code_fails_and_stays_normal(ed):
    feed(ed, "i", "return {}", "ESC")
    command(ed, ":w")
    assert ed.mode == NORMAL and ed.message.startswith("Failed") and not ed.done


def test_w_with_right_code_passes(ed):
    feed(ed, "i", BODY, "ESC")
    command(ed, ":w")
    assert ed.message.startswith("Passed!") and not ed.done
    command(ed, ":q")
    assert ed.done


def test_wq_wrong_asks_to_reveal_then_y_shows_solution(ed):
    feed(ed, "i", "return {}", "ESC")
    command(ed, ":wq")
    assert ed.mode == PROMPT and not ed.done and ed.message.startswith("Failed")
    feed(ed, "y")
    assert ed.mode == PAGER and ed.pager_text == ed.session.exercise.reference_solution
    assert "q, Esc or Enter" in rows_of(render(ed, 80, 24))[-1]
    feed(ed, "q")
    assert ed.mode == NORMAL


def test_wq_wrong_reveal_no(ed):
    feed(ed, "i", "return {}", "ESC")
    command(ed, ":wq")
    feed(ed, "n")
    assert ed.mode == NORMAL and not ed.done


def test_wq_right_quits(ed):
    feed(ed, "i", BODY, "ESC")
    command(ed, ":wq")
    assert ed.done and ed.farewell.startswith("Passed!")


def test_q_after_typing_is_refused_and_q_bang_quits(ed):
    ed.resize(120, 24)  # the refusal text fits on the message line
    feed(ed, "i", "pass", "ESC")
    command(ed, ":q")
    assert not ed.done and "Unchecked changes" in ed.message and ed.mode == NORMAL
    command(ed, ":q!")
    assert ed.done


def test_refusal_wider_than_screen_opens_pager(ed):
    ed.resize(50, 24)
    feed(ed, "i", "pass", "ESC")
    command(ed, ":q")  # the text is longer than 50 columns
    assert ed.mode == PAGER and "Unchecked changes" in ed.pager_text and not ed.done
    feed(ed, "q")
    assert ed.mode == NORMAL


def test_q_after_failed_check_offers_solution(ed):
    ed.resize(120, 24)
    feed(ed, "i", "return {}", "ESC")
    command(ed, ":w")
    command(ed, ":q")
    assert ed.mode == PROMPT and not ed.done and "didn't pass" in ed.message


def test_long_refusal_then_reveal_prompt(ed):
    ed.resize(60, 24)  # the failure fits on the message line, the refusal does not
    feed(ed, "i", "return {}", "ESC")
    command(ed, ":w")
    command(ed, ":q")  # long text -> pager first, then the reveal question
    assert ed.mode == PAGER and "didn't pass" in ed.pager_text
    feed(ed, "ENTER")
    assert ed.mode == PROMPT
    feed(ed, "Y")
    assert ed.mode == PAGER and ed.pager_title == "Solution"


def test_skip_resets_buffer_and_saved_code(ed):
    feed(ed, "i", "return {}", "ESC")
    command(ed, ":w")
    command(ed, ":s")
    ex = ed.session.exercise
    starter = ex.function_signature + "\n    "
    assert ed.buffer.text == starter and ed.session.saved_code == starter
    assert cursor(ed) == (1, 4) and ed.message.startswith("Skipped")
    command(ed, ":q")  # the new starter text counts as saved
    assert ed.done


def test_help_opens_pager(ed):
    command(ed, ":help")
    assert ed.mode == PAGER and ed.pager_text == builtins.zip.__doc__.strip()
    assert "zip" in ed.pager_title
    feed(ed, "ESC")
    assert ed.mode == NORMAL and ed.buffer.text == STARTER


def test_solution_opens_pager(ed):
    command(ed, ":solution")
    assert ed.mode == PAGER and "dict(zip" in ed.pager_text
    feed(ed, "ENTER")
    assert ed.mode == NORMAL


def test_pager_scrolls_long_text(ed):
    ed.resize(80, 10)
    ed.open_pager("t", "\n".join(f"line {n}" for n in range(50)))
    feed(ed, "j", "DOWN")
    assert ed.pager_top == 2
    feed(ed, " ")
    assert ed.pager_top == 10
    feed(ed, "k")
    assert ed.pager_top == 9
    feed(ed, "END")
    assert ed.pager_top == 50 - 8
    feed(ed, "PAGEDOWN")
    assert ed.pager_top == 50 - 8  # clamped at the end


def test_hint_has_prefix(ed):
    command(ed, ":h")
    assert ed.message == "Hint: " + ed.session.exercise.hint


def test_long_message_goes_to_pager(ed):
    ed.resize(30, 24)
    command(ed, ":h")
    assert ed.mode == PAGER and ed.pager_text.startswith("Hint: ")


def test_unknown_command(ed):
    command(ed, ":xyz")
    assert ed.mode == NORMAL and ed.message.startswith("Unknown command ':xyz'")
    assert not ed.done and ed.buffer.text == STARTER


def test_empty_command_does_nothing(ed):
    feed(ed, ":", "ENTER")
    assert ed.mode == NORMAL and ed.message == START_HINT


def test_command_line_backspace_and_esc_cancel(ed):
    feed(ed, ":", "w", "q")
    assert ed.mode == COMMAND and ed.cmdline == "wq"
    feed(ed, "BACKSPACE")
    assert ed.cmdline == "w"
    feed(ed, "BACKSPACE")
    assert ed.mode == COMMAND and ed.cmdline == ""
    feed(ed, "BACKSPACE")  # empty: cancels
    assert ed.mode == NORMAL
    feed(ed, ":", "q", "ESC")
    assert ed.mode == NORMAL and not ed.done
    feed(ed, ":", "q", "ENTER")  # the cancelled text is not kept
    assert ed.done


def test_ctrl_c_never_quits(ed):
    for mode_keys in ([], ["i"], [":", "q"]):
        feed(ed, *mode_keys)
        feed(ed, "CTRL_C")
        assert not ed.done and ed.message == CTRL_C_MESSAGE
        feed(ed, "ESC")
    assert ed.mode == NORMAL


def test_ctrl_c_in_insert_keeps_insert(ed):
    feed(ed, "i", "CTRL_C")
    assert ed.mode == INSERT and ed.message == CTRL_C_MESSAGE
    feed(ed, "x")
    assert ed.message == "" and ed.buffer.lines[1] == "    x"


# ----- editing ---------------------------------------------------------------------------------

def test_enter_auto_indents_after_def(ed):
    ed.buffer = Buffer("def f(x):")
    ed.buffer.col = len("def f(x):")
    feed(ed, "i", "ENTER")
    assert ed.buffer.lines == ["def f(x):", "    "] and cursor(ed) == (1, 4)
    feed(ed, "if x:", "ENTER")
    assert ed.buffer.lines[2] == "        " and cursor(ed) == (2, 8)
    feed(ed, "return 1", "ENTER")
    assert ed.buffer.lines[3] == "        "  # same indentation, no ':' at the end


def test_enter_splits_line(ed):
    ed.buffer = Buffer("    ab")
    ed.buffer.col = 5
    feed(ed, "i", "ENTER")
    assert ed.buffer.lines == ["    a", "    b"] and cursor(ed) == (1, 4)


def test_backspace_deletes_and_joins(ed):
    feed(ed, "i", "ab", "BACKSPACE")
    assert ed.buffer.lines[1] == "    a"
    feed(ed, "HOME", "BACKSPACE")
    assert ed.buffer.lines == ["def combine(keys, values):    a"]
    assert cursor(ed) == (0, len("def combine(keys, values):"))
    ed.buffer.row, ed.buffer.col = 0, 0
    feed(ed, "BACKSPACE")  # start of the buffer: nothing happens
    assert ed.buffer.lines == ["def combine(keys, values):    a"]


def test_backspace_ctrl_h_variant():
    assert parse_keys(b"\x08") == parse_keys(b"\x7f") == ["BACKSPACE"]


def test_tab_inserts_four_spaces(ed):
    feed(ed, "i", "TAB")
    assert ed.buffer.lines[1] == "        " and "\t" not in ed.buffer.text


def test_dd_deletes_line_and_keeps_one(ed):
    feed(ed, "d", "d")
    assert ed.buffer.lines == ["def combine(keys, values):"] and ed.mode == NORMAL
    feed(ed, "d", "d")
    assert ed.buffer.lines == [""] and cursor(ed) == (0, 0)


def test_d_then_other_key_cancels(ed):
    feed(ed, "d", "ESC", "d", "k")
    assert ed.buffer.text == STARTER and cursor(ed) == (0, 4)


def test_x_deletes_char_under_cursor(ed):
    feed(ed, "k", "HOME", "x")
    assert ed.buffer.lines[0] == "ef combine(keys, values):"
    feed(ed, "END", "x")  # at the end of the line: nothing to delete
    assert ed.buffer.lines[0] == "ef combine(keys, values):"


def test_o_opens_line_below_with_indent(ed):
    feed(ed, "o")
    assert ed.mode == INSERT and ed.buffer.lines == ["def combine(keys, values):", "    ", "    "]
    assert cursor(ed) == (2, 4)


def test_a_appends_after_cursor(ed):
    feed(ed, "k", "HOME", "a", "X")
    assert ed.buffer.lines[0] == "dXef combine(keys, values):" and ed.mode == INSERT


def test_hjkl_and_arrows_move(ed):
    feed(ed, "k")
    assert cursor(ed) == (0, 4)
    feed(ed, "l", "RIGHT")
    assert cursor(ed) == (0, 6)
    feed(ed, "h", "LEFT", "j")
    assert cursor(ed) == (1, 4)
    feed(ed, "i", "k")  # in INSERT, k is text
    assert ed.buffer.lines[1] == "    k"


def test_arrows_clamp_column(ed):
    feed(ed, "UP", "END")
    assert cursor(ed) == (0, 26)
    feed(ed, "DOWN")
    assert cursor(ed) == (1, 4)  # the second line is only 4 long
    feed(ed, "DOWN", "DOWN")
    assert cursor(ed) == (1, 4)  # can't leave the buffer
    feed(ed, "i", "UP", "RIGHT")
    assert cursor(ed) == (0, 5)


def test_delete_key(ed):
    feed(ed, "k", "HOME", "DELETE")
    assert ed.buffer.lines[0].startswith("ef ")
    feed(ed, "i", "END", "DELETE")  # INSERT: Delete at the end joins the next line
    assert ed.buffer.lines == ["ef combine(keys, values):    "]


def test_pasted_text_is_not_reindented(ed):
    ed.buffer = Buffer("")
    feed(ed, "i", "PASTE_START", "def f():", "ENTER", "    return 1", "PASTE_END")
    assert ed.buffer.lines == ["def f():", "    return 1"]


def test_paste_in_normal_inserts_text(ed):
    feed(ed, "PASTE_START", "dd:q", "PASTE_END")
    assert ed.mode == NORMAL and ed.buffer.lines[1] == "    dd:q" and not ed.done


def test_scrolls_to_keep_cursor_visible(ed):
    ed.resize(80, 10)
    feed(ed, "i")
    for _ in range(30):
        feed(ed, "ENTER")
    body = ed.body_rows()
    assert ed.top <= ed.buffer.row < ed.top + body
    frame = rows_of(render(ed, 80, 10))
    assert any(r.lstrip().startswith(str(ed.buffer.row + 1) + " ") for r in frame)
    assert not any(r.lstrip().startswith("1 ") for r in frame)  # line 1 scrolled off


# ----- parse_keys --------------------------------------------------------------------------------

def test_parse_arrows_both_forms():
    assert parse_keys(b"\x1b[A\x1b[B\x1b[C\x1b[D") == ["UP", "DOWN", "RIGHT", "LEFT"]
    assert parse_keys(b"\x1bOA\x1bOB\x1bOC\x1bOD") == ["UP", "DOWN", "RIGHT", "LEFT"]


def test_parse_lone_esc_and_esc_then_text():
    assert parse_keys(b"\x1b") == ["ESC"]
    assert parse_keys(b"ab\x1b") == ["a", "b", "ESC"]
    assert parse_keys(b"\x1b:wq\r") == ["ESC", ":", "w", "q", "ENTER"]
    assert parse_keys(b"\x1b\x1b") == ["ESC", "ESC"]


def test_parse_home_end_delete_pages():
    assert parse_keys(b"\x1b[H\x1b[F\x1b[1~\x1b[4~\x1b[3~") == ["HOME", "END", "HOME", "END", "DELETE"]
    assert parse_keys(b"\x1b[5~\x1b[6~") == ["PAGEUP", "PAGEDOWN"]


def test_parse_pasted_utf8_text():
    assert parse_keys("é → 中\r".encode()) == ["é", " ", "→", " ", "中", "ENTER"]
    assert parse_keys(b"x = 1\r\ny") == ["x", " ", "=", " ", "1", "ENTER", "y"]


def test_parse_bracketed_paste_and_unknown_sequences():
    assert parse_keys(b"\x1b[200~a\x1b[201~") == ["PASTE_START", "a", "PASTE_END"]
    assert parse_keys(b"\x1b[Ix") == ["x"]  # focus event: dropped


def test_parse_control_keys():
    assert parse_keys(b"\x03") == ["CTRL_C"]
    assert parse_keys(b"\t\x7f\x08\r") == ["TAB", "BACKSPACE", "BACKSPACE", "ENTER"]
    assert parse_keys(b"\x04") == ["CTRL_D"]


# ----- render -------------------------------------------------------------------------------------

def rows_of(frame):
    """Split a frame into its plain-text rows (by the row positioning codes)."""
    parts = re.split(r"\x1b\[(\d+);1H", frame)
    rows = {}
    for n, text in zip(parts[1::2], parts[2::2]):
        rows[int(n)] = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    return [rows[k] for k in sorted(rows)]


def test_render_status_line(ed):
    rows = rows_of(render(ed, 80, 24))
    status = rows[-2]
    assert "NORMAL" in status and "zip-01" in status and "2,5" in status
    assert len(status) == 80
    assert rows[-1] == START_HINT


def test_render_insert_label(ed):
    feed(ed, "i")
    rows = rows_of(render(ed, 80, 24))
    assert rows[-1] == "-- INSERT --" and "INSERT" in rows[-2]


def test_render_command_line(ed):
    feed(ed, ":", "w", "q")
    frame = render(ed, 80, 24)
    assert rows_of(frame)[-1] == ":wq"
    assert frame.endswith("\x1b[24;4H\x1b[?25h")  # cursor after the typed text


def test_render_prompt(ed):
    feed(ed, "i", "return {}", "ESC")
    command(ed, ":wq")
    rows = rows_of(render(ed, 80, 24))
    assert rows[-1].startswith("Reveal the solution? [y/N]") and rows[-2].startswith("✗ Failed")


def test_render_line_numbers_filler_and_header(ed):
    ed.intro_visible = False  # the start screen is tested separately below
    rows = rows_of(render(ed, 80, 24))
    assert rows[0].startswith("zip  ●○○  level 1 ─ Write a function")
    header = len(_header_rows(ed.session.exercise, 80, 24))
    assert 1 <= header <= 3
    assert rows[header:header + 2] == ["  1 def combine(keys, values):", "  2     "]
    assert rows[header + 2:-2] == ["~"] * (24 - header - 2 - 2)


def test_render_fits_height(ed):
    for width, height in [(80, 24), (40, 10), (120, 50), (20, 5)]:
        frame = render(ed, width, height)
        positions = [int(r) for r in re.findall(r"\x1b\[(\d+);\d+H", frame)]
        assert max(positions) <= height and len(rows_of(frame)) == height
        assert all(len(r) <= width for r in rows_of(frame))
        assert "\n" not in frame


def test_render_cursor_at_buffer_position(ed):
    frame = render(ed, 80, 24)
    rows = rows_of(frame)
    row, col = map(int, re.search(r"\x1b\[(\d+);(\d+)H\x1b\[\?25h$", frame).groups())
    assert rows[row - 1].startswith("  2 ") and col == 4 + 4 + 1  # gutter + 4 spaces, 1-based


def test_render_pager(ed):
    command(ed, ":help")
    rows = rows_of(render(ed, 80, 24))
    assert "Documentation: zip" in rows[0] and len(rows) == 24
    assert "q, Esc or Enter" in rows[-1]


def test_ctrl_c_in_pager_shows_message_in_footer(ed):
    command(ed, ":help")
    feed(ed, "CTRL_C")
    assert ed.mode == PAGER and rows_of(render(ed, 80, 24))[-1] == CTRL_C_MESSAGE


# ----- colours ---------------------------------------------------------------------------------------

SGR = re.compile(r"\x1b\[([0-9;]*)m")


def raw_rows(frame):
    """The rows of a frame with their colour codes (the clear-to-end-of-line code removed)."""
    frame = re.sub(r"\x1b\[\d+;\d+H\x1b\[\?25h$", "", frame)  # drop the final cursor positioning
    parts = re.split(r"\x1b\[(\d+);1H", frame)
    return [text.replace("\x1b[K", "") for text in parts[2::2]]


def colour_params(frame):
    """All SGR numbers that set a colour (30-49, 90-107, including 38;5;n / 48;2;r;g;b)."""
    nums = [int(p) for params in SGR.findall(frame) for p in params.split(";") if p]
    return [n for n in nums if 30 <= n <= 49 or 90 <= n <= 107]


def test_render_has_colours_and_every_row(ed):
    frame = render(ed, 80, 24)
    assert "\x1b[38;2;" in frame  # truecolor syntax colours
    body = re.sub(r"\x1b\[\d+;\d+H\x1b\[\?25h$", "", frame)  # without the final cursor position
    assert [int(n) for n in re.findall(r"\x1b\[(\d+);1H", body)] == list(range(1, 25))
    assert len(rows_of(frame)) == 24
    frame256 = render(ed, 80, 24, color_system="256")
    assert "\x1b[38;2;" not in frame256 and "38;5;" in frame256
    assert rows_of(frame256) == rows_of(frame)  # same text, only the colour codes differ


@pytest.mark.parametrize("keys, label, background", [
    ([], "NORMAL", "44"), (["i"], "INSERT", "42"), ([":"], "COMMAND", "43")])
def test_status_line_mode_block(ed, keys, label, background):
    feed(ed, *keys)
    frame = render(ed, 80, 24)
    assert rows_of(frame)[-2].startswith(f" {label} ")
    assert raw_rows(frame)[-2].startswith(f"\x1b[1;30;{background}m {label} \x1b[0m")


def test_current_line_number_stands_out(ed):
    header = len(_header_rows(ed.session.exercise, 80, 24))

    def number_codes():
        rows = raw_rows(render(ed, 80, 24))
        return [SGR.match(rows[header + n]).group(1) for n in (0, 1)]

    first, second = number_codes()  # the cursor starts on line 2
    assert second == "1;33" and first != second
    feed(ed, "k")
    assert number_codes() == [second, first]


def test_messages_are_coloured_by_kind(ed):
    assert raw_rows(render(ed, 80, 24))[-1] == f"\x1b[2m{START_HINT}\x1b[0m"  # dim
    feed(ed, "i", BODY, "ESC")
    command(ed, ":w")
    assert ed.message_kind == "pass"
    assert raw_rows(render(ed, 80, 24))[-1].startswith("\x1b[1;32m✓ Passed!")
    command(ed, ":xyz")
    assert raw_rows(render(ed, 80, 24))[-1].startswith("\x1b[31mUnknown command")
    feed(ed, "CTRL_C")
    assert ed.message_kind == "" and raw_rows(render(ed, 80, 24))[-1].startswith("\x1b[2m")


def test_refused_and_failed_messages_have_icons(ed):
    feed(ed, "i", "return {}", "ESC")
    command(ed, ":w")
    assert rows_of(render(ed, 80, 24))[-1].startswith("✗ Failed")
    feed(ed, "i", "x", "ESC")
    command(ed, ":q")
    assert rows_of(render(ed, 80, 24))[-1] == "! " + ed.message


def test_pager_titles_and_solution_colours(ed):
    command(ed, ":help")
    assert raw_rows(render(ed, 80, 24))[0].startswith("\x1b[1;7;34m Documentation: zip")
    feed(ed, "q")
    command(ed, ":solution")
    rows = raw_rows(render(ed, 80, 24))
    assert rows[0].startswith("\x1b[1;7;32m Solution")
    assert "\x1b[38;2;" in rows[1]  # the solution is syntax highlighted


def test_no_color_keeps_reverse_status_line(ed, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    frame = render(ed, 80, 24)
    assert colour_params(frame) == []
    assert raw_rows(frame)[-2].startswith("\x1b[1;7m NORMAL ")
    assert rows_of(frame) == rows_of(render(ed, 80, 24, color_system=None))


def test_color_system_none_has_no_codes(ed):
    assert not SGR.search(render(ed, 80, 24, color_system=None))


def test_bottom_right_cell_never_written(ed):
    ed.message = "x" * 200
    for mode_keys in ([], [":"] + ["w"] * 200):
        feed(ed, *mode_keys)
        assert len(rows_of(render(ed, 80, 24))[-1]) <= 79


# ----- which front-end ui.run() picks ---------------------------------------------------------------

def test_editor_not_available_without_a_terminal():
    from pygym.editor import editor_available
    assert editor_available() is False  # pytest captures stdin/stdout: not TTYs


@pytest.mark.parametrize("notebook, terminal, tk, expected", [
    (True, True, True, "jupyter"),
    (False, True, True, "editor"),
    (False, False, True, "tk"),
    (False, False, False, "text"),
])
def test_auto_routing(monkeypatch, notebook, terminal, tk, expected):
    from pygym import editor, jupyter, ui

    called = []
    monkeypatch.setattr(editor, "is_windows", lambda: False)   # on Windows the default is tk (see below)
    monkeypatch.setattr(jupyter, "in_notebook", lambda: notebook)
    monkeypatch.setattr(editor, "editor_available", lambda: terminal)
    monkeypatch.setattr(ui, "tk_available", lambda: tk)
    monkeypatch.setattr(jupyter, "start", lambda s: called.append("jupyter"))
    monkeypatch.setattr(editor, "run_editor", lambda s: called.append("editor"))
    monkeypatch.setattr(ui, "run_tk", lambda s: called.append("tk"))
    monkeypatch.setattr(ui, "run_text", lambda s: called.append("text"))
    ui.run(Session("zip", 1))
    assert called == [expected]


@pytest.mark.parametrize("terminal, tk, expected", [
    (True, True, "tk"),        # the Windows default: the popup, even in a terminal
    (True, False, "text"),     # no tkinter: plain text, the editor only runs when asked for (ui="editor")
    (False, True, "tk"),
])
def test_auto_routing_on_windows(monkeypatch, terminal, tk, expected):
    from pygym import editor, jupyter, ui

    called = []
    monkeypatch.setattr(editor, "is_windows", lambda: True)
    monkeypatch.setattr(jupyter, "in_notebook", lambda: False)
    monkeypatch.setattr(editor, "editor_available", lambda: terminal)
    monkeypatch.setattr(ui, "tk_available", lambda: tk)
    monkeypatch.setattr(editor, "run_editor", lambda s: called.append("editor"))
    monkeypatch.setattr(ui, "run_tk", lambda s: called.append("tk"))
    monkeypatch.setattr(ui, "run_text", lambda s: called.append("text"))
    ui.run(Session("zip", 1))
    assert called == [expected]


def test_forced_editor_without_terminal_falls_back_to_text(monkeypatch, capsys):
    from pygym import editor, ui

    called = []
    monkeypatch.setattr(editor, "editor_available", lambda: False)
    monkeypatch.setattr(ui, "run_text", lambda s: called.append("text"))
    ui.run(Session("zip", 1), ui="editor")
    assert called == ["text"] and "line CLI" in capsys.readouterr().out


# ----- start screen and :intro ----------------------------------------------------------------

def _plain_rows(ed, w, h):
    raw = render(ed, w, h, color_system=None)
    rows = [""] * h
    for m in re.finditer(r"\x1b\[(\d+);1H(.*?)(?=\x1b\[\d+;1H|\x1b\[\d+;\d+H\x1b\[\?25h$|$)", raw, re.S):
        rows[int(m.group(1)) - 1] = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", m.group(2))
    return rows


def test_start_screen_lists_every_command(ed):
    from pygym.session import COMMANDS
    screen = "\n".join(_plain_rows(ed, 90, 24))
    assert "NORMAL" in screen and "INSERT" in screen and "Esc" in screen
    for name, _ in COMMANDS:
        assert name in screen, name
    assert ":intro" in ed.message


def test_start_screen_fits_and_stays_in_filler_rows(ed):
    rows = _plain_rows(ed, 80, 24)
    assert len(rows) == 24
    intro_rows = [r for r in rows[:-2] if ":w " in r or "NORMAL  " in r]  # not the status/hint rows
    assert intro_rows and all(r.startswith("~") for r in intro_rows)


def test_start_screen_small_terminal_points_to_intro(ed):
    rows = _plain_rows(ed, 60, 14)
    assert any(":intro for the full list" in r for r in rows)


def test_moving_and_commands_keep_start_screen(ed):
    feed(ed, "DOWN", "UP", "l", "h")
    command(ed, ":h")
    assert ed.intro_visible


def test_first_edit_hides_start_screen(ed):
    feed(ed, "i", "x")
    assert not ed.intro_visible
    assert not any(":solution" in r for r in _plain_rows(ed, 90, 24))


def test_intro_command_opens_command_list(ed):
    feed(ed, "i", "x", "ESC")
    command(ed, ":intro")
    assert ed.mode == PAGER and ed.pager_title == "pygym commands"
    assert ":solution" in ed.pager_text and ":intro" in ed.pager_text
    feed(ed, "q")
    assert ed.mode == NORMAL
