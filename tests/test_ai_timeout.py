"""The AI time budget: 60 s by default, configurable as ai_timeout (10-300 s)."""
import json

import pytest

from pygym import ai, config, menu
from pygym.ai import engine
from pygym.ai.providers import FakeProvider


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)


def _write(tmp_path, **data):
    (tmp_path / "config.json").write_text(json.dumps(data), encoding="utf-8")


def test_default_is_a_minute():
    assert config.Settings().ai_timeout == 60 and engine.DEFAULT_TIMEOUT == 60.0


@pytest.mark.parametrize("value, loaded", [
    (90, 90), (10, 10), (300, 300), (45.5, 45),
    (5, 60), (301, 60), ("90", 60), (True, 60), (None, 60),
])
def test_ai_timeout_is_checked_on_load(tmp_path, value, loaded):
    _write(tmp_path, ai_timeout=value)
    assert config.load().ai_timeout == loaded


def test_invalid_ai_timeout_is_not_saved():
    with pytest.raises(ValueError):
        config.save(config.Settings(ai_timeout=5))
    config.save(config.Settings(ai_timeout=120))
    assert config.load().ai_timeout == 120


def _capture_budget(monkeypatch):
    seen = {}

    def fake_generate(info, level, avoid, settings, timeout, report, bigger=False):
        seen["timeout"] = timeout
        raise ai.GenerationUnavailable("stop here")
    monkeypatch.setattr(engine, "_generate", fake_generate)
    return seen


def test_generate_uses_the_setting_when_no_timeout_is_given(tmp_path, monkeypatch):
    _write(tmp_path, ai_timeout=90)
    seen = _capture_budget(monkeypatch)
    with pytest.raises(ai.GenerationUnavailable):
        ai.generate_exercise("zip", 1)
    assert seen["timeout"] == 90.0


def test_an_explicit_timeout_wins(monkeypatch):
    seen = _capture_budget(monkeypatch)
    with pytest.raises(ai.GenerationUnavailable):
        ai.generate_exercise("zip", 1, timeout=12.5)
    assert seen["timeout"] == 12.5


def test_progress_events_report_the_budget(monkeypatch):
    fake = FakeProvider(["{bad"] * engine.MAX_ATTEMPTS)
    monkeypatch.setattr(engine, "get_provider", lambda settings: fake)
    events = []
    with pytest.raises(ai.GenerationUnavailable):
        ai.generate_exercise("zip", 1, on_progress=events.append)
    assert events and all(e["budget"] == 60.0 for e in events)
    assert len(fake.calls) == engine.MAX_ATTEMPTS        # no real model was asked


def test_menu_texts_show_the_budget(tmp_path):
    _write(tmp_path, ai_timeout=75)
    assert menu._ai_timeout_text() == "up to 75 s"
