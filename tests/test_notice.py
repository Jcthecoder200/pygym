"""Session.notice and the AI badge in every front-end, :help for dotted targets, :s without a
next exercise. No LLM: ai.generate_exercise is replaced by a fake."""
import dataclasses
import re

import pytest

from pygym import ai, editor, jupyter, loader, style, ui
from pygym.models import Exercise
from pygym.session import Session

FALLBACK_REASON = "Ollama is not running at http://localhost:11434"


def ai_copy(base_id: str = "zip-01", n: int = 1) -> Exercise:
    """A preset exercise dressed up as a generated one (so it can really be graded)."""
    base = next(e for e in loader.load_all() if e.id == base_id)
    return dataclasses.replace(base, id=f"ai-{base.target_function}-{base.difficulty}-{n:08x}",
                               source="ai", generator="fake:model")


@pytest.fixture
def fake_ai(monkeypatch):
    """generate_exercise returns a new generated exercise on every call."""
    calls = []

    def generate(target, difficulty, *, avoid_ids=None, settings=None, timeout=20.0):
        calls.append(target)
        return ai_copy(n=len(calls))

    monkeypatch.setattr(ai, "generate_exercise", generate)
    return calls


@pytest.fixture
def offline_ai(monkeypatch):
    def generate(target, difficulty, **kwargs):
        raise ai.GenerationUnavailable(FALLBACK_REASON)

    monkeypatch.setattr(ai, "generate_exercise", generate)


def plain(renderable) -> str:
    return style.to_plain(renderable, width=120)


# ----- style ----------------------------------------------------------------------------------

def test_title_line_has_an_ai_badge_only_for_generated_exercises():
    preset = loader.pick("zip", 1)
    assert "AI" not in plain(style.title_line(preset))
    line = style.title_line(ai_copy())
    assert line.plain.endswith("level 1  " + style.AI_BADGE)
    assert any(span.style == style.PALETTE["ai_badge"] for span in line.spans)


def test_notice_line_is_coloured_and_has_an_icon():
    text = style.notice(ai.WARNING)
    assert text.plain == "! " + ai.WARNING
    assert text.style == style.PALETTE["notice"]


def test_exercise_view_shows_the_notice_below_the_panel(fake_ai):
    s = Session("zip", 1, mode="ai")
    out = plain(style.exercise_view(s))
    assert out.rstrip().endswith("! " + ai.WARNING)
    assert style.AI_BADGE.strip() in out


def test_exercise_view_without_notice_is_just_the_panel():
    s = Session("zip", 1, mode="preset")
    assert s.notice == ""
    assert plain(style.exercise_view(s)) == plain(style.exercise_header(s.exercise))


# ----- line CLI -------------------------------------------------------------------------------

def drive_text(session, lines):
    it = iter(lines)
    out = []

    def fake_input(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    ui.run_text(session, input_fn=fake_input, print_fn=lambda *a: out.append(" ".join(map(str, a))))
    return "\n".join(out)


def test_cli_shows_the_ai_warning_at_start_and_after_skip(fake_ai):
    out = drive_text(Session("zip", 1, mode="ai"), [":s", ":q!"])
    assert out.count("! " + ai.WARNING) == 2  # once for each generated exercise
    assert "ai-zip-1-00000001" in out and "ai-zip-1-00000002" in out
    assert fake_ai == ["zip", "zip"]


def test_cli_shows_the_fallback_note(offline_ai):
    s = Session("zip", 1, mode="ai")
    assert s.exercise.source == "preset"
    out = drive_text(s, [":q!"])
    assert f"! AI mode unavailable ({FALLBACK_REASON}); using the preset exercises." in out
    assert " AI " not in out.split("\n")[-1]


def test_cli_preset_mode_has_no_notice():
    out = drive_text(Session("zip", 1, mode="preset"), [":q!"])
    assert "! AI" not in out and "AI mode" not in out


# ----- tkinter (run_tk itself is unchanged: the notice goes to the terminal first) --------------

def test_tk_prints_the_notice_before_the_window(fake_ai, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(ui, "run_tk", lambda s: opened.append(capsys.readouterr().out))
    ui.run(Session("zip", 1, mode="ai"), "tk")
    assert opened == ["! " + ai.WARNING + "\n"]


# ----- editor -----------------------------------------------------------------------------------

def rows_of(frame):
    parts = re.split(r"\x1b\[(\d+);1H", frame)
    rows = {}
    for n, text in zip(parts[1::2], parts[2::2]):
        rows[int(n)] = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    return [rows[k] for k in sorted(rows)]


def test_editor_shows_the_notice_on_the_message_line_and_start_screen(fake_ai):
    ed = editor.Editor(Session("zip", 1, mode="ai"), 80, 24)
    assert (ed.message, ed.message_kind) == (ai.WARNING, "notice")
    rows = rows_of(editor.render(ed, 80, 24))
    assert rows[0].startswith("zip  ●○○  level 1  " + style.AI_BADGE + " ─ ")
    assert rows[-1] == "! " + ai.WARNING
    assert any(row.startswith("~") and "! AI-generated exercise" in row for row in rows)


def test_editor_message_line_is_yellow_for_the_notice(fake_ai):
    ed = editor.Editor(Session("zip", 1, mode="ai"), 80, 24)
    frame = editor.render(ed, 80, 24, color_system="standard")
    last = re.split(r"\x1b\[\d+;1H", frame)[-1]
    assert "\x1b[33m! AI-generated" in last  # 33 = yellow


def test_editor_shows_the_notice_again_after_skip(fake_ai):
    ed = editor.Editor(Session("zip", 1, mode="ai"), 80, 24)
    ed.feed("i")  # typing clears the message line
    assert ed.message == ""
    ed.feed("ESC")
    for key in [":", "s", "ENTER"]:
        ed.feed(key)
    assert ed.session.exercise.id == "ai-zip-1-00000002"
    assert (ed.message, ed.message_kind) == (ai.WARNING, "notice")


def test_editor_long_fallback_note_is_wrapped_on_the_start_screen(offline_ai):
    ed = editor.Editor(Session("zip", 1, mode="ai"), 60, 30)
    assert ed.message_kind == "notice" and ed.message.startswith("AI mode unavailable")
    rows = rows_of(editor.render(ed, 60, 30))
    screen = " ".join(" ".join(r.lstrip("~").split()) for r in rows if r.startswith("~"))
    assert f"AI mode unavailable ({FALLBACK_REASON}); using the preset exercises." in screen
    assert all(len(r) <= 60 for r in rows)
    assert style.AI_BADGE not in rows[0]


def test_editor_without_notice_keeps_the_start_hint():
    ed = editor.Editor(Session("zip", 1, mode="preset"), 80, 24)
    assert ed.message == editor.START_HINT


# ----- Jupyter ------------------------------------------------------------------------------------

class FakeIPython:
    def register_magic_function(self, fn, magic_kind, magic_name):
        self.magic = fn


def test_jupyter_shows_the_notice_at_start_and_after_skip(fake_ai, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setattr(jupyter, "_active", None)
    jupyter.start(Session("zip", 1, mode="ai"), FakeIPython())
    out = capsys.readouterr().out
    assert "! " + ai.WARNING in out and "level 1   AI " in out
    jupyter.pygym_magic(":s")
    out = capsys.readouterr().out
    assert "Skipped." in out and "! " + ai.WARNING in out and "ai-zip-1-00000002" in out


# ----- :help for dotted targets -----------------------------------------------------------------

def session_for(target: str) -> Session:
    s = Session("zip", 1, mode="preset")
    s.exercise = dataclasses.replace(s.exercise, target_function=target)
    return s


def test_help_for_a_module_function():
    text = session_for("statistics.median").help()
    assert text.startswith("statistics.median(data)\n\n")
    assert "Return the median" in text


def test_help_for_a_method():
    text = session_for("str.join").help()
    assert text.startswith("str.join(")
    assert "Concatenate" in text


def test_help_for_a_class_in_a_module():
    text = session_for("itertools.chain").help()
    assert text.startswith("itertools.chain(*iterables)\n\n")
    assert "until it is exhausted" in text


def test_help_through_handle_and_the_cli_title():
    s = session_for("statistics.median")
    resp = s.handle(":help")
    assert resp.kind == "help" and "Return the median" in resp.text
    assert "Documentation: statistics.median" in plain(style.response(resp, s))


def test_help_for_a_not_allowed_or_unknown_dotted_target(monkeypatch):
    # os is only allowed with an OS sandbox; without one (Windows) it is refused.
    monkeypatch.setenv("PYGYM_SANDBOX", "audit-hook")
    assert session_for("os.listdir").help() == "No documentation available for 'os.listdir'."
    assert session_for("statistics.nope").help() == "No documentation available for 'statistics.nope'."


def test_help_for_builtins_is_unchanged():
    s = Session("zip", 1, mode="preset")
    assert s.help() == zip.__doc__.strip()


# ----- :s when no other exercise exists ---------------------------------------------------------

def test_skip_without_any_other_exercise_keeps_the_current_one(monkeypatch):
    """A function with no presets: the first generation works, the next one fails."""
    results = [ai_copy(), ai.GenerationUnavailable("no valid exercise after 4 tries (crash)")]

    def generate(target, difficulty, **kwargs):
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(ai, "generate_exercise", generate)
    s = Session("bin", 1, mode="preset")
    first = s.exercise
    assert s.notice == ai.WARNING
    resp = s.handle(":s")
    assert resp.kind == "refused" and not resp.clear_code
    assert "No other exercise" in resp.text
    assert s.exercise is first and s.notice == ai.WARNING
