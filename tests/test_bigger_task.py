"""The bigger-task question: a function too small for an advanced exercise on its own (bin) is
not moved to advanced without asking; yes -> advanced exercises are bigger tasks, no ->
intermediate. Session first, then every front-end (line CLI, editor, Jupyter, menu).
No LLM: a fake generator writes graded bin exercises."""
import re
import threading
import urllib.request

import pytest
from rich.console import Console

from pygym import ai, editor, jupyter, menu
from pygym.ai import engine
from pygym.models import Exercise
from pygym.session import (BIGGER_PROMPT, CANCELLED, LEVEL_UP_AFTER, NOT_ANSWERED, Session,
                           bigger_answer, is_command_line)

RIGHT = "def to_bits(n):\n    return bin(n)[2:]"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)  # the generator below is a fake
    monkeypatch.setattr(engine, "_status_cache", {})

    def no_network(*a, **k):
        raise AssertionError("unit tests must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)


def bin_exercise(level: int, n: int, target: str = "bin") -> Exercise:
    """A small exercise about `target` that really grades (RIGHT passes it)."""
    return Exercise(id=f"ai-{target}-{level}-{n:08x}", target_function=target, difficulty=level,
                    question_text="Write to_bits(n): n in binary without '0b'. Use bin.",
                    function_name="to_bits", function_signature="def to_bits(n):",
                    reference_solution=RIGHT, test_inputs=[[1], [2], [5]],
                    expected_outputs=["1", "10", "101"], hint="bin(n)", source="ai",
                    generator="fake:model")


class FakeAI:
    """ai.generate_exercise replacement; records (level, bigger) of every call. slow=True: a
    call with on_progress waits for `gate`."""

    def __init__(self, slow=False):
        self.calls, self.slow = [], slow
        self.gate, self.waiting = threading.Event(), threading.Event()
        self.lock = threading.Lock()

    def __call__(self, target, difficulty, *, avoid_ids=None, settings=None, timeout=20.0,
                 on_progress=None, bigger=False):
        with self.lock:
            self.calls.append((difficulty, bigger))
            n = len(self.calls)
        if on_progress is not None and self.slow:
            on_progress({"stage": "asking", "attempt": 1})
            self.waiting.set()
            assert self.gate.wait(5), "the test never opened the gate"
            on_progress({"stage": "checking", "attempt": 1})
        return bin_exercise(difficulty, n, target)


@pytest.fixture
def fake(monkeypatch):
    f = FakeAI()
    monkeypatch.setattr(ai, "generate_exercise", f)
    return f


@pytest.fixture
def slow(monkeypatch):
    f = FakeAI(slow=True)
    monkeypatch.setattr(ai, "generate_exercise", f)
    yield f
    f.gate.set()


def solve(s, times):
    """Pass `times` exercises (:w with the right code, then :s); the last response."""
    resp = None
    for i in range(times):
        resp = s.handle(":w", RIGHT)
        assert resp.kind == "pass", resp.text
        if i < times - 1:
            s.handle(":s")
    return resp


# ----- Session ------------------------------------------------------------------------------------

def test_bigger_answer_and_command_line():
    assert bigger_answer("") == bigger_answer(" Y ") == bigger_answer("yes") == ":bigger yes"
    assert bigger_answer("n") == bigger_answer("NO") == ":bigger no"
    assert bigger_answer(None) is None and bigger_answer("maybe") is None
    assert is_command_line(":bigger yes") and is_command_line(":bigger no")
    assert not is_command_line(":bigger yes please")


def test_level_3_asks_first_and_writes_nothing(fake):
    s = Session("bin", 1, mode="ai")
    assert s.is_simple and s.bigger_applies() and s.question() is None
    assert not s.needs_wait(":level 3")
    r = s.handle(":level 3")
    assert r.ask_bigger and r.kind == "level" and not r.clear_code
    assert r.text == "`bin` is too small for an advanced exercise on its own."
    assert s.bigger_prompt() == BIGGER_PROMPT.format(name="bin")
    assert s.bigger_prompt() == ("Practise it inside a bigger task (bin + loops, conditions and "
                                 "data structures)? [Y/n] ")
    assert s.level == 1 and s.asking_bigger and fake.calls == [(1, False)]


def test_yes_writes_an_advanced_bigger_task(fake):
    s = Session("bin", 1, mode="ai")
    s.handle(":level 3")
    assert s.needs_wait(":bigger yes")
    r = s.handle(":bigger yes")
    assert r.kind == "exercise" and r.clear_code and not r.ask_bigger
    assert r.text == "Level: advanced ●●● (bin inside bigger tasks). Here is a new exercise."
    assert s.level == 3 and s.bigger_task is True and not s.asking_bigger
    assert fake.calls[-1] == (3, True) and s.exercise.difficulty == 3
    # remembered: :s writes the next bigger task, nothing is asked again
    r = s.handle(":s")
    assert not r.ask_bigger and fake.calls[-1] == (3, True)
    assert "bin inside bigger tasks" in s.level_text()


def test_no_from_beginner_loads_intermediate(fake):
    s = Session("bin", 1, mode="ai")
    s.handle(":level 3")
    assert s.needs_wait(":bigger no")
    r = s.handle(":bigger no")
    assert r.kind == "exercise" and r.clear_code
    assert r.text == ("Intermediate ●●○ (`bin` alone is too small for advanced). "
                      "Here is a new exercise.")
    assert s.level == 2 and s.bigger_task is False and fake.calls[-1] == (2, False)


def test_no_from_intermediate_stays_and_is_remembered(fake):
    s = Session("bin", 2, mode="ai")
    s.handle(":lv a")
    calls = len(fake.calls)
    assert not s.needs_wait(":bigger no")
    r = s.handle(":bigger no")
    assert r.kind == "level" and not r.clear_code
    assert r.text == "Staying at intermediate ●●○: `bin` alone is too small for advanced."
    assert s.level == 2 and len(fake.calls) == calls
    # asked again? no: the answer is remembered
    r = s.handle(":level 3")
    assert not r.ask_bigger and "Staying at intermediate" in r.text and "(:bigger yes to change)" in r.text
    assert not s.handle(":s").ask_bigger and fake.calls[-1] == (2, False)
    assert "the highest for `bin`; :bigger yes for bigger tasks" in s.level_text()
    # ...and after a "no", :level 3 from beginner loads intermediate
    s.handle(":level 1")
    assert s.needs_wait(":level 3")
    r = s.handle(":level 3")
    assert r.kind == "exercise" and s.level == 2 and fake.calls[-1] == (2, False)


def test_changing_the_answer_later(fake):
    s = Session("bin", 2, mode="ai")
    s.handle(":level 3")
    s.handle(":bigger no")
    r = s.handle(":bigger yes")        # changed their mind: an advanced bigger task now
    assert r.kind == "exercise" and s.level == 3 and fake.calls[-1] == (3, True)
    r = s.handle(":bigger yes")
    assert r.kind == "level" and r.text == "Advanced `bin` exercises are bigger tasks."
    r = s.handle(":bigger no")         # and back: the next exercise is intermediate
    assert r.kind == "level" and s.level == 2 and ":s for the next exercise" in r.text
    s.handle(":s")
    assert fake.calls[-1] == (2, False)


def test_bad_or_needless_answers_are_refused(fake):
    s = Session("bin", 1, mode="ai")
    assert s.handle(":bigger maybe").kind == "refused"
    assert s.handle(":bigger").text == "Answer :bigger yes or :bigger no."
    z = Session("zip", 1, mode="ai")
    assert not z.is_simple
    r = z.handle(":bigger yes")
    assert r.kind == "refused" and "advanced exercises of its own" in r.text


def test_a_target_with_enough_features_is_never_asked(fake):
    s = Session("sorted", 1, mode="ai")
    r = s.handle(":level 3")
    assert not r.ask_bigger and r.kind == "exercise" and fake.calls[-1] == (3, False)
    s3 = Session("zip", 3, mode="ai")
    assert s3.level == 3 and s3.question() is None


def test_presets_are_used_as_they_are(fake):
    s = Session("abs", 1, mode="preset")         # abs is simple but has hand-written exercises
    assert s.is_simple and not s.bigger_applies()
    r = s.handle(":level 3")
    assert not r.ask_bigger and s.level == 3 and s.exercise.source == "preset"
    assert fake.calls == []


def test_preset_mode_without_presets_asks_too(fake):
    s = Session("bin", 3, mode="preset")         # no hand-written bin: the AI writes it
    assert s.level == 2 and s.question() is not None and fake.calls == [(2, False)]


def test_no_ai_means_no_question(monkeypatch, fake):
    monkeypatch.setenv("PYGYM_NO_AI", "1")
    s = Session("abs", 3, mode="ai")             # falls back to presets: nothing to ask
    assert s.level == 3 and s.question() is None and not s.bigger_applies()


def test_session_started_at_advanced(fake):
    s = Session("bin", 3, mode="ai")
    assert s.level == 2 and fake.calls == [(2, False)]       # never an advanced one-liner
    q = s.question()
    assert q.ask_bigger and q.kind == "level" and "too small" in q.text and s.asking_bigger
    r = s.handle(":bigger yes")
    assert r.kind == "exercise" and s.level == 3 and fake.calls[-1] == (3, True)
    assert s.question() is None

    t = Session("bin", 3, mode="ai")
    r = t.handle(":bigger no")
    assert r.kind == "level" and t.level == 2 and r.text.startswith("Staying at intermediate")


@pytest.mark.parametrize("answer, level, call", [(True, 3, (3, True)), (False, 2, (2, False))])
def test_session_with_the_answer_given(fake, answer, level, call):
    s = Session("bin", 3, mode="ai", bigger=answer)          # the menu asked already
    assert s.level == level and fake.calls == [call] and s.question() is None


def test_level_up_to_advanced_asks(fake):
    s = Session("bin", 2, mode="ai")
    r = solve(s, LEVEL_UP_AFTER)
    assert r.kind == "pass" and r.ask_bigger
    assert r.text.endswith("`bin` alone is too small for advanced.") and "Level up" not in r.text
    assert s.level == 2 and s.asking_bigger and all(lv == 2 for lv, _ in fake.calls)
    assert not s.needs_wait(":bigger yes")       # it only changes the level
    r = s.handle(":bigger yes")
    assert r.kind == "level" and not r.clear_code
    assert r.text == "Level up! advanced ●●● (bin inside bigger tasks). :s for the next exercise."
    assert s.level == 3 and s.solved_at_level == 0
    s.handle(":s")
    assert fake.calls[-1] == (3, True)


def test_level_up_after_no_stays_and_does_not_ask(fake):
    s = Session("bin", 2, mode="ai")
    r = solve(s, LEVEL_UP_AFTER)
    s.handle(":bigger no")
    s.handle(":s")
    r = solve(s, LEVEL_UP_AFTER)
    assert not r.ask_bigger and s.level == 2 and "Level up" not in r.text


def test_level_up_with_wq_quits_without_asking(fake):
    s = Session("bin", 2, mode="ai")
    solve(s, LEVEL_UP_AFTER - 1)
    s.handle(":s")
    r = s.handle(":wq", RIGHT)
    assert r.quit and not r.ask_bigger


def test_level_up_from_beginner_to_intermediate_does_not_ask(fake):
    s = Session("bin", 1, mode="ai")
    r = solve(s, LEVEL_UP_AFTER)
    assert not r.ask_bigger and s.level == 2 and "Level up!" in r.text


def test_start_next_asks_like_level(fake):
    s = Session("bin", 1, mode="ai")
    job = s.start_next(3)
    assert job.done and job.result().ask_bigger and s.level == 1


def test_prefetch_waits_for_the_answer(fake):
    s = Session("bin", 2, mode="ai", prefetch=True)
    try:
        assert s._prefetch.join(5) and fake.calls == [(2, False), (2, False)]
        solve(s, LEVEL_UP_AFTER)                   # the question is open now
        assert s.asking_bigger
        if s._prefetch is not None:
            s._prefetch.join(5)
        assert all(lv == 2 for lv, _ in fake.calls)    # nothing advanced written before "yes"
        s.handle(":bigger yes")
        assert s._prefetch.join(5) and fake.calls[-1] == (3, True)
        assert s.next_ready
    finally:
        s.close()


def test_prefetch_at_level_3_start_waits_for_the_answer(fake):
    s = Session("bin", 3, mode="ai", prefetch=True)
    try:
        assert s._prefetch.join(5)
        assert fake.calls == [(2, False), (2, False)]  # intermediate meanwhile, never advanced
        s.handle(":bigger yes")
        assert s._prefetch.join(5) and fake.calls[-1] == (3, True)
    finally:
        s.close()


def test_cancelling_the_bigger_load_keeps_the_exercise(slow):
    s = Session("bin", 1, mode="ai")
    first = s.exercise
    s.handle(":level 3")
    job = s.start(":bigger yes")
    assert slow.waiting.wait(5) and not job.done
    job.cancel()
    assert job.result().text == CANCELLED and s.exercise is first and s.level == 1
    slow.gate.set()
    # the answer is remembered: :level 3 now loads the bigger task without asking
    assert s.bigger_task is True and not s.asking_bigger
    s2 = s.handle(":level 3")
    assert not s2.ask_bigger and s.level == 3 and slow.calls[-1] == (3, True)


def test_no_answer_changes_nothing(fake):
    s = Session("bin", 1, mode="ai")
    s.handle(":level 3")        # the front-end got EOF / Esc: it sends nothing
    assert s.level == 1 and s.bigger_task is None and s.asking_bigger
    assert not s.handle(":s").ask_bigger and fake.calls[-1] == (1, False)
    assert s.handle(":level 3").ask_bigger   # asked again on an explicit :level 3


# ----- the line CLI ----------------------------------------------------------------------------------

class Script:
    def __init__(self, *lines):
        self.lines, self.prompts = list(lines), []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.lines:
            raise EOFError
        line = self.lines.pop(0)
        if isinstance(line, BaseException):
            raise line
        return line


def run_cli(session, *lines):
    from pygym import ui
    out = []
    script = Script(*lines)
    ui.run_text(session, input_fn=script, print_fn=lambda *a: out.append(" ".join(map(str, a))))
    return "\n".join(out), script.prompts


def test_cli_yes(fake):
    s = Session("bin", 1, mode="ai")
    out, prompts = run_cli(s, ":level 3", "y", ":q!")
    assert "`bin` is too small for an advanced exercise on its own." in out
    assert s.bigger_prompt() in prompts
    assert "Level: advanced ●●● (bin inside bigger tasks)" in out
    assert s.level == 3 and fake.calls[-1] == (3, True)


def test_cli_enter_means_yes_and_no_stays(fake):
    s = Session("bin", 2, mode="ai")
    run_cli(s, ":level 3", "", ":q!")
    assert s.level == 3
    t = Session("bin", 2, mode="ai")
    out, _ = run_cli(t, ":level 3", "no", ":q!")
    assert t.level == 2 and "Staying at intermediate ●●○" in out


@pytest.mark.parametrize("answer", [EOFError(), KeyboardInterrupt(), "what?"])
def test_cli_question_not_answered(fake, answer):
    s = Session("bin", 1, mode="ai")
    out, prompts = run_cli(s, ":level 3", answer, ":s", ":q!")
    assert NOT_ANSWERED in out and s.level == 1 and s.asking_bigger
    assert prompts[-1] == ">>> "               # the session went on after the question


def test_cli_asks_at_start(fake):
    s = Session("bin", 3, mode="ai")
    out, prompts = run_cli(s, "y", ":q!")
    assert prompts[0] == s.bigger_prompt() and "too small" in out
    assert s.level == 3 and fake.calls[-1] == (3, True)


def test_cli_level_up_asks(fake):
    s = Session("bin", 2, mode="ai")
    solve(s, LEVEL_UP_AFTER - 1)
    s.handle(":s")
    out, prompts = run_cli(s, RIGHT.splitlines()[0], RIGHT.splitlines()[1], ":w", "n", ":q!")
    assert s.bigger_prompt() in prompts and "Staying at intermediate" in out and s.level == 2


# ----- the editor ---------------------------------------------------------------------------------

def command(ed, text):
    for key in [":", *text, "ENTER"]:
        ed.feed(key)


def rows_of(frame):
    parts = re.split(r"\x1b\[(\d+);1H", frame)
    rows = {}
    for n, text in zip(parts[1::2], parts[2::2]):
        rows[int(n)] = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    return [rows[k] for k in sorted(rows)]


def test_editor_asks_and_yes_loads(fake):
    ed = editor.Editor(Session("bin", 1, mode="ai"), 100, 24)
    command(ed, "level 3")
    assert ed.mode == editor.PROMPT and ed.prompt_kind == "bigger"
    assert ed.message == "`bin` is too small for an advanced exercise on its own."
    rows = rows_of(editor.render(ed, 100, 24))
    assert rows[-1].startswith(ed.session.bigger_prompt().rstrip())
    ed.feed("y")
    assert ed.mode == editor.NORMAL and ed.session.level == 3
    assert fake.calls[-1] == (3, True) and ed.buffer.text.startswith("def to_bits(n):")


def test_editor_enter_is_yes_n_is_no_esc_is_not_answered(fake):
    ed = editor.Editor(Session("bin", 2, mode="ai"), 100, 24)
    command(ed, "level 3")
    ed.feed("ESC")
    assert ed.mode == editor.NORMAL and ed.message == NOT_ANSWERED and ed.session.level == 2
    command(ed, "level 3")
    ed.feed("n")
    assert ed.session.level == 2 and ed.message.startswith("Staying at intermediate")
    ed2 = editor.Editor(Session("bin", 2, mode="ai"), 100, 24)
    command(ed2, "level 3")
    ed2.feed("ENTER")
    assert ed2.session.level == 3


def test_editor_ctrl_c_at_the_question(fake):
    ed = editor.Editor(Session("bin", 1, mode="ai"), 100, 24)
    command(ed, "level 3")
    ed.feed("CTRL_C")
    assert ed.mode == editor.NORMAL and ed.session.level == 1 and ed.session.asking_bigger


def test_editor_short_prompt_on_a_narrow_screen(fake):
    ed = editor.Editor(Session("bin", 1, mode="ai"), 60, 24)
    command(ed, "level 3")
    rows = rows_of(editor.render(ed, 60, 24))
    assert rows[-1].rstrip() == "Practise bin inside a bigger task? [Y/n]"


def test_editor_asks_at_start_and_yes_runs_in_the_background(slow):
    s = Session("bin", 3, mode="ai")
    ed = editor.Editor(s, 100, 24, background=True)
    assert ed.mode == editor.PROMPT and ed.prompt_kind == "bigger"
    ed.feed("y")
    assert ed.job is not None and slow.waiting.wait(5)
    slow.gate.set()
    ed.job.wait(5)
    assert ed.tick() and ed.job is None and s.level == 3 and slow.calls[-1] == (3, True)


def test_editor_level_up_question(fake):
    s = Session("bin", 2, mode="ai")
    solve(s, LEVEL_UP_AFTER - 1)
    s.handle(":s")
    ed = editor.Editor(s, 100, 24)
    ed.buffer.lines = RIGHT.splitlines()
    command(ed, "w")
    assert ed.mode == editor.PROMPT and ed.prompt_kind == "bigger"
    ed.feed("y")
    assert s.level == 3 and ed.message.startswith("Level up! advanced ●●●")


def test_editor_reveal_prompt_unchanged(fake):
    ed = editor.Editor(Session("bin", 1, mode="ai"), 100, 24)
    command(ed, "wq")
    assert ed.mode == editor.PROMPT and ed.prompt_kind == "reveal"
    assert rows_of(editor.render(ed, 100, 24))[-1].startswith("Reveal the solution? [y/N]")


# ----- Jupyter -------------------------------------------------------------------------------------

@pytest.fixture
def notebook(monkeypatch):
    pytest.importorskip("IPython")
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setattr(jupyter, "_active", None)

    class IP:
        def register_magic_function(self, *a, **k):
            pass

    answers = []
    monkeypatch.setattr("builtins.input", lambda prompt="": answers.pop(0) if answers
                        else (_ for _ in ()).throw(EOFError()))
    return IP(), answers


def test_jupyter_asks_and_answers(fake, notebook, capsys):
    ip, answers = notebook
    s = Session("bin", 1, mode="ai")
    jupyter.start(s, ip)
    answers.append("y")
    jupyter.pygym_magic(":level 3")
    out = capsys.readouterr().out
    assert "too small for an advanced exercise" in out and "bin inside bigger tasks" in out
    assert s.level == 3 and fake.calls[-1] == (3, True)


def test_jupyter_asks_at_start_and_eof_is_safe(fake, notebook, capsys):
    ip, answers = notebook
    s = Session("bin", 3, mode="ai")
    jupyter.start(s, ip)                     # input() raises EOFError: not answered
    out = capsys.readouterr().out
    assert "too small" in out and NOT_ANSWERED in out and s.level == 2
    answers.append("n")
    jupyter.pygym_magic(":level 3")
    assert "Staying at intermediate" in capsys.readouterr().out and s.level == 2


# ----- the menu -----------------------------------------------------------------------------------

def make_console():
    import io
    return Console(file=io.StringIO(), width=120, color_system=None, highlight=False)


@pytest.fixture
def started(monkeypatch, fake):
    seen = {}
    monkeypatch.setattr(menu, "_ai_ready", lambda: False)   # no progress bar in tests
    monkeypatch.setattr(menu.ui, "run", lambda session, ui: seen.update(session=session))
    return seen


@pytest.mark.parametrize("answer, level, call", [("y", 3, (3, True)), ("", 3, (3, True)),
                                                 ("n", 2, (2, False))])
def test_menu_asks_before_writing_anything(started, fake, answer, level, call):
    console = make_console()
    prompts = []
    rc = menu.start(menu.Choice("bin", 3, "ai", "text"), console,
                    input_fn=lambda p: prompts.append(p) or answer)
    s = started["session"]
    assert rc == 0 and prompts == [BIGGER_PROMPT.format(name="bin")]
    assert s.level == level and fake.calls == [call] and s.question() is None
    out = console.file.getvalue()
    assert "`bin` is too small for an advanced exercise on its own." in out
    assert ("Staying at intermediate" in out) == (answer == "n")


@pytest.mark.parametrize("answer", [EOFError(), KeyboardInterrupt(), "later"])
def test_menu_not_answered_goes_back(started, fake, answer):
    def ask(prompt):
        if isinstance(answer, BaseException):
            raise answer
        return answer
    console = make_console()
    assert menu.start(menu.Choice("bin", 3, "ai", "text"), console, input_fn=ask) == 1
    assert "session" not in started and fake.calls == []


def test_menu_does_not_ask_when_not_needed(started, fake):
    def ask(prompt):
        raise AssertionError("not asked")
    for choice in (menu.Choice("bin", 2, "ai", "text"), menu.Choice("sorted", 3, "ai", "text"),
                   menu.Choice("abs", 3, "preset", "text")):
        assert not menu.asks_bigger(choice)
        assert menu.start(choice, make_console(), input_fn=ask) == 0
    assert menu.asks_bigger(menu.Choice("bin", 3, "ai", "text"))
    assert menu.asks_bigger(menu.Choice("bin", 3, "preset", "text"))   # no presets for bin


def test_command_line_level_3_asks(started, fake, monkeypatch):
    monkeypatch.setattr(menu.config, "load", lambda: menu.config.Settings())
    prompts = []
    rc = menu.main(["bin", "--level", "3", "--mode", "ai", "--ui", "text"],
                   console=make_console(), input_fn=lambda p: prompts.append(p) or "y")
    assert rc == 0 and prompts == [BIGGER_PROMPT.format(name="bin")]
    assert started["session"].level == 3 and fake.calls == [(3, True)]


def test_menu_flow_asks_after_the_level(started, fake, monkeypatch):
    monkeypatch.setattr(menu, "_save", lambda settings, console: None)
    answers = ["bin", "3", "n", "q"]
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return answers.pop(0)

    assert menu.run_menu(console=make_console(), input_fn=ask) == 0
    assert BIGGER_PROMPT.format(name="bin") in prompts
    assert started["session"].level == 2 and fake.calls == [(2, False)]
