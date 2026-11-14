"""The cursor must sit where the text really is when the line holds double-width characters.

Chinese (and Japanese, Korean) characters take two screen cells. The editor placed the cursor by
character count, so after typing 中文 in a comment the cursor was drawn two cells inside the text.
"""
import pytest
from rich.cells import cell_len

from pygym.editor import COMMAND, Editor, _editor_frame
from pygym.session import Session

GUTTER = 4          # a 3-digit line number and a space


@pytest.fixture(autouse=True)
def _private(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))


def cursor_for(text, col):
    ed = Editor(Session("zip", 1), 80, 24)
    ed.buffer.lines = ["def combine(keys, values):", text, "    return 1"]
    ed.buffer.row, ed.buffer.col = 1, col
    return _editor_frame(ed, 80, 24, True)[1]


def test_ascii_is_unchanged():
    assert cursor_for("    # abc", 8)[1] == GUTTER + 8 + 1


def test_cursor_after_wide_characters_counts_two_cells_each():
    text = "    # 中文abc"
    assert cursor_for(text, 8)[1] == GUTTER + cell_len(text[:8]) + 1      # 4 + 4 wide cells... = 14
    assert cursor_for(text, 8)[1] == GUTTER + 10 + 1


def test_cursor_before_the_wide_characters_is_not_moved():
    assert cursor_for("    # 中文abc", 6)[1] == GUTTER + 6 + 1


def test_cursor_at_the_start_and_end_of_a_wide_line():
    text = "中文"
    assert cursor_for(text, 0)[1] == GUTTER + 1
    assert cursor_for(text, 2)[1] == GUTTER + 4 + 1


def test_the_command_line_cursor_counts_cells_too():
    ed = Editor(Session("zip", 1), 80, 24)
    ed.mode, ed.cmdline = COMMAND, "中"
    assert _editor_frame(ed, 80, 24, True)[1] == (24, cell_len(":中") + 1)
