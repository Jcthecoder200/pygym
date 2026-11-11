"""ai.usecases and how the engine uses them: one model call per target lists real-world
situations, they are checked, saved and taken in turns; each exercise is set in one of them."""
import json
import random
import urllib.request

import pytest

from pygym import ai, style
from pygym.ai import engine, prompt, usecases
from pygym.ai.providers import FakeProvider, ProviderError
from pygym.ai.targets import resolve

# The 30 fixed themes exercises used to be set in (whatever the function). None may come back.
OLD_SCENARIOS = [
    "a school gradebook", "a weather station", "a music playlist", "a small shop",
    "a sports league table", "a library catalogue", "a to-do app", "a train timetable",
    "a recipe book", "a game leaderboard", "a fitness tracker", "a chat log",
    "a bus route planner", "a cinema booking system", "a plant watering schedule",
    "a student club budget", "a bakery order list", "a hiking trail log", "a coffee shop menu",
    "a parking garage", "a photo album", "a quiz game", "a bike rental service",
    "a university course list", "a pet shelter", "a word game", "a bank statement",
    "a delivery tracker", "a football match report", "a space mission log",
]

CUMSUM_CASES = ["a bank account balance after each transaction", "rainfall so far this month",
                "distance travelled after each stop", "total sales so far each day",
                "points so far after each game", "calories eaten so far today"]
ZIP_CASES = ["pair each student with their test score", "match products with their prices",
             "combine dates with temperatures", "pair players with their shirt numbers",
             "join first names with last names", "match questions with answers"]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch, real_usecases):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)
    monkeypatch.setattr(engine, "_status_cache", {})

    def no_network(*a, **k):
        raise AssertionError("unit tests must not use the network")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)


@pytest.fixture
def use(monkeypatch):
    def install(*replies, **kw):
        fake = FakeProvider(replies, **kw)
        monkeypatch.setattr(engine, "get_provider", lambda settings: fake)
        return fake
    return install


def ideas(cases):
    return json.dumps({"use_cases": cases})


def exercise(name="pair_up", body="return list(zip(names, scores))"):
    return json.dumps({
        "question_text": f"Write {name}(names, scores) that pairs names and scores with zip.",
        "function_name": name, "function_signature": f"def {name}(names, scores):",
        "reference_solution": f"def {name}(names, scores):\n    {body}",
        "tests": [{"names": ["ana", "bo"], "scores": [3, 7]}, {"names": [], "scores": []},
                  {"names": ["x"], "scores": [1, 2]}],
        "hint": "zip pairs items."})


def user_text(call):
    return "\n".join(m["content"] for m in call["messages"] if m["role"] == "user")


def situation(call):
    return next(line for line in user_text(call).splitlines()
                if line.startswith("Real-world situation: "))[len("Real-world situation: "):]


# ----- the request ------------------------------------------------------------------------------

def test_the_use_case_request_comes_first_with_its_own_schema_and_time_slice(use):
    fake = use(ideas(ZIP_CASES), exercise())
    ex = ai.generate_exercise("zip", 1, timeout=20.0)
    assert ex.function_name == "pair_up" and len(fake.calls) == 2
    first, second = fake.calls
    assert first["schema"] == usecases.SCHEMA
    assert first["schema"]["properties"]["use_cases"]["minItems"] == 6
    assert first["schema"]["properties"]["use_cases"]["maxItems"] == 8
    text = user_text(first)
    assert "`zip`" in text and "real-world situations" in text and "natural" in text
    assert first["timeout"] <= usecases.TIME_SLICE and first["max_tokens"] == usecases.MAX_TOKENS
    assert second["schema"] == prompt.SCHEMA
    assert situation(second).rstrip(".") in ZIP_CASES


def test_the_time_slice_is_a_share_of_a_small_budget(use):
    fake = use(ideas(ZIP_CASES), exercise())
    ai.generate_exercise("zip", 1, timeout=10.0)
    assert fake.calls[0]["timeout"] <= 10.0 * usecases.BUDGET_SHARE + 1e-9
    assert fake.calls[1]["timeout"] <= 10.0


# ----- parsing and checking ----------------------------------------------------------------------

def test_parse_cleans_and_checks_the_list():
    raw = json.dumps({"use_cases": [
        "1. Rainfall so far this month.", "- rainfall so far this month", "  ", 42,
        "x" * 121, "a bank   account balance\nafter each payment", "distance after each stop",
    ]})
    assert usecases.parse(raw) == ["Rainfall so far this month",
                                   "a bank account balance after each payment",
                                   "distance after each stop"]


def test_parse_keeps_at_most_eight_and_accepts_exactly_120_characters():
    many = [f"case number {i}" for i in range(12)]
    assert usecases.parse(ideas(many)) == many[:8]
    long_ok = "y" * 120
    assert usecases.parse(ideas([long_ok, "a", "b"]))[0] == long_ok


@pytest.mark.parametrize("raw, why", [
    ("{not json", "JSON"),
    (json.dumps({"cases": ["a", "b", "c"]}), "use_cases"),
    (ideas(["a", "A", "a."]), "only 1"),
    (ideas(["one", "two", "", "x" * 200]), "only 2"),
    (json.dumps([1, 2, 3]), "only 0"),
])
def test_parse_rejects_unusable_replies(raw, why):
    with pytest.raises(usecases.InvalidUseCases, match=why):
        usecases.parse(raw)


def test_a_bare_list_is_accepted():
    assert usecases.parse(json.dumps(["a", "b", "c"])) == ["a", "b", "c"]


# ----- saved, reused, taken in turns ------------------------------------------------------------

def test_use_cases_are_saved_and_reused_without_a_second_call(use, tmp_path):
    use(ideas(ZIP_CASES), exercise())
    ai.generate_exercise("zip", 1)
    file = tmp_path / "generated" / "usecases" / "zip.json"
    assert file == usecases.path("zip") and file.exists()
    saved = json.loads(file.read_text())
    assert saved["target"] == "zip" and saved["use_cases"] == ZIP_CASES
    assert saved["generator"] == "fake:fake" and len(saved["used"]) == 1
    fake = use(exercise("as_dict", "return dict(zip(names, scores))"))
    ai.generate_exercise("zip", 1)
    assert len(fake.calls) == 1 and fake.calls[0]["schema"] == prompt.SCHEMA


def test_every_use_case_comes_round_before_any_repeats():
    usecases.save("numpy.cumsum", CUMSUM_CASES)
    rng = random.Random(7)
    first_round = [usecases.pick("numpy.cumsum", CUMSUM_CASES, rng) for _ in CUMSUM_CASES]
    assert sorted(first_round) == sorted(CUMSUM_CASES)
    second_round = [usecases.pick("numpy.cumsum", CUMSUM_CASES, rng) for _ in CUMSUM_CASES]
    assert second_round == first_round  # then the one used longest ago, each time
    assert json.loads(usecases.path("numpy.cumsum").read_text())["used"] == first_round


def test_each_new_exercise_gets_a_situation_not_used_recently(use):
    fake = use(ideas(ZIP_CASES), *[exercise(f"pair_{i}") for i in range(4)])
    for _ in range(4):
        ai.generate_exercise("zip", 1)
    situations = [situation(c).rstrip(".") for c in fake.calls[1:]]
    assert len(set(situations)) == 4 and set(situations) <= set(ZIP_CASES)


def test_a_fresh_retry_takes_another_situation_and_a_repair_keeps_it(use):
    bad_schema = json.dumps({"question_text": "x"})
    fake = use(ideas(ZIP_CASES), "{not json", bad_schema, exercise())
    ai.generate_exercise("zip", 1, timeout=60.0)
    first, fresh, repair = (situation(c) for c in fake.calls[1:4])
    assert fresh != first          # "{not json" is not repairable: a fresh try, a new situation
    assert repair == fresh         # a schema problem is sent back for repair: same situation


def test_a_broken_saved_file_is_asked_again(use):
    file = usecases.path("zip")
    file.parent.mkdir(parents=True)
    file.write_text('{"target": "zip", "use_cases": ["only one"]}')
    fake = use(ideas(ZIP_CASES), exercise())
    ai.generate_exercise("zip", 1)
    assert fake.calls[0]["schema"] == usecases.SCHEMA
    assert usecases.load("zip") == ZIP_CASES


# ----- when the model gives none ------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["{not json", ideas(["only", "two"]),
                                 ProviderError("HTTP 500 from server")])
def test_failure_writes_without_a_story_and_asks_again_next_time(use, bad):
    fake = use(bad, exercise())
    ex = ai.generate_exercise("zip", 1)
    assert ex.function_name == "pair_up"
    assert situation(fake.calls[1]) == prompt.NO_USE_CASE.format(target="zip") + "."
    assert not usecases.path("zip").exists()
    fake = use(ideas(ZIP_CASES), exercise("as_dict", "return dict(zip(names, scores))"))
    ai.generate_exercise("zip", 1)
    assert fake.calls[0]["schema"] == usecases.SCHEMA          # tried again
    assert situation(fake.calls[1]).rstrip(".") in ZIP_CASES


# ----- progress -----------------------------------------------------------------------------------

def test_the_ideas_stage_is_reported_first_and_only_when_asking(use):
    use(ideas(ZIP_CASES), exercise())
    events = []
    ai.generate_exercise("zip", 1, on_progress=events.append)
    assert [(e["stage"], e["attempt"]) for e in events] == [
        ("ideas", 0), ("asking", 1), ("checking", 1), ("done", 1)]
    assert style.progress_text(events[0], 1.4) == "finding real-world uses · 1 s"
    assert "ideas" in engine.STAGES
    use(exercise("as_dict", "return dict(zip(names, scores))"))
    events = []
    ai.generate_exercise("zip", 1, on_progress=events.append)
    assert events[0]["stage"] == "asking"                        # saved: no "ideas" step


# ----- the exercise prompt ------------------------------------------------------------------------

@pytest.mark.parametrize("target", ["zip", "numpy.cumsum", "statistics.median", "str.join"])
def test_the_prompt_has_the_use_case_and_the_no_hidden_rules_rule(target):
    info = resolve(target)
    case = "a bank account balance after each transaction"
    for use_case in (case, None):
        text = prompt.build_messages(info, 2, use_case=use_case)[1]["content"]
        lowered = text.lower()
        assert not [s for s in OLD_SCENARIOS if s in lowered]
        assert "Story idea" not in text and "Focus:" not in text
        assert "No hidden rules" in text and "states EVERY rule" in text
        assert f"natural tool, so the student learns" in text
        if use_case:
            assert f"Real-world situation: {case}." in text
        else:
            assert f"a realistic everyday task where `{target}` is the natural tool" in text


def test_old_variety_lists_are_gone():
    assert not hasattr(prompt, "SCENARIOS") and not hasattr(prompt, "FOCUS")
    assert not hasattr(engine, "_recent_scenarios")
