"""Prefetch: while the student works, Session writes the next exercise in the background, so :s
is instant. Real engine and Session, a fake provider (no network) that can be held at a gate."""
import json
import threading
import time
import urllib.request

import pytest

from pygym import ai, config, jupyter, loader, menu, ui
from pygym.ai import engine
from pygym.ai.providers import ProviderError
from pygym.session import BUSY, CANCELLED, LEVEL_UP_AFTER, NO_OTHER, Session

WAIT = 10.0  # seconds a test waits for a background thread before failing


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)
    monkeypatch.setattr(engine, "_status_cache", {})

    def no_network(*a, **k):
        raise AssertionError("unit tests must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)


def exercise_json(n):
    name = f"pair_{n}"
    return json.dumps({
        "question_text": f"Write {name}(names, scores) that pairs names and scores with zip.",
        "function_name": name, "function_signature": f"def {name}(names, scores):",
        "reference_solution": f"def {name}(names, scores):\n    return list(zip(names, scores))",
        "tests": [{"names": ["ana", "bo"], "scores": [3, 7]}, {"names": [], "scores": []},
                  {"names": ["x"], "scores": [1, 2]}],
        "hint": "zip pairs items."})


class Model:
    """A fake provider: every exercise call returns a new valid exercise (pair_1, pair_2...).
    hold() makes the next calls wait at a gate until release(); fail(n) makes the next n calls
    raise ProviderError."""
    name, model = "fake", "fake"

    def __init__(self):
        self.calls = []            # the level asked for, one per call ("intermediate" ...)
        self.gate = threading.Event()
        self.gate.set()
        self.waiting = threading.Event()
        self.failures = 0
        self.lock = threading.Lock()

    def ping(self, timeout=0.5):
        return True, "fake"

    def hold(self):
        self.gate.clear()
        self.waiting.clear()

    def release(self):
        self.gate.set()

    def fail(self, n=1):
        self.failures = n

    def chat(self, messages, schema, *, temperature, max_tokens, seed, timeout):
        with self.lock:
            text = messages[1]["content"]
            self.calls.append(next(w for w in ("beginner", "intermediate", "advanced")
                                   if f"Write one {w} exercise" in text))
            n = len(self.calls)
            failing = self.failures > 0
            self.failures -= failing
        self.waiting.set()
        assert self.gate.wait(WAIT), "the test never released the gate"
        if failing:
            raise ProviderError("Ollama is not running at http://localhost:11434")
        return exercise_json(n)


@pytest.fixture
def model(monkeypatch):
    m = Model()
    monkeypatch.setattr(engine, "get_provider", lambda settings: m)
    return m


def settle(s):
    """Wait until the prefetch (if any) has finished."""
    if s._prefetch is not None:
        assert s._prefetch.join(WAIT)


def background_threads():
    return [t for t in threading.enumerate() if t is not threading.main_thread()]


# ----- when it starts -----------------------------------------------------------------------------

def test_ai_mode_starts_writing_the_next_exercise_at_once(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    assert s.exercise.function_name == "pair_1"
    assert s._prefetch is not None and s._prefetch_level == 1
    settle(s)
    assert model.calls == ["beginner", "beginner"] and s.next_ready
    s.close()


def test_preset_mode_prefetches_for_a_function_without_hand_written_exercises(model):
    target = "statistics.median"
    with pytest.raises(loader.ExerciseUnavailable):
        loader.pick(target)
    model_reply = json.dumps({
        "question_text": "Write middle(xs) that returns the median of xs.",
        "function_name": "middle", "function_signature": "def middle(xs):",
        "reference_solution": "import statistics\ndef middle(xs):\n    return statistics.median(xs)",
        "tests": [[[3, 1, 2]], [[4, 1]], [[7]]], "hint": "h"})
    replies = [model_reply, model_reply.replace("middle", "centre")]
    model.chat = lambda messages, schema, **kw: (model.calls.append(1), replies.pop(0))[1]
    s = Session(target, 1, mode="preset", prefetch=True)
    assert s.exercise.function_name == "middle" and s._prefetch is not None
    settle(s)
    assert len(model.calls) == 2 and s.next_ready
    s.close()


def test_preset_mode_with_hand_written_exercises_never_prefetches(model):
    s = Session("zip", 1, mode="preset", prefetch=True)
    s.handle(":s")
    s.handle(":level 2")
    assert s._prefetch is None and model.calls == [] and not s.next_ready


def test_prefetch_false_and_the_setting_turn_it_off(model, real_prefetch_setting):
    assert config.Settings().prefetch is True
    assert Session("zip", 1, mode="preset").prefetch is True        # the default setting
    config.save(config.Settings(prefetch=False))
    s = Session("zip", 1, mode="ai")
    assert s.prefetch is False and s._prefetch is None
    s.handle(":s")
    assert s._prefetch is None and model.calls == ["beginner", "beginner"]
    t = Session("zip", 1, mode="ai", prefetch=False)
    assert t._prefetch is None


def test_the_prefetched_exercise_is_not_seen_until_shown(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    upcoming, _ = s._prefetch.result()
    assert upcoming.id not in s.seen and s.seen == {s.exercise.id}
    s.handle(":s")
    assert s.exercise.id == upcoming.id and upcoming.id in s.seen
    s.close()


# ----- :s takes it -------------------------------------------------------------------------------

def test_s_takes_a_ready_prefetch_at_once(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    assert s.needs_wait(":s") is False                      # Jupyter: no progress line needed
    before = len(model.calls)
    start = time.monotonic()
    job = s.start(":s")
    assert job.done and time.monotonic() - start < 0.5
    resp = job.result()
    assert resp.kind == "exercise" and resp.clear_code and s.exercise.function_name == "pair_2"
    assert s.notice == ai.WARNING
    assert s._prefetch is not None                          # and the next one is on its way
    settle(s)
    assert len(model.calls) == before + 1
    s.close()


def test_s_while_it_is_still_written_follows_the_same_request(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    s.handle(":s")                                          # pair_2; the prefetch of pair_3 ...
    model.hold()
    s._drop_prefetch()
    s._start_prefetch()                                     # ... is now held at the gate
    assert model.waiting.wait(WAIT)
    calls = len(model.calls)
    assert s.needs_wait(":s") is True
    job = s.start(":s")
    assert not job.done and s.busy
    assert job.progress["stage"] == "asking" and job.elapsed >= 0
    assert s.start(":w").result().text == BUSY
    model.release()
    assert job.wait(WAIT)
    resp = job.result()
    assert resp.kind == "exercise" and s.exercise.function_name == f"pair_{calls}"
    assert not s.busy
    settle(s)
    assert len(model.calls) == calls + 1                    # only the NEXT prefetch was added
    s.close()


def test_cancelling_the_wait_keeps_the_prefetch_for_the_next_s(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    model.hold()
    settle_first = s._prefetch
    assert model.waiting.wait(WAIT)
    job = s.start(":s")
    assert job.cancel() and job.result().text == CANCELLED
    assert s._prefetch is settle_first and not s.busy and s.exercise.function_name == "pair_1"
    model.release()
    resp = s.start(":s")
    assert resp.wait(WAIT) and resp.result().kind == "exercise"
    assert s.exercise.function_name == "pair_2" and len(model.calls) >= 2
    s.close()


def test_handle_s_blocks_on_the_prefetch_instead_of_asking_again(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    model.hold()
    assert model.waiting.wait(WAIT)
    threading.Timer(0.2, model.release).start()
    assert s.handle(":s").kind == "exercise" and s.exercise.function_name == "pair_2"
    settle(s)
    assert len(model.calls) == 3
    s.close()


def test_skip_uses_the_prefetch_too(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    upcoming, _ = s._prefetch.result()
    assert s.skip().id == upcoming.id
    s.close()


# ----- when it fails -----------------------------------------------------------------------------

def test_a_failed_prefetch_gives_the_usual_fallback(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    s._drop_prefetch()
    model.fail(1)
    s._start_prefetch()
    settle(s)
    resp = s.start(":s").result()
    assert resp.kind == "exercise" and s.exercise.source == "preset"
    assert s.notice.startswith("AI mode unavailable (Ollama is not running")
    s.close()


def test_a_failed_prefetch_without_presets_says_so_and_tries_again(model):
    target = "statistics.median"
    reply = json.dumps({
        "question_text": "Write middle(xs) that returns the median of xs.",
        "function_name": "middle", "function_signature": "def middle(xs):",
        "reference_solution": "import statistics\ndef middle(xs):\n    return statistics.median(xs)",
        "tests": [[[3, 1, 2]], [[4, 1]], [[7]]], "hint": "h"})
    answers = [reply, ProviderError("Ollama is not running"),
               reply.replace("middle", "centre")]

    def chat(messages, schema, **kw):
        model.calls.append(1)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    model.chat = chat
    s = Session(target, 1, mode="preset", prefetch=True)
    settle(s)
    resp = s.handle(":s")
    assert resp.kind == "refused" and resp.text == NO_OTHER
    assert s.exercise.function_name == "middle"
    assert s._prefetch is not None                          # tried again in the background
    settle(s)
    assert s.handle(":s").kind == "exercise" and s.exercise.function_name == "centre"
    s.close()


# ----- level changes -----------------------------------------------------------------------------

def test_level_command_discards_the_old_prefetch_and_starts_a_new_one(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    old = s._prefetch
    resp = s.handle(":level 2")
    assert resp.kind == "exercise" and s.level == 2 and s.exercise.difficulty == 2
    assert old.cancelled
    assert s._prefetch is not None and s._prefetch_level == 2
    settle(s)
    assert model.calls == ["beginner", "beginner", "intermediate", "intermediate"]
    s.close()


def test_a_level_up_discards_the_old_prefetch_and_starts_a_new_one(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    old = s._prefetch
    s.solved_at_level = LEVEL_UP_AFTER - 1
    resp = s.handle(":w", s.exercise.reference_solution)
    assert resp.kind == "pass" and "Level up" in resp.text and s.level == 2
    assert old.cancelled and s._prefetch is not old and s._prefetch_level == 2
    settle(s)
    s.handle(":s")
    assert s.exercise.difficulty == 2 and model.calls[-1] == "intermediate"
    s.close()


def test_a_new_prefetch_waits_for_the_thrown_away_request(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    model.hold()
    s._drop_prefetch()
    s._start_prefetch()
    assert model.waiting.wait(WAIT)
    held = s._prefetch
    calls = len(model.calls)
    s.solved_at_level = LEVEL_UP_AFTER - 1
    s.handle(":w", s.exercise.reference_solution)          # level up: held one is thrown away
    assert held.cancelled and s._prefetch is not held
    time.sleep(0.3)
    assert len(model.calls) == calls                        # one request at a time
    model.release()
    settle(s)
    assert len(model.calls) == calls + 1 and model.calls[-1] == "intermediate"
    s.close()


# ----- quitting ----------------------------------------------------------------------------------

@pytest.mark.parametrize("command", [":q", ":q!"])
def test_quit_cancels_the_prefetch(model, command):
    s = Session("zip", 1, mode="ai", prefetch=True)
    model.hold()
    assert model.waiting.wait(WAIT)
    job = s._prefetch
    assert s.handle(command).quit
    assert s.closed and job.cancelled and s._prefetch is None
    assert all(t.daemon for t in background_threads())     # nothing keeps the process alive
    s._start_prefetch()
    assert s._prefetch is None                              # closed: no new one
    model.release()
    assert job.join(WAIT)
    assert len(model.calls) == 2


def test_a_passing_wq_also_cancels_it(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    job = s._prefetch
    assert s.handle(":wq", s.exercise.reference_solution).quit
    assert job.cancelled and s.closed


@pytest.mark.parametrize("end", [EOFError, KeyboardInterrupt])
def test_ui_run_closes_the_session_at_end_of_input_and_on_ctrl_c(model, monkeypatch, end):
    s = Session("zip", 1, mode="ai", prefetch=True)
    job = s._prefetch
    lines = iter([":h"])

    def typed(prompt):
        try:
            return next(lines)
        except StopIteration:
            raise end from None

    original = ui.run_text
    monkeypatch.setattr(ui, "run_text", lambda session: original(
        session, input_fn=typed, print_fn=lambda *a, **k: None))
    ui.run(s, "text")
    assert s.closed and job.cancelled and s._prefetch is None


def test_ui_run_closes_the_session_when_the_window_raises(model, monkeypatch):
    s = Session("zip", 1, mode="ai", prefetch=True)

    def boom(session):
        raise KeyboardInterrupt

    monkeypatch.setattr(ui, "run_text", boom)
    with pytest.raises(KeyboardInterrupt):
        ui.run(s, "text")
    assert s.closed


def test_a_new_jupyter_session_closes_the_old_one(model):
    class IP:
        def register_magic_function(self, *a, **k):
            pass

    first = Session("zip", 1, mode="ai", prefetch=True)
    second = Session("zip", 1, mode="ai", prefetch=True)
    jupyter.start(first, ip=IP())
    jupyter.start(second, ip=IP())
    assert first.closed and not second.closed
    second.close()
    jupyter._active = None


# ----- the student's own checks ------------------------------------------------------------------

def test_w_works_while_a_prefetch_waits_for_the_model(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    model.hold()
    assert model.waiting.wait(WAIT)
    results = []
    worker = threading.Thread(target=lambda: results.append(
        s.handle(":w", s.exercise.reference_solution)), daemon=True)
    worker.start()
    worker.join(WAIT)
    assert results and results[0].kind == "pass"            # not stuck behind the model
    assert results[0].result.passed and not s._prefetch.done
    model.release()
    s.close()


# ----- the line CLI end to end -------------------------------------------------------------------

def test_line_cli_s_after_a_prefetch_shows_no_wait(model):
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    out = []
    lines = iter([":s", ":q!"])
    ui.run_text(s, input_fn=lambda prompt: next(lines), print_fn=out.append)
    text = "\n".join(map(str, out))
    assert "pair_2" in text and "Skipped" in text
    s.close()


def test_editor_s_after_a_prefetch_has_nothing_to_wait_for(model):
    from pygym import editor
    s = Session("zip", 1, mode="ai", prefetch=True)
    settle(s)
    ed = editor.Editor(s, 80, 24, background=True)
    for key in [":", "s", "ENTER"]:
        ed.feed(key)
    assert ed.job is None and s.exercise.function_name == "pair_2"
    assert "pair_2" in ed.buffer.text
    s.close()


def test_status_shows_the_prefetch_setting(monkeypatch):
    from rich.console import Console
    console = Console(record=True, width=120)
    monkeypatch.setattr(menu, "_ai_ready", lambda: False)
    menu.main(["--status"], console=console)
    row = next(line for line in console.export_text().splitlines() if "prefetch" in line)
    assert row.split()[1] == "on"
    config.save(config.Settings(prefetch=False))
    console = Console(record=True, width=120)
    menu.main(["--status"], console=console)
    row = next(line for line in console.export_text().splitlines() if "prefetch" in line)
    assert row.split()[-1] == "off"
