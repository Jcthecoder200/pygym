"""ai.engine: the generate loop with a fake provider (no network), plus Session in both modes."""
import json
import socket
import urllib.request

import pytest

from pygym import ai, config, loader
from pygym.ai import cache, engine
from pygym.ai.prompt import SCHEMA
from pygym.ai.providers import FakeProvider, ProviderError
from pygym.session import Session

REAL_URLOPEN = urllib.request.urlopen


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)
    monkeypatch.setattr(engine, "_status_cache", {})

    def no_network(*a, **k):
        raise AssertionError("unit tests must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)


@pytest.fixture
def use(monkeypatch):
    """use(*replies) -> the FakeProvider the engine will get."""
    def install(*replies, **kw):
        fake = FakeProvider(replies, **kw)
        monkeypatch.setattr(engine, "get_provider", lambda settings: fake)
        return fake
    return install


def reply(name="pair_up", body="return list(zip(names, scores))", **overrides):
    d = {
        "question_text": f"Write {name}(names, scores) that pairs names and scores with zip.",
        "function_name": name,
        "function_signature": f"def {name}(names, scores):",
        "reference_solution": f"def {name}(names, scores):\n    {body}",
        "tests": [{"names": ["ana", "bo"], "scores": [3, 7]}, {"names": [], "scores": []},
                  {"names": ["x"], "scores": [1, 2]}],
        "hint": "zip pairs items.",
    }
    d.update(overrides)
    return json.dumps(d)


GOOD = reply()
OTHER = reply("as_dict", "return dict(zip(names, scores))")
CRASH = reply(body="return list(zip(names, scores))[99]")
LOOP = reply(body="zip\n    while True:\n        pass\n    return 1")
OFF_TARGET = reply(body="return [[n, s] for n, s in map(lambda a, b: (a, b), names, scores)]")
DEAD_CODE = reply(body="if False:\n        zip(names)\n    return [names, scores]")


def user_text(call):
    return "\n".join(m["content"] for m in call["messages"] if m["role"] == "user")


# ----- success -----------------------------------------------------------------------------

def test_success_first_try(use):
    fake = use(GOOD)
    ex = ai.generate_exercise("zip", 2)
    assert ex.source == "ai" and ex.generator == "fake:fake" and ex.difficulty == 2
    assert ex.expected_outputs == [[["ana", 3], ["bo", 7]], [], [["x", 1]]]
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["schema"] == SCHEMA
    assert "`zip`" in user_text(call) and "intermediate" in user_text(call)
    assert call["temperature"] == pytest.approx(0.8) and call["max_tokens"] == 700
    assert call["timeout"] <= engine.DEFAULT_TIMEOUT    # the 60 s default budget


def test_expected_outputs_never_trusted_from_the_model(use):
    use(reply(expected_outputs=[1, 2, 3]))
    assert ai.generate_exercise("zip", 1).expected_outputs[0] == [["ana", 3], ["bo", 7]]


# ----- rejected replies are retried, then GenerationUnavailable --------------------------------

def test_invalid_json_then_fresh_retry(use):
    fake = use("{not json", GOOD)
    assert ai.generate_exercise("zip", 1).function_name == "pair_up"
    assert len(fake.calls) == 2
    second = fake.calls[1]
    assert second["temperature"] > fake.calls[0]["temperature"]   # fresh: hotter, no repair turn
    assert not any(m["role"] == "assistant" for m in second["messages"])


def test_schema_violation_is_sent_back_for_repair(use):
    bad = reply(tests="three tests")
    fake = use(bad, GOOD)
    ai.generate_exercise("zip", 1)
    msgs = fake.calls[1]["messages"]
    assert msgs[-2] == {"role": "assistant", "content": bad}
    assert "rejected" in msgs[-1]["content"] and "tests" in msgs[-1]["content"]


@pytest.mark.parametrize("bad, words", [
    (CRASH, "crashed"),
    (OFF_TARGET, "must call"),
    (DEAD_CODE, "really call"),
    ("[]", "JSON"),
])
def test_bad_replies_end_in_generation_unavailable(use, bad, words):
    fake = use(*[bad] * (engine.MAX_ATTEMPTS + 1))
    with pytest.raises(ai.GenerationUnavailable) as e:
        ai.generate_exercise("zip", 1)
    assert len(fake.calls) == engine.MAX_ATTEMPTS
    assert f"after {engine.MAX_ATTEMPTS} tries" in str(e.value) and words in str(e.value)


def test_looping_reference_is_rejected_then_retried(use, monkeypatch):
    monkeypatch.setattr(engine, "SANDBOX_TIMEOUT", 1.0)
    fake = use(LOOP, GOOD)
    assert ai.generate_exercise("zip", 1).function_name == "pair_up"
    assert "too long" in fake.calls[1]["messages"][-1]["content"]


def test_provider_error_stops_at_once(use):
    fake = use(ProviderError("Ollama is not running at http://localhost:11434"), GOOD)
    with pytest.raises(ai.GenerationUnavailable) as e:
        ai.generate_exercise("zip", 1)
    assert len(fake.calls) == 1 and "not running" in str(e.value)


def test_deadline_is_respected(use, monkeypatch):
    now = [100.0]
    monkeypatch.setattr(engine, "clock", lambda: now[0])

    def slow(messages):
        now[0] += 18.0          # the model took 18 of the 20 seconds
        return "{bad"

    fake = use(slow, GOOD)
    with pytest.raises(ai.GenerationUnavailable):
        ai.generate_exercise("zip", 1, timeout=20.0)
    assert len(fake.calls) == 1   # 2 s left: not worth another call
    assert fake.calls[0]["timeout"] == pytest.approx(20.0)


def test_unknown_target_and_kill_switch(use, monkeypatch):
    monkeypatch.setenv("PYGYM_SANDBOX", "audit-hook")  # os needs an OS sandbox (see test_sandbox)
    fake = use(GOOD)
    with pytest.raises(ai.GenerationUnavailable, match="'os' is not allowed"):
        ai.generate_exercise("os.system", 1)
    monkeypatch.setenv("PYGYM_NO_AI", "1")
    with pytest.raises(ai.GenerationUnavailable, match="PYGYM_NO_AI"):
        ai.generate_exercise("zip", 1)
    assert fake.calls == [] and ai.available() is False


def test_unknown_provider_name(monkeypatch):
    s = config.load()
    s.provider = "nope"
    with pytest.raises(ai.GenerationUnavailable, match="unknown AI provider"):
        ai.generate_exercise("zip", 1, settings=s)


def test_engine_bug_never_escapes(use, monkeypatch):
    use(GOOD)
    monkeypatch.setattr(engine, "to_exercise", lambda *a, **k: 1 / 0)
    with pytest.raises(ai.GenerationUnavailable, match="ZeroDivisionError"):
        ai.generate_exercise("zip", 1)


# ----- cache and variety --------------------------------------------------------------------

def test_cache_write_and_reuse(use, tmp_path):
    use(GOOD)
    ex = ai.generate_exercise("zip", 2)
    assert (tmp_path / "generated" / "zip.json").exists()
    assert [e.id for e in cache.load("zip")] == [ex.id]
    # the model is down now: the saved exercise is reused
    use(ProviderError("Ollama is not running"))
    assert ai.generate_exercise("zip", 2).id == ex.id
    # ...but not one the student has already seen, and not for another level
    use(ProviderError("Ollama is not running"))
    with pytest.raises(ai.GenerationUnavailable):
        ai.generate_exercise("zip", 2, avoid_ids={ex.id})
    use(ProviderError("Ollama is not running"))
    with pytest.raises(ai.GenerationUnavailable):
        ai.generate_exercise("zip", 3)


def test_avoid_ids_reject_repeats_and_are_named_in_the_prompt(use):
    use(GOOD)
    first = ai.generate_exercise("zip", 1)
    fake = use(GOOD, OTHER)                       # the model repeats itself first
    second = ai.generate_exercise("zip", 1, avoid_ids={first.id})
    assert second.id != first.id and second.function_name == "as_dict"
    assert len(fake.calls) == 2
    assert "Do not repeat" in user_text(fake.calls[0]) and "pair_up" in user_text(fake.calls[0])


def test_cached_exercise_is_not_returned_as_new(use):
    use(GOOD)
    first = ai.generate_exercise("zip", 1)
    fake = use(GOOD, OTHER)
    assert ai.generate_exercise("zip", 1).id != first.id
    assert len(fake.calls) == 2


def test_situations_vary_between_calls(use):
    fake = use(GOOD, OTHER, reply("third", "return [list(p) for p in zip(names, scores)]"))
    for _ in range(3):
        ai.generate_exercise("zip", 1, avoid_ids=set())
    stories = [next(line for line in user_text(c).splitlines()
                    if line.startswith("Real-world situation"))
               for c in fake.calls]
    assert len(set(stories)) == 3
    assert len({c["seed"] for c in fake.calls}) == 3


# ----- dotted targets end to end ------------------------------------------------------------

def test_module_target_end_to_end(use):
    use(json.dumps({
        "question_text": "Write middle_temp(temps) that returns the median temperature.",
        "function_name": "middle_temp", "function_signature": "def middle_temp(temps):",
        "reference_solution": "import statistics\ndef middle_temp(temps):\n"
                              "    return statistics.median(temps)",
        "tests": [{"temps": [3, 1, 2]}, {"temps": [4, 1]}, {"temps": [7]}], "hint": "h"}))
    ex = ai.generate_exercise("statistics.median", 1)
    assert ex.target_function == "statistics.median" and ex.allowed_imports == ["statistics"]
    from pygym.grader import grade
    student = "from statistics import median\ndef middle_temp(temps):\n    return median(temps)"
    assert grade(student, ex).passed
    cheat = ("import statistics\ndef middle_temp(temps):\n    if False:\n"
             "        statistics.median(temps)\n    s = sorted(temps)\n    n = len(s)\n"
             "    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2")
    assert grade(cheat, ex).reason == "target_not_used"


# ----- status / available -------------------------------------------------------------------

def _unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_available_false_when_server_unreachable(monkeypatch):
    # the real urlopen, but only to a closed port on this machine
    monkeypatch.setattr(urllib.request, "urlopen", REAL_URLOPEN)
    s = config.load()
    s.provider, s.base_url = "ollama", f"http://127.0.0.1:{_unused_port()}"
    config.save(s)
    st = ai.status()
    assert st.ok is False and "not running" in st.message
    assert ai.available() is False


def test_available_never_raises(monkeypatch):
    def boom(settings):
        raise RuntimeError("broken")
    monkeypatch.setattr(engine, "get_provider", boom)
    assert ai.available() is False


def test_status_uses_the_provider_ping_and_is_cached(use):
    fake = use(ok=True, reason="ready")
    assert ai.status().ok and ai.available()
    fake.ok = False
    assert ai.available() is True                 # remembered for a few seconds


# ----- Session in both modes ----------------------------------------------------------------

def test_session_ai_mode_end_to_end(use):
    use(GOOD, OTHER)
    s = Session("zip", 2, mode="ai")
    assert s.notice == ai.WARNING and s.exercise.source == "ai"
    r = s.handle(":w", s.exercise.reference_solution)
    assert r.kind == "pass"
    s.handle(":s")
    assert s.exercise.function_name == "as_dict" and s.exercise.source == "ai"


def test_session_ai_mode_falls_back_to_preset(use):
    use(ProviderError("Ollama is not running at http://localhost:11434"))
    s = Session("zip", 1, mode="ai")
    assert s.exercise.source == "preset"
    assert s.notice.startswith("AI mode unavailable (Ollama is not running")


def test_session_preset_mode_generates_for_a_function_without_presets(use):
    target = "statistics.median"
    with pytest.raises(loader.ExerciseUnavailable):
        loader.pick(target)
    use(json.dumps({
        "question_text": "Return the median.", "function_name": "middle",
        "function_signature": "def middle(xs):",
        "reference_solution": "import statistics\ndef middle(xs):\n    return statistics.median(xs)",
        "tests": [[[3, 1, 2]], [[4, 1]], [[7]]], "hint": "h"}))
    s = Session(target, 1, mode="preset")
    assert s.exercise.source == "ai" and s.notice == ai.WARNING


def test_session_preset_mode_without_ai_raises(monkeypatch):
    monkeypatch.setenv("PYGYM_NO_AI", "1")
    with pytest.raises(loader.ExerciseUnavailable):
        Session("statistics.median", 1, mode="preset")


def test_rejected_replies_go_to_the_local_log(use):
    use("{bad", GOOD)
    ai.generate_exercise("zip", 1)
    entry = json.loads(cache.rejected_path().read_text().splitlines()[-1])
    assert entry["target"] == "zip" and "JSON" in entry["problem"] and entry["reply"] == "{bad"
