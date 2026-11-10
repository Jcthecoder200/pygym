"""The editor's Windows layer, tested on any OS with a fake msvcrt (no Windows console needed)."""
import pytest

from pygym import editor, jupyter, ui
from pygym.editor import (Editor, read_windows_keys, translate_windows_keys)
from pygym.session import Session


@pytest.fixture(autouse=True)
def _private_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))


class FakeMsvcrt:
    """Plays back characters the way msvcrt.getwch()/kbhit() would deliver them.

    `batches` is a list of strings; each string is what is already waiting when the editor reads
    (one key typed by a person = one batch; a paste = one long batch).
    """

    def __init__(self, batches):
        self.pending = [list(b) for b in batches]
        self.current = []   # keys waiting right now
        self.idle = 0

    def kbhit(self):
        if self.current:
            return True
        if self.pending:  # time passes: the next batch arrives after this "nothing waiting"
            self.current = self.pending.pop(0)
            return False
        self.idle += 1
        if self.idle > 3:
            raise RuntimeError("the test ran out of keys before the editor quit")
        return False

    def getwch(self):
        if not self.current:  # getwch blocks until a key arrives
            if not self.pending:
                raise RuntimeError("the test ran out of keys before the editor quit")
            self.current = self.pending.pop(0)
        return self.current.pop(0)


# ----- key translation ----------------------------------------------------------------------

@pytest.mark.parametrize("chars,expected", [
    (["\xe0", "H"], ["UP"]), (["\xe0", "P"], ["DOWN"]),
    (["\xe0", "K"], ["LEFT"]), (["\xe0", "M"], ["RIGHT"]),
    (["\x00", "H"], ["UP"]),                      # numpad arrows use the \x00 prefix
    (["\xe0", "G"], ["HOME"]), (["\xe0", "O"], ["END"]), (["\xe0", "S"], ["DELETE"]),
    (["\xe0", "I"], ["PAGEUP"]), (["\xe0", "Q"], ["PAGEDOWN"]),
    (["\x00", ";"], []),                          # F1: ignored
    (["\x1b"], ["ESC"]), (["\r"], ["ENTER"]), (["\x08"], ["BACKSPACE"]), (["\t"], ["TAB"]),
    (["\x03"], ["CTRL_C"]), (["\x04"], ["CTRL_D"]),
    (["a"], ["a"]), ([":"], [":"]), (["é"], ["é"]),
])
def test_translate(chars, expected):
    assert translate_windows_keys(chars) == expected


def test_esc_then_colon_are_two_keys():
    assert translate_windows_keys(["\x1b", ":"]) == ["ESC", ":"]  # no escape sequences on Windows


def test_paste_burst_is_wrapped():
    keys = translate_windows_keys(list("x = 1\r    y = 2"))
    assert keys[0] == "PASTE_START" and keys[-1] == "PASTE_END" and "ENTER" in keys


def test_fast_arrows_are_not_a_paste():
    assert translate_windows_keys(["\xe0", "P"] * 5) == ["DOWN"] * 5


def test_read_takes_everything_waiting():
    fake = FakeMsvcrt(["abc", "z"])
    assert read_windows_keys(fake) == ["a", "b", "c"]
    fake = FakeMsvcrt(["\xe0H", "z"])
    assert read_windows_keys(fake) == ["UP"]


# ----- the Windows loop, end to end with a fake console ---------------------------------------

def run_windows(monkeypatch, batches, interrupt_at=None):
    import sys
    fake = FakeMsvcrt(batches)
    if interrupt_at is not None:  # Ctrl-C: Python raises KeyboardInterrupt instead of a key
        real_getwch, count = fake.getwch, [0]

        def getwch():
            count[0] += 1
            if count[0] == interrupt_at:
                raise KeyboardInterrupt
            return real_getwch()
        fake.getwch = getwch
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    modes = []
    monkeypatch.setattr(editor, "_win_vt_mode",
                        lambda enable, restore=None: modes.append((enable, restore)) or 7)
    out = []
    monkeypatch.setattr(editor, "_write", out.append)
    monkeypatch.setattr("time.sleep", lambda s: None)
    ed = Editor(Session("zip", 1), 80, 24)
    editor._run_windows(ed)
    return ed, "".join(out), modes


def test_windows_loop_types_checks_and_quits(monkeypatch):
    keys = ["i", *"return dict(zip(keys, values))", "\x1b", ":", "w", "q", "\r"]
    ed, out, modes = run_windows(monkeypatch, keys)
    assert ed.done and ed.farewell.startswith("Passed!")
    assert out.startswith("\x1b[?1049h") and out.endswith("\x1b[?1049l")  # alternate screen in/out
    assert modes[0] == (True, None) and modes[-1] == (False, 7)              # console mode restored


def test_windows_ctrl_c_does_not_quit(monkeypatch):
    ed, out, _ = run_windows(monkeypatch, [":", "q", "!", "\r"], interrupt_at=1)
    # Ctrl-C became CTRL_C: a message only, the editor kept running until :q!
    assert "Type :q to quit" in out
    assert ed.done and "code not checked" in ed.farewell


def test_windows_restores_console_after_crash(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "msvcrt", FakeMsvcrt(["i"]))
    modes = []
    monkeypatch.setattr(editor, "_win_vt_mode",
                        lambda enable, restore=None: modes.append((enable, restore)) or 7)
    out = []
    monkeypatch.setattr(editor, "_write", out.append)
    ed = Editor(Session("zip", 1), 80, 24)

    def boom(key):
        raise RuntimeError("bug")
    monkeypatch.setattr(ed, "feed", boom)
    with pytest.raises(RuntimeError):
        editor._run_windows(ed)
    assert out[-1].endswith("\x1b[?1049l") and modes[-1] == (False, 7)


# ----- routing: Windows keeps the tkinter popup as the default ---------------------------------

def _route(monkeypatch, windows, terminal, tk, ui_value="auto"):
    called = []
    monkeypatch.setattr(editor, "is_windows", lambda: windows)
    monkeypatch.setattr(jupyter, "in_notebook", lambda: False)
    monkeypatch.setattr(editor, "editor_available", lambda: terminal)
    monkeypatch.setattr(ui, "tk_available", lambda: tk)
    monkeypatch.setattr(editor, "run_editor", lambda s: called.append("editor"))
    monkeypatch.setattr(ui, "run_tk", lambda s: called.append("tk"))
    monkeypatch.setattr(ui, "run_text", lambda s: called.append("text"))
    ui.run(Session("zip", 1), ui_value)
    return called


def test_windows_default_is_tk(monkeypatch):
    assert _route(monkeypatch, windows=True, terminal=True, tk=True) == ["tk"]


def test_windows_without_tk_uses_line_cli(monkeypatch):
    assert _route(monkeypatch, windows=True, terminal=True, tk=False) == ["text"]


def test_windows_forced_editor(monkeypatch):
    assert _route(monkeypatch, windows=True, terminal=True, tk=True, ui_value="editor") == ["editor"]


def test_linux_default_is_editor(monkeypatch):
    assert _route(monkeypatch, windows=False, terminal=True, tk=True) == ["editor"]
