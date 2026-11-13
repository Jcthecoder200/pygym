"""The Windows editor's raw-event input (ReadConsoleInputW), tested on any OS.

The sample events are what a real Windows 11 / Windows Terminal console delivered (tools/
real_machine_check.py --keys2): arrow keys have character 0 and a virtual-key code, while à, é
and 中 arrive as characters with virtual key 0. msvcrt.getwch() cannot tell the letter à from the
"\\xe0" it puts in front of every arrow key; this layer can.
"""
import ctypes

import pytest

from pygym import editor
from pygym.editor import Editor, windows_events_to_keys
from pygym.session import Session

LEFT_ALT, RIGHT_ALT, LEFT_CTRL, SHIFT, ENHANCED, NUMLOCK = 0x2, 0x1, 0x8, 0x10, 0x100, 0x20


def key(vk=0, ch="\x00", down=True, state=0, repeat=1):
    return (down, vk, ch, state, repeat)


@pytest.fixture(autouse=True)
def _private_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))


# ----- translation: the real events from the Windows 11 probe -------------------------------

def test_arrow_and_home_have_no_character():
    events = [key(0x25, state=ENHANCED | NUMLOCK), key(0x24, state=ENHANCED | NUMLOCK)]
    assert windows_events_to_keys(events) == ["LEFT", "HOME"]


@pytest.mark.parametrize("vk,name", [(0x26, "UP"), (0x28, "DOWN"), (0x27, "RIGHT"), (0x23, "END"),
                                     (0x21, "PAGEUP"), (0x22, "PAGEDOWN"), (0x2E, "DELETE")])
def test_navigation_keys(vk, name):
    assert windows_events_to_keys([key(vk, state=ENHANCED)]) == [name]


def test_the_letter_a_grave_is_text_not_a_key_prefix():
    # Alt+0224 as Windows Terminal reported it: Alt down, then the character, then its key-up
    events = [key(0x12, state=LEFT_ALT | NUMLOCK), key(0, "à"), key(0, "à", down=False)]
    assert windows_events_to_keys(events) == ["à"]


def test_a_grave_between_arrows_does_not_eat_its_neighbours():
    # the bug with getwch(): "\xe0" then "K" looked like Left, and a typed à took the next key
    events = [key(0x25, state=ENHANCED), key(0, "à"), key(0x4B, "K", state=SHIFT), key(0x25, state=ENHANCED)]
    assert windows_events_to_keys(events) == ["LEFT", "à", "K", "LEFT"]


def test_e_acute_and_a_chinese_character():
    events = [key(0, "é"), key(0, "é", down=False), key(0, "中"), key(0, "中", down=False)]
    assert windows_events_to_keys(events) == ["é", "中"]


def test_a_paste_of_ctrl_v_leaves_no_stray_control_key():
    # seen in the probe: Ctrl down, the pasted characters, then the key-up of V with char \x16
    events = [key(0x11, state=LEFT_CTRL | NUMLOCK), key(0, "中"), key(0x56, "\x16", down=False, state=LEFT_CTRL)]
    assert windows_events_to_keys(events) == ["中"]


def test_key_up_events_are_ignored():
    events = [key(0x08, "\x08"), key(0x08, "\x08", down=False)]
    assert windows_events_to_keys(events) == ["BACKSPACE"]


def test_old_console_host_sends_an_alt_numpad_character_on_alt_release():
    assert windows_events_to_keys([key(0x12, "à", down=False)]) == ["à"]
    assert windows_events_to_keys([key(0x12, "\x00", down=False)]) == []


def test_modifier_keys_alone_do_nothing():
    events = [key(0x10), key(0x11, state=LEFT_CTRL), key(0x12, state=LEFT_ALT), key(0x14)]
    assert windows_events_to_keys(events) == []


@pytest.mark.parametrize("event", [
    key(0x25, state=ENHANCED | LEFT_CTRL),         # Ctrl+Left (ignored by the editor, as before)
    key(0x25, state=ENHANCED | LEFT_ALT),          # Alt+Left
    key(0x46, "f", state=LEFT_ALT),                # Alt+F chord
    key(0x09, "\t", state=SHIFT),                  # Shift+Tab
    key(0x70),                                     # F1
    key(0x7B),                                     # F12
])
def test_keys_the_editor_never_used_stay_ignored(event):
    assert windows_events_to_keys([event]) == []


def test_altgr_still_types_its_character():
    assert windows_events_to_keys([key(0x32, "@", state=RIGHT_ALT | LEFT_CTRL)]) == ["@"]


@pytest.mark.parametrize("event,name", [
    (key(0x0D, "\r"), "ENTER"), (key(0x0D, "\n", state=LEFT_CTRL), "ENTER"),
    (key(0x09, "\t"), "TAB"), (key(0x1B, "\x1b"), "ESC"), (key(0x08, "\x7f", state=LEFT_CTRL), "BACKSPACE"),
    (key(0x41, "\x01", state=LEFT_CTRL), "CTRL_A"), (key(0x43, "\x03", state=LEFT_CTRL), "CTRL_C"),
    (key(0x41, "a"), "a"), (key(0xBA, ":", state=SHIFT), ":"), (key(0x20, " "), " "),
])
def test_text_and_control_keys(event, name):
    assert windows_events_to_keys([event]) == [name]


def test_a_held_key_repeats():
    assert windows_events_to_keys([key(0x25, state=ENHANCED, repeat=3)]) == ["LEFT"] * 3
    assert windows_events_to_keys([key(0x41, "a", repeat=2)]) == ["a", "a"]


def test_characters_above_ffff_arrive_as_two_surrogates():
    hi, lo = "\ud83d", "\ude00"                      # U+1F600
    assert windows_events_to_keys([key(0, hi), key(0, lo)]) == ["\U0001F600"]
    assert windows_events_to_keys([key(0, lo)]) == []                 # half a pair: dropped
    assert windows_events_to_keys([key(0, hi), key(0, "a")]) == ["a"]


def test_a_burst_with_text_and_enter_is_a_paste():
    events = [key(0, c) for c in "ab"] + [key(0x0D, "\r"), key(0, "c")]
    keys = windows_events_to_keys(events)
    assert keys[0] == "PASTE_START" and keys[-1] == "PASTE_END" and "ENTER" in keys


def test_fast_arrows_are_not_a_paste():
    assert windows_events_to_keys([key(0x25, state=ENHANCED)] * 5) == ["LEFT"] * 5


# ----- the reader, against a fake kernel32 -------------------------------------------------

class FakeKernel32:
    """Hands out raw INPUT_RECORDs the way ReadConsoleInputW does."""

    def __init__(self, records, is_console=True):
        self.records, self.is_console, self.reads = list(records), is_console, 0

    def GetStdHandle(self, which):
        assert which == -10
        return 1234

    def GetConsoleMode(self, handle, mode_ref):
        return 1 if self.is_console else 0

    def GetNumberOfConsoleInputEvents(self, handle, count_ref):
        count_ref._obj.value = len(self.records)
        return 1

    def ReadConsoleInputW(self, handle, buffer, length, count_ref):
        self.reads += 1
        taken, self.records = self.records[:length], self.records[length:]
        for i, record in enumerate(taken):
            buffer[i] = record
        count_ref._obj.value = len(taken)
        return 1


def record(vk=0, ch="\x00", down=True, state=0, repeat=1, kind=1):
    rec = editor._input_record_type()()
    rec.EventType = kind
    rec.Event.Key.bKeyDown, rec.Event.Key.wRepeatCount = int(down), repeat
    rec.Event.Key.wVirtualKeyCode, rec.Event.Key.uChar = vk, ord(ch)
    rec.Event.Key.dwControlKeyState = state
    return rec


def test_the_record_layout_is_the_windows_one():
    assert ctypes.sizeof(editor._input_record_type()) == 20


def test_reader_returns_keys_and_skips_non_key_events():
    kernel = FakeKernel32([record(kind=16), record(0x25, state=ENHANCED), record(0, "à"),
                           record(kind=2), record(0, "à", down=False)])
    reader = editor._ConsoleReader(kernel)
    assert reader.pending() is True
    assert reader.read() == ["LEFT", "à"]
    assert reader.pending() is False


def test_reader_takes_a_long_paste_in_one_read():
    text = "x" * 300                                  # > BATCH records: several reads, one result
    kernel = FakeKernel32([record(0, c) for c in text] + [record(0x0D, "\r")])
    keys = editor._ConsoleReader(kernel).read()
    assert keys[0] == "PASTE_START" and keys.count("x") == 300 and kernel.reads >= 2


def test_reader_refuses_a_stdin_that_is_not_a_console():
    with pytest.raises(OSError):
        editor._ConsoleReader(FakeKernel32([], is_console=False))


def test_without_a_console_the_msvcrt_reader_is_used(monkeypatch):
    import sys
    sentinel = object()
    monkeypatch.setitem(sys.modules, "msvcrt", sentinel)
    monkeypatch.setattr(editor, "_ConsoleReader", lambda: (_ for _ in ()).throw(OSError("no console")))
    reader = editor._windows_reader()
    assert isinstance(reader, editor._MsvcrtReader) and reader._msvcrt is sentinel


# ----- the whole loop with the raw reader --------------------------------------------------

class FakeReader:
    """Batches of events; one batch per 'time passes' step, like a person typing."""

    def __init__(self, batches):
        self.batches, self.current = [list(b) for b in batches], None

    def pending(self):
        if self.current is None and self.batches:
            self.current = self.batches.pop(0)
            return False                            # nothing waiting yet; the keys arrive next time
        if self.current is not None:
            return True
        raise RuntimeError("the test ran out of keys before the editor quit")

    def read(self):
        keys, self.current = windows_events_to_keys(self.current), None
        return keys


def typed(text):
    return [[key(0, c)] for c in text]


def run_with_reader(monkeypatch, batches):
    monkeypatch.setattr(editor, "_win_vt_mode", lambda enable, restore=None: 7)
    monkeypatch.setattr(editor, "_write", lambda text: None)
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(editor, "_windows_reader", lambda: FakeReader(batches))
    ed = Editor(Session("zip", 1), 80, 24)
    editor._run_windows(ed)
    return ed


def test_typing_a_grave_in_the_editor_loses_nothing(monkeypatch):
    # i = insert; type "voilà"; Left arrow; type "X"; Esc; :q! Enter
    batches = ([[key(0, "i")]] + typed("voilà") + [[key(0x25, state=ENHANCED)]] + typed("X")
               + [[key(0x1B, "\x1b")]] + typed(":q!") + [[key(0x0D, "\r")]])
    ed = run_with_reader(monkeypatch, batches)
    assert ed.done
    assert "voilXà" in ed.buffer.text      # à kept, the Left arrow moved before it, X went in


def test_pasted_text_with_a_grave_arrives_whole(monkeypatch):
    paste = [key(0, c) for c in "café à la carte"] + [key(0x0D, "\r")]
    batches = [[key(0, "i")], paste, [key(0x1B, "\x1b")]] + typed(":q!") + [[key(0x0D, "\r")]]
    ed = run_with_reader(monkeypatch, batches)
    assert ed.done and "café à la carte" in ed.buffer.text


def test_the_msvcrt_fallback_still_has_the_old_limit():
    # documents why the raw reader exists: getwch() sends "\xe0" before arrow keys AND for the letter
    from pygym.editor import translate_windows_keys
    assert translate_windows_keys(["\xe0", "x"]) == []            # à and the next key are lost
    assert translate_windows_keys(["\xe0", "K"]) == ["LEFT"]
