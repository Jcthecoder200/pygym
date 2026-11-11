"""The progress of AI generation (engine events, Session jobs, every front-end) and the level
system (automatic level-up, :level / :lv). No LLM, no network: fake providers and generators."""
import dataclasses
import io
import re
import threading
import time
import urllib.request

import pytest
from rich.console import Console

from pygym import ai, editor, jupyter, loader, style, ui
from pygym.ai import engine
from pygym.ai.providers import FakeProvider, ProviderError
from pygym.session import (BUSY, CANCELLED, COMMANDS, LEVEL_UP_AFTER, SHORT, Job, Session,
                           is_command_line, parse_level)

RIGHT = "def combine(keys, values):\n    return dict(zip(keys, values))"
WRONG = "def combine(keys, values):\n    return {}"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)  # the generators below are all fakes
    monkeypatch.setattr(engine, "_status_cache", {})

    def no_network(*a, **k):
        raise AssertionError("unit tests must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)


def preset_as_ai(level: int, n: int):
    """A zip preset at `level` dressed up as a generated exercise (so it can really be graded)."""
    base = loader.pick("zip", level)
    return dataclasses.replace(base, id=f"ai-zip-{level}-{n:08x}", source="ai",
                               generator="fake:model")


def event(stage, attempt=1, detail="", elapsed=0.5):
    return {"attempt": attempt, "max_attempts": 4, "elapsed": elapsed, "budget": 20.0,
            "stage": stage, "detail": detail}


class FakeAI:
    """ai.generate_exercise replacement. With slow=True a call that has an on_progress callback
    reports "asking", then waits for `gate`, then reports "checking" and returns."""

    def __init__(self, slow=False, fail=None):
        self.slow, self.fail = slow, fail
        self.gate = threading.Event()
        self.waiting = threading.Event()   # set once a slow call is waiting at the gate
        self.levels, self.callbacks, self.returned = [], [], []

    def __call__(self, target, difficulty, *, avoid_ids=None, settings=None, timeout=20.0,
                 on_progress=None):
        self.levels.append(difficulty)
        self.callbacks.append(on_progress)
        if on_progress is not None and self.slow:
            on_progress(event("asking"))
            self.waiting.set()
            assert self.gate.wait(5), "the test never opened the gate"
            on_progress(event("checking", elapsed=1.0))
        if self.fail is not None and len(self.levels) > 1:
            raise self.fail
        ex = preset_as_ai(difficulty, len(self.levels))
        self.returned.append(ex.id)
        return ex


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(ai, "generate_exercise", fake)
    return fake


@pytest.fixture
def slow_ai(monkeypatch):
    fake = FakeAI(slow=True)
    monkeypatch.setattr(ai, "generate_exercise", fake)
    yield fake
    fake.gate.set()  # never leave a worker thread waiting


# ----- the engine's on_progress events ------------------------------------------------------------

@pytest.fixture
def provider(monkeypatch):
    def install(*replies):
        fake = FakeProvider(replies)
        monkeypatch.setattr(engine, "get_provider", lambda settings: fake)
        return fake
    return install


def _reply(body="return list(zip(names, scores))"):
    import json
    return json.dumps({
        "question_text": "Write pair_up(names, scores) that pairs names and scores with zip.",
        "function_name": "pair_up", "function_signature": "def pair_up(names, scores):",
        "reference_solution": f"def pair_up(names, scores):\n    {body}",
        "tests": [{"names": ["ana", "bo"], "scores": [3, 7]}, {"names": [], "scores": []},
                  {"names": ["x"], "scores": [1, 2]}],
        "hint": "zip pairs items."})


GOOD = _reply()
CRASH = _reply("return list(zip(names, scores))[99]")


def stages(events):
    return [(e["stage"], e["attempt"]) for e in events]


def test_engine_reports_each_step_of_a_good_first_try(provider):
    provider(GOOD)
    events = []
    ai.generate_exercise("zip", 1, on_progress=events.append)
    assert stages(events) == [("asking", 1), ("checking", 1), ("done", 1)]
    for e in events:
        assert set(e) == {"attempt", "max_attempts", "elapsed", "budget", "stage", "detail"}
        assert e["max_attempts"] == engine.MAX_ATTEMPTS and e["budget"] == 20.0
        assert e["elapsed"] >= 0


def test_engine_reports_a_rejected_try_with_a_short_reason(provider):
    provider(CRASH, GOOD)
    events = []
    ai.generate_exercise("zip", 1, timeout=30.0, on_progress=events.append)
    assert stages(events) == [("asking", 1), ("checking", 1), ("rejected", 1),
                              ("asking", 2), ("checking", 2), ("done", 2)]
    assert events[2]["detail"] == "crashed on its tests" and events[0]["budget"] == 30.0
    assert style.progress_text(events[2], 3.2) == "try 1 rejected: crashed on its tests · 3 s"


def test_engine_reports_every_attempt_then_fallback(provider):
    provider("{bad", "{bad", "{bad", "{bad")
    events = []
    with pytest.raises(ai.GenerationUnavailable):
        ai.generate_exercise("zip", 1, on_progress=events.append)
    assert [a for s, a in stages(events) if s == "asking"] == [1, 2, 3, 4]
    assert all(e["detail"] == "not valid JSON" for e in events if e["stage"] == "rejected")
    assert events[-1]["stage"] == "fallback" and events[-1]["detail"]


def test_engine_reports_the_cache_fallback(provider):
    provider(GOOD)
    first = ai.generate_exercise("zip", 2)
    provider(ProviderError("Ollama is not running"))
    events = []
    assert ai.generate_exercise("zip", 2, on_progress=events.append).id == first.id
    assert stages(events)[0] == ("asking", 1)
    assert events[-1]["stage"] == "fallback"
    assert events[-1]["detail"] == "using an earlier saved exercise"


def test_engine_ignores_a_broken_progress_callback(provider):
    provider(GOOD)

    def broken(e):
        raise ValueError("display crashed")

    assert ai.generate_exercise("zip", 1, on_progress=broken).function_name == "pair_up"


def test_engine_without_a_callback_is_unchanged(provider):
    fake = provider(GOOD)
    assert ai.generate_exercise("zip", 1).function_name == "pair_up" and len(fake.calls) == 1


@pytest.mark.parametrize("problem, short", [
    ("the reply was not valid JSON", "not valid JSON"),
    ("the reference solution crashed on the tests: IndexError", "crashed on its tests"),
    ("the reference solution ran too long (infinite loop?)", "ran too long"),
    ("reference_solution must call zip(...)", "did not use the function"),
    ("it repeated an earlier exercise", "repeated an earlier exercise"),
    ("something new", "not usable"),
])
def test_short_reasons(problem, short):
    assert engine.short_reason(problem) == short


# ----- progress text and bar (shared by every front-end) --------------------------------------------

def test_progress_text_for_every_stage():
    assert style.progress_text(event("asking", 2), 7.4) == "writing exercise · try 2 of 4 · 7 s"
    assert style.progress_text(event("checking", 3), 9.9) == "checking exercise · try 3 of 4 · 9 s"
    assert style.progress_text(event("done")) == "exercise ready"
    assert (style.progress_text(event("fallback", detail="using a hand-written exercise instead"))
            == "using a hand-written exercise instead")
    assert style.progress_text({"stage": "starting"}, 0.2) == "asking the model · 0 s"


def test_progress_fraction_fills_against_the_budget():
    assert style.progress_fraction(event("asking"), 5.0) == pytest.approx(0.25)
    assert style.progress_fraction(event("asking"), 40.0) == 0.99   # never "full" while waiting
    assert style.progress_fraction(event("done"), 1.0) == 1.0


@pytest.mark.parametrize("width", [5, 20, 40, 60, 79, 120])
def test_progress_line_fits_the_width(width):
    row = style.progress_line(event("asking", 2), 7.0, width)
    assert row.cell_len <= width
    if width >= 79:
        assert "writing exercise · try 2 of 4 · 7 s" in row.plain and "Ctrl-C cancels" in row.plain
        assert style.BAR_FULL in row.plain and style.BAR_EMPTY in row.plain


def test_progress_line_spinner_moves():
    frames = {style.progress_line(event("asking"), t / 10, 80).plain[0] for t in range(10)}
    assert len(frames) == 10


# ----- Session jobs ---------------------------------------------------------------------------------

def test_preset_commands_are_done_at_once():
    s = Session("zip", 1, mode="preset")
    job = s.start(":s")
    assert job.done and not job.background
    assert job.result().kind == "exercise"
    assert s.start(":h").done and s.needs_wait(":s") is False


def test_background_skip_leaves_the_session_alone_until_result(slow_ai):
    s = Session("zip", 1, mode="ai")
    first = s.exercise
    s.handle(":w", WRONG)
    saved, result = s.saved_code, s.last_result
    assert s.needs_wait(":s") and s.needs_wait(":lv 2") and not s.needs_wait(":lv 1")
    job = s.start(":s", WRONG)
    assert slow_ai.waiting.wait(5)
    assert not job.done and job.background and s.busy
    assert job.progress["stage"] == "asking" and job.progress["attempt"] == 1
    # nothing changed while the worker runs
    assert s.exercise is first and s.saved_code == saved and s.last_result is result
    assert s.handle(":s").text == BUSY and s.handle(":w", RIGHT).text == BUSY
    slow_ai.gate.set()
    assert job.wait(5) and job.done
    assert s.exercise is first          # still: only result() applies the outcome
    resp = job.result()
    assert resp.kind == "exercise" and resp.clear_code and resp.text.startswith("Skipped.")
    assert s.exercise.id == slow_ai.returned[-1] and s.saved_code is None and not s.busy
    assert job.result() is resp         # applied once


def test_cancel_keeps_the_exercise_and_stops_the_worker(slow_ai):
    s = Session("zip", 1, mode="ai")
    first = s.exercise
    job = s.start(":s")
    assert slow_ai.waiting.wait(5)
    assert job.cancel() and job.done and job.cancelled and not s.busy
    resp = job.result()
    assert resp.text == CANCELLED and resp.kind == "refused" and not resp.clear_code
    slow_ai.gate.set()                  # the worker's next progress report stops it
    time.sleep(0.2)
    assert len(slow_ai.returned) == 1   # only the first exercise was ever returned
    assert s.exercise is first and s.handle(":h").kind == "hint"   # usable again at once


def test_cancel_after_result_is_too_late(fake_ai):
    s = Session("zip", 1, mode="ai")
    job = s.start(":s")
    job.wait(5)
    resp = job.result()
    assert job.cancel() is False and job.result() is resp


def test_level_change_is_applied_only_when_the_job_finishes(slow_ai):
    s = Session("zip", 1, mode="ai")
    s.handle(":w", s.exercise.reference_solution)
    job = s.start(":level 2")
    assert slow_ai.waiting.wait(5)
    assert s.level == 1 and s.solved_at_level == 1
    slow_ai.gate.set()
    job.wait(5)
    assert job.result().text.startswith("Level: intermediate ●●○")
    assert s.level == 2 and s.solved_at_level == 0 and slow_ai.levels[-1] == 2


def test_cancelled_level_change_keeps_the_level(slow_ai):
    s = Session("zip", 1, mode="ai")
    job = s.start(":lv 3")
    assert slow_ai.waiting.wait(5)
    job.cancel()
    assert job.result().text == CANCELLED and s.level == 1


def test_generator_errors_become_the_preset_fallback(monkeypatch):
    fake = FakeAI(fail=RuntimeError("model exploded"))
    monkeypatch.setattr(ai, "generate_exercise", fake)
    s = Session("zip", 1, mode="ai")
    assert s.exercise.source == "ai"
    job = s.start(":s")
    job.wait(5)
    resp = job.result()
    assert resp.kind == "exercise" and s.exercise.source == "preset"
    assert s.notice == "AI mode unavailable (RuntimeError: model exploded); using the preset exercises."
    assert job.progress["stage"] == "fallback"
    assert job.progress["detail"] == "using a hand-written exercise instead"


def test_errors_without_any_exercise_are_a_refusal(monkeypatch):
    fake = FakeAI(fail=ai.GenerationUnavailable("no valid exercise after 4 tries"))
    monkeypatch.setattr(ai, "generate_exercise", fake)
    s = Session("bin", 1, mode="preset")   # no preset for bin: AI only
    first = s.exercise
    job = s.start(":s")
    job.wait(5)
    resp = job.result()
    assert resp.kind == "refused" and "No other exercise" in resp.text and s.exercise is first


def test_first_exercise_reports_progress_through_the_constructor(monkeypatch):
    seen = []

    def generate(target, difficulty, *, avoid_ids=None, on_progress=None, **kw):
        on_progress(event("asking"))
        return preset_as_ai(difficulty, 1)

    monkeypatch.setattr(ai, "generate_exercise", generate)
    s = Session("zip", 2, mode="ai", on_progress=seen.append)
    assert s.exercise.difficulty == 2 and seen[0]["stage"] == "asking"


def test_generic_job_returns_the_value_or_raises():
    job = Job(lambda report: report({"stage": "asking"}) or 42)
    assert job.wait(5) and job.result() == 42
    bad = Job(lambda report: 1 / 0)
    bad.wait(5)
    with pytest.raises(ZeroDivisionError):
        bad.result()


# ----- level-up ---------------------------------------------------------------------------------

def solve(s):
    return s.handle(":w", s.exercise.reference_solution)


def test_three_solved_exercises_level_up():
    s = Session("zip", 1, mode="preset")
    assert (s.level, s.solved_at_level, LEVEL_UP_AFTER) == (1, 0, 3)
    for n in (1, 2):
        assert "Level up" not in solve(s).text and s.solved_at_level == n
        s.handle(":s")
    resp = solve(s)
    assert resp.kind == "pass"
    assert "Level up! Next exercise: intermediate ●●○" in resp.text
    assert len("✓ " + resp.text) <= 79  # fits the editor's message line
    assert (s.level, s.solved_at_level) == (2, 0)
    s.handle(":s")
    assert s.exercise.difficulty == 2  # the loader picks at the new level


def test_a_second_pass_of_the_same_exercise_counts_once():
    s = Session("zip", 1, mode="preset")
    solve(s)
    solve(s)
    s.handle(":wq", s.exercise.reference_solution)
    assert s.solved_at_level == 1


def test_solving_after_the_solution_was_shown_does_not_count():
    s = Session("zip", 1, mode="preset")
    s.handle(":solution")
    assert solve(s).kind == "pass" and s.solved_at_level == 0
    s.handle(":s")
    s.solution()  # the reveal prompt (y) after a failed :wq calls solution() directly
    solve(s)
    assert s.solved_at_level == 0


def test_skipping_does_not_reset_the_count():
    s = Session("zip", 1, mode="preset")
    solve(s)
    s.handle(":s")
    s.handle(":s")
    s.handle(":w", WRONG)
    assert s.solved_at_level == 1 and s.level == 1


def test_level_never_goes_down_by_itself_and_stops_at_3():
    s = Session("zip", 3, mode="preset")
    for _ in range(4):
        s.handle(":w", WRONG)
        assert "Level up" not in solve(s).text
        s.handle(":s")
    assert s.level == 3 and s.solved_at_level == 4
    s = Session("zip", 2, mode="preset")
    for _ in range(5):
        s.handle(":wq", WRONG)
    assert s.level == 2


def test_wq_level_up_says_so_and_quits():
    s = Session("zip", 1, mode="preset")
    s.solved_at_level = 2
    resp = s.handle(":wq", s.exercise.reference_solution)
    assert resp.quit and "Level up! Next exercise: intermediate ●●○" in resp.text


def test_level_up_in_ai_mode_asks_for_the_next_level(fake_ai):
    s = Session("zip", 1, mode="ai")
    for _ in range(3):
        solve(s)
        s.handle(":s")
    assert s.level == 2 and fake_ai.levels == [1, 1, 1, 2]
    assert s.exercise.difficulty == 2


def test_manual_change_resets_the_count():
    s = Session("zip", 1, mode="preset")
    solve(s)
    s.handle(":level 2")
    assert (s.level, s.solved_at_level) == (2, 0)
    solve(s)
    s.handle(":lv 1")
    assert (s.level, s.solved_at_level) == (1, 0)


def test_difficulty_none_starts_at_beginner():
    s = Session("zip", None, mode="preset")
    assert s.level == 1 and s.difficulty == 1 and s.exercise.difficulty == 1


# ----- :level / :lv in Session ----------------------------------------------------------------

@pytest.mark.parametrize("command, level", [
    (":level 2", 2), (":level intermediate", 2), (":lv i", 2), (":lv b", 1), (":lv a", 3),
    (":level 3", 3), (":level Advanced", 3), (":lv adv", 3), ("  :level   2  ", 2)])
def test_level_forms(command, level):
    s = Session("zip", 1 if level != 1 else 3, mode="preset")
    resp = s.handle(command)
    assert resp.kind == "exercise" and resp.clear_code
    assert s.level == level and s.exercise.difficulty == level
    assert resp.text.startswith(f"Level: {('', 'beginner', 'intermediate', 'advanced')[level]}")


def test_level_alone_shows_the_level_and_the_count():
    s = Session("zip", 1, mode="preset")
    solve(s)
    solve(s)
    s.handle(":s")
    solve(s)
    resp = s.handle(":level")
    assert resp.kind == "level" and not resp.clear_code
    assert resp.text == ("Level: beginner ●○○ — 2 of 3 solved to level up. :level 1-3 to change")
    assert s.handle(":lv").text == resp.text
    s.handle(":lv 3")
    assert s.handle(":level").text == "Level: advanced ●●● (the highest). :level 1-3 to change"


def test_same_level_just_says_so():
    s = Session("zip", 2, mode="preset")
    first = s.exercise
    s.solved_at_level = 1
    resp = s.handle(":lv i")
    assert resp.kind == "level" and resp.text == "You are already at intermediate ●●○."
    assert s.exercise is first and s.solved_at_level == 1


@pytest.mark.parametrize("arg", ["9", "0", "x", "expert", "2.5"])
def test_invalid_level_is_friendly(arg):
    s = Session("zip", 1, mode="preset")
    resp = s.handle(f":level {arg}")
    assert resp.kind == "refused" and f"'{arg}' is not a level" in resp.text
    assert "1, 2 or 3" in resp.text and "b, i, a" in resp.text and s.level == 1


def test_only_level_takes_an_argument():
    s = Session("zip", 1, mode="preset")
    assert s.handle(":s 2").kind == "unknown"
    assert s.handle(":w 3", RIGHT).kind == "unknown"
    assert s.handle(":level 2 3").kind == "unknown"


def test_level_is_listed_everywhere():
    s = Session("zip", 1, mode="preset")
    assert ":level" in dict(COMMANDS) and ":level" in SHORT
    assert ":level" in s.handle(":intro").text and ":lv" in s.handle(":intro").text
    assert len(s.handle(":nope").text) <= 80  # the "Try:" list still fits a message line


def test_parse_level_is_shared_with_the_menu():
    from pygym import menu
    assert menu.parse_level is parse_level and menu.LEVELS[2] == "intermediate"


# ----- per-line command recognition -------------------------------------------------------------

@pytest.mark.parametrize("line", [":w", ":q!", ":xyz", ":level", ":level 2", ":lv b",
                                  "  :level intermediate  ", ":level\t3"])
def test_command_lines(line):
    assert is_command_line(line)


@pytest.mark.parametrize("line", [":level 2 3", "x = :level", ":w 3", ":s 2", "    : 1}",
                                  ":level:2", "level 2", ""])
def test_not_command_lines(line):
    assert not is_command_line(line)


# ----- line CLI -----------------------------------------------------------------------------------

def drive_text(session, lines, console=None):
    it = iter(lines)
    out = []

    def fake_input(prompt):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    ui.run_text(session, input_fn=fake_input, print_fn=lambda *a: out.append(" ".join(map(str, a))),
                console=console)
    return "\n".join(out)


def test_cli_level_commands_and_isolation():
    s = Session("zip", 1, mode="preset")
    out = drive_text(s, [":level", ":level 2", "def combine(keys, values):", ":level 2 3",
                         "    return dict(zip(keys, values))", ":w", ":lv x", ":q!"])
    assert "Level: beginner ●○○ — 0 of 3 solved to level up." in out
    assert "Level: intermediate ●●○. Here is a new exercise." in out
    assert re.search(r"zip  ●●○  level 2", out)
    # ":level 2 3" is not a command: it became code, so the check has a syntax error
    assert "Unknown command" not in out and "Failed:" in out
    assert "'x' is not a level" in out and s.level == 2


def test_cli_shows_the_level_under_a_passed_check():
    out = drive_text(Session("zip", 1, mode="preset"), [RIGHT.split("\n")[0], RIGHT.split("\n")[1],
                                                        ":w", ":q"])
    assert "Level: beginner ●○○ 1/3 solved to level up" in out


def test_cli_shows_a_progress_bar_while_the_ai_writes(monkeypatch):
    fake = FakeAI(slow=True)
    monkeypatch.setattr(ai, "generate_exercise", fake)
    threading.Timer(0.6, fake.gate.set).start()
    console = Console(file=io.StringIO(), record=True, force_terminal=True, width=100,
                      color_system=None)
    out = drive_text(Session("zip", 1, mode="ai"), [":s", ":q!"], console=console)
    bar = console.export_text()
    assert "writing exercise · try 1 of 4" in bar and "Ctrl-C cancels" in bar
    assert "━" in bar or "─" in bar
    assert "Skipped. Here is another one." in out and fake.returned[-1] in out


def test_cli_ctrl_c_cancels_the_generation(monkeypatch):
    fake = FakeAI(slow=True)
    monkeypatch.setattr(ai, "generate_exercise", fake)

    def interrupt(job, console):
        assert fake.waiting.wait(5)
        job.cancel()            # what wait_with_bar does on KeyboardInterrupt
        return False

    monkeypatch.setattr(style, "wait_with_bar", interrupt)
    s = Session("zip", 1, mode="ai")
    first = s.exercise.id
    out = drive_text(s, [":s", ":q!"], console=Console(file=io.StringIO()))
    fake.gate.set()
    assert "Cancelled. You are still on this exercise." in out and s.exercise.id == first


def test_wait_with_bar_turns_ctrl_c_into_cancel(monkeypatch):
    release = threading.Event()
    job = Job(lambda report: release.wait(5))

    def wait(timeout=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(job, "wait", wait)
    try:
        assert style.wait_with_bar(job, Console(file=io.StringIO())) is False and job.cancelled
    finally:
        release.set()  # no thread left behind (the pseudo-terminal tests fork later)


# ----- editor -------------------------------------------------------------------------------------

def rows_of(frame):
    parts = re.split(r"\x1b\[(\d+);1H", frame)
    rows = {}
    for n, text in zip(parts[1::2], parts[2::2]):
        rows[int(n)] = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    return [rows[k] for k in sorted(rows)]


def command(ed, text):
    for key in [":", *text, "ENTER"]:
        ed.feed(key)


def test_editor_shows_progress_and_ignores_keys_while_writing(slow_ai):
    ed = editor.Editor(Session("zip", 1, mode="ai"), 80, 24, background=True)
    first, text = ed.session.exercise.id, ed.buffer.text
    command(ed, "s")
    assert slow_ai.waiting.wait(5) and ed.job is not None
    rows = rows_of(editor.render(ed, 80, 24))
    assert re.match(r"^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] writing exercise · try 1 of 4 · \d+ s  [━─]+  Ctrl-C cancels$",
                    rows[-1]), rows[-1]
    assert len(rows[-1]) <= 79
    for key in ["i", "x", "ENTER", "ESC", ":", "q", "!", "ENTER", "d", "d"]:
        ed.feed(key)                 # ignored: the buffer must not change, :q! must not run
    assert ed.buffer.text == text and ed.mode == editor.NORMAL and not ed.done
    assert ed.tick() is True and ed.job is not None   # still waiting: redraw the bar
    slow_ai.gate.set()
    ed.job.wait(5)
    assert ed.tick() is True and ed.job is None       # finished: applied now
    assert ed.session.exercise.id != first and ed.session.exercise.id == slow_ai.returned[-1]
    assert ed.buffer.text == ed.session.exercise.starter_code + "\n    "
    assert ed.message == ai.WARNING and ed.tick() is False
    assert not rows_of(editor.render(ed, 80, 24))[-1].startswith("⠋")


def test_editor_ctrl_c_cancels_and_keeps_the_exercise(slow_ai):
    ed = editor.Editor(Session("zip", 1, mode="ai"), 80, 24, background=True)
    ed.feed("i")
    for ch in "return 1":
        ed.feed(ch)
    ed.feed("ESC")
    first, text = ed.session.exercise.id, ed.buffer.text
    command(ed, "lv 3")
    assert slow_ai.waiting.wait(5)
    ed.feed("CTRL_C")
    assert ed.job is None and ed.message == CANCELLED
    assert ed.session.exercise.id == first and ed.buffer.text == text and ed.session.level == 1
    slow_ai.gate.set()


def test_editor_without_background_blocks_like_before(fake_ai):
    ed = editor.Editor(Session("zip", 1, mode="ai"), 80, 24)
    command(ed, "s")
    assert ed.job is None and ed.session.exercise.id == fake_ai.returned[-1]


def test_editor_preset_skip_shows_no_progress():
    ed = editor.Editor(Session("zip", 1, mode="preset"), 80, 24, background=True)
    command(ed, "s")
    assert ed.job is None and ed.message.startswith("Skipped")


def test_editor_status_line_shows_the_level():
    ed = editor.Editor(Session("zip", 1, mode="preset"), 80, 24)
    status = rows_of(editor.render(ed, 80, 24))[-2]
    assert " ●○○ 0/3 " in status and status.rstrip().endswith("2,5") and len(status) == 80
    for _ in range(2):
        command(ed, "s")
        ed.buffer.lines = ed.session.exercise.reference_solution.split("\n")
        command(ed, "w")
    assert " ●○○ 2/3 " in rows_of(editor.render(ed, 80, 24))[-2]
    command(ed, "s")
    ed.buffer.lines = ed.session.exercise.reference_solution.split("\n")
    command(ed, "w")
    rows = rows_of(editor.render(ed, 80, 24))
    assert rows[-1] == "✓ Passed! Correct! Level up! Next exercise: intermediate ●●○ (:s)"
    assert " ●●○ 0/3 " in rows[-2]
    command(ed, "level 3")
    assert " ●●● " in rows_of(editor.render(ed, 80, 24))[-2]
    assert "/3" not in rows_of(editor.render(ed, 80, 24))[-2]


def test_editor_level_command_messages():
    ed = editor.Editor(Session("zip", 1, mode="preset"), 80, 24)
    command(ed, "level")
    assert ed.message.startswith("Level: beginner ●○○ — 0 of 3") and ed.mode == editor.NORMAL
    assert ed.message_kind == "level"
    command(ed, "lv i")
    assert ed.session.level == 2 and ed.message.startswith("Level: intermediate ●●○")
    assert ed.buffer.text == ed.session.exercise.starter_code + "\n    "
    command(ed, "level nope")
    assert ed.message.startswith("'nope' is not a level")


def test_editor_status_line_narrow_screen_drops_the_level():
    ed = editor.Editor(Session("zip", 1, mode="preset"), 24, 10)
    status = rows_of(editor.render(ed, 24, 10))[-2]
    assert len(status) == 24 and "0/3" not in status


# ----- Jupyter ------------------------------------------------------------------------------------

class FakeIPython:
    def register_magic_function(self, fn, magic_kind, magic_name):
        self.magic = fn


def test_jupyter_level_and_progress_lines(monkeypatch, capsys):
    fake = FakeAI(slow=True)
    monkeypatch.setattr(ai, "generate_exercise", fake)
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setattr(jupyter, "_active", None)
    jupyter.start(Session("zip", 1, mode="ai"), FakeIPython())
    assert "%pygym :level" in capsys.readouterr().out
    jupyter.pygym_magic(":level")
    assert "Level: beginner ●○○ — 0 of 3 solved to level up." in capsys.readouterr().out
    threading.Timer(0.5, fake.gate.set).start()
    jupyter.pygym_magic(":level 2")
    out = capsys.readouterr().out
    assert "… writing exercise · try 1 of 4" in out
    assert "Level: intermediate ●●○. Here is a new exercise." in out and "level 2" in out
    jupyter.pygym_magic(":level 2 3")
    assert "Unknown command ':level 2 3'" in capsys.readouterr().out
