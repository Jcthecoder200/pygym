"""ai.cache: saved generated exercises in PYGYM_HOME/generated/."""
import json

import pytest

from pygym import progress
from pygym.ai import cache
from pygym.models import Exercise


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))


def make(i, level=1, target="zip"):
    return Exercise(id=f"ai-{target}-{level}-{i:08d}", target_function=target, difficulty=level,
                    question_text=f"q{i}", function_name="f", function_signature="def f(a):",
                    reference_solution="def f(a):\n    return list(zip(a))",
                    test_inputs=[[[1]], [[2]], [[3]]], expected_outputs=[[[1]], [[2]], [[3]]],
                    hint="h", source="ai", generator="fake:fake")


def test_add_load_pick(tmp_path):
    assert cache.load("zip") == [] and cache.pick("zip", 1) is None
    cache.add(make(1))
    cache.add(make(2, level=2))
    cache.add(make(1))                                   # same id: replaced, not duplicated
    assert [e.id for e in cache.load("zip")] == [make(2, 2).id, make(1).id]
    assert cache.pick("zip", 1).id == make(1).id
    assert cache.pick("zip", 1, avoid_ids={make(1).id}) is None
    assert cache.pick("zip", 3) is None
    data = json.loads((tmp_path / "generated" / "zip.json").read_text())
    assert data["version"] == 1 and len(data["exercises"]) == 2
    assert not list((tmp_path / "generated").glob("*.tmp"))   # written via a temp file


def test_dotted_target_file_name(tmp_path):
    cache.add(make(1, target="statistics.median"))
    assert (tmp_path / "generated" / "statistics.median.json").exists()
    assert len(cache.load("statistics.median")) == 1 and cache.load("zip") == []


def test_keeps_the_newest(tmp_path):
    for i in range(7):
        cache.add(make(i), keep=5)
    assert [e.id for e in cache.load("zip")] == [make(i).id for i in range(2, 7)]


def test_prefers_unsolved():
    cache.add(make(1))
    cache.add(make(2))
    progress.record(make(1).id, True)
    assert {cache.pick("zip", 1).id for _ in range(20)} == {make(2).id}


@pytest.mark.parametrize("content", ["not json", "[]", '{"exercises": [{"id": 1}]}',
                                     '{"exercises": "x"}'])
def test_broken_file_is_ignored(tmp_path, content):
    (tmp_path / "generated").mkdir()
    (tmp_path / "generated" / "zip.json").write_text(content)
    assert cache.load("zip") == [] and cache.pick("zip", 1) is None
    cache.add(make(1))                                  # and it can be overwritten
    assert len(cache.load("zip")) == 1


def test_broken_exercises_are_skipped():
    bad = make(1)
    bad.expected_outputs = []                            # would "pass everything" (P0-1)
    cache.add(bad)
    cache.add(make(2))
    assert [e.id for e in cache.load("zip")] == [make(2).id]


def test_add_never_raises(tmp_path, monkeypatch):
    (tmp_path / "generated").write_text("a file where the folder should be")
    cache.add(make(1))
    assert cache.load("zip") == []


def test_rejected_replies_are_logged_and_capped(tmp_path):
    for i in range(cache.KEEP_REJECTED + 5):
        cache.log_rejected("zip", 1, "fake:fake", f"problem {i}", "{bad")
    lines = cache.rejected_path().read_text().splitlines()
    assert len(lines) == cache.KEEP_REJECTED
    last = json.loads(lines[-1])
    assert last["problem"] == f"problem {cache.KEEP_REJECTED + 4}" and last["reply"] == "{bad"
