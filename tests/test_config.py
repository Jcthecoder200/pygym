"""config.py: defaults, round trip, and cleaning of bad values."""
import json

import pytest

from pygym import config
from pygym.config import Settings


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    return tmp_path


def write(home, text):
    (home / "config.json").write_text(text, encoding="utf-8")


def test_defaults_when_no_file():
    s = config.load()
    assert s == Settings()
    assert (s.mode, s.level, s.ui, s.library) == ("preset", 1, "auto", "builtins")
    assert s.provider == "ollama" and s.model == "qwen2.5:1.5b"
    assert s.base_url == "http://localhost:11434"


def test_save_and_load_round_trip(_home):
    s = Settings(mode="ai", level=3, model="qwen2.5:3b", ui="text", library="statistics")
    config.save(s)
    assert (_home / "config.json").exists()
    assert config.load() == s
    assert not (_home / "config.json.tmp").exists()


def test_save_creates_the_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path / "new" / "place"))
    config.save(Settings(mode="ai"))
    assert config.load().mode == "ai"


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", "null", '"preset"', "{"])
def test_broken_file_gives_defaults(_home, text):
    write(_home, text)
    assert config.load() == Settings()


def test_unknown_keys_are_ignored(_home):
    write(_home, json.dumps({"mode": "ai", "colour": "red"}))
    s = config.load()
    assert s.mode == "ai" and not hasattr(s, "colour")


def test_old_file_without_new_fields_keeps_its_values(_home):
    write(_home, json.dumps({"mode": "ai", "level": 2, "model": "llama3.2:3b"}))
    s = config.load()
    assert (s.mode, s.level, s.model, s.ui, s.library) == ("ai", 2, "llama3.2:3b", "auto", "builtins")


@pytest.mark.parametrize("bad", ["", "robot", 3, None, ["ai"]])
def test_bad_mode_becomes_preset(_home, bad):
    write(_home, json.dumps({"mode": bad}))
    assert config.load().mode == "preset"


def test_mode_is_case_insensitive(_home):
    write(_home, json.dumps({"mode": " AI "}))
    assert config.load().mode == "ai"


@pytest.mark.parametrize("bad", [0, 4, -1, "2", 2.0, True, None, []])
def test_bad_level_becomes_one(_home, bad):
    write(_home, json.dumps({"level": bad, "mode": "ai"}))
    s = config.load()
    assert s.level == 1 and s.mode == "ai"   # the other values are kept


def test_bad_ui_becomes_auto(_home):
    write(_home, json.dumps({"ui": "hologram"}))
    assert config.load().ui == "auto"


@pytest.mark.parametrize("field", ["provider", "model", "base_url", "library"])
@pytest.mark.parametrize("bad", ["", "   ", 5, None])
def test_bad_text_fields_use_the_default(_home, field, bad):
    write(_home, json.dumps({field: bad}))
    assert getattr(config.load(), field) == getattr(Settings(), field)


@pytest.mark.parametrize("settings", [
    Settings(mode="robot"), Settings(level=7), Settings(level=True), Settings(ui="x"),
    Settings(model=""), Settings(mode="AI"),
])
def test_save_refuses_invalid_values(settings, _home):
    with pytest.raises(ValueError):
        config.save(settings)
    assert not (_home / "config.json").exists()


def test_failed_save_keeps_the_old_file(_home):
    config.save(Settings(mode="ai"))
    with pytest.raises(ValueError):
        config.save(Settings(mode="nope"))
    assert config.load().mode == "ai"
