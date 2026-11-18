"""ai.keys: where a key comes from, keys.json (0600), and that a key is never shown."""
import json
import os
import stat

import pytest

from pygym import config
from pygym.ai import keys


def test_no_key_anywhere():
    assert keys.get_key("openai") == (None, "")
    assert keys.describe("openai") == "no key"
    assert keys.describe("openai-compatible") == "no key (optional)"
    assert keys.describe("ollama") == "no key needed"
    assert keys.get_key("ollama") == (None, "")


def test_order_provider_variable_then_saved_then_pygym_api_key(monkeypatch):
    monkeypatch.setenv("PYGYM_API_KEY", "fallback-key")
    assert keys.get_key("openai") == ("fallback-key", "env PYGYM_API_KEY")
    keys.save_key("openai", "saved-key")
    assert keys.get_key("openai") == ("saved-key", "saved")
    monkeypatch.setenv("OPENAI_API_KEY", "  env-key \n")
    assert keys.get_key("openai") == ("env-key", "env OPENAI_API_KEY")
    assert keys.get_key("groq") == ("fallback-key", "env PYGYM_API_KEY")   # not openai's key


def test_first_listed_variable_wins(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "google")
    assert keys.get_key("gemini") == ("google", "env GOOGLE_API_KEY")
    monkeypatch.setenv("GEMINI_API_KEY", "gemini")
    assert keys.get_key("google") == ("gemini", "env GEMINI_API_KEY")      # an alias works too


def test_providers_without_keys_ignore_pygym_api_key(monkeypatch):
    monkeypatch.setenv("PYGYM_API_KEY", "x")
    for name in ("ollama", "lmstudio", "hermes", "claude-code"):
        assert keys.get_key(name) == (None, "")


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
def test_keys_json_is_owner_only_and_never_in_config():
    keys.save_key("anthropic", "sk-ant-secret")
    assert stat.S_IMODE(os.stat(keys.path()).st_mode) == 0o600
    assert json.loads(keys.path().read_text()) == {"anthropic": "sk-ant-secret"}
    config.save(config.Settings(provider="anthropic"))
    assert "sk-ant-secret" not in (keys.path().parent / "config.json").read_text()
    keys.save_key("groq", "gsk-2")                     # rewritten: still 0600, both kept
    assert stat.S_IMODE(os.stat(keys.path()).st_mode) == 0o600
    assert json.loads(keys.path().read_text()) == {"anthropic": "sk-ant-secret", "groq": "gsk-2"}
    assert not list(keys.path().parent.glob("*.tmp"))


def test_keys_json_lives_in_pygym_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path / "elsewhere"))
    keys.save_key("openai", "k-123")
    assert (tmp_path / "elsewhere" / "keys.json").exists()


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", '{"openai": 5, "groq": ""}', ""])
def test_a_broken_keys_file_means_no_saved_keys(text):
    keys.path().parent.mkdir(parents=True, exist_ok=True)
    keys.path().write_text(text)
    assert keys.get_key("openai") == (None, "") and keys.describe("openai") == "no key"
    keys.save_key("openai", "fresh")                   # and saving still works
    assert keys.get_key("openai") == ("fresh", "saved")


def test_describe_never_shows_the_key(monkeypatch):
    keys.save_key("openai", "sk-saved-123456")
    assert keys.describe("openai") == "key saved (keys.json)"
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-123456")
    assert keys.describe("openai") == "key set (OPENAI_API_KEY)"
    for name in ("openai", "anthropic", "openai-compatible", "hermes-agent"):
        text = keys.describe(name)
        assert "sk-" not in text and "123456" not in text


@pytest.mark.parametrize("provider, key, why", [
    ("ollama", "abc", "needs no API key"), ("openai", "   ", "empty"),
    ("openai", "sk abc", "spaces"), ("openai", "sk-\x00abc", "invisible"),
])
def test_bad_keys_are_refused_without_echoing_them(provider, key, why):
    with pytest.raises(ValueError) as e:
        keys.save_key(provider, key)
    assert why in str(e.value) and (key.strip() == "" or key.strip() not in str(e.value))
    assert not keys.path().exists()


def test_remove_key():
    assert keys.remove_key("openai") is False
    keys.save_key("openai", "a-key")
    keys.save_key("groq", "b-key")
    assert keys.remove_key("openai") is True and keys.get_key("openai") == (None, "")
    assert keys.get_key("groq") == ("b-key", "saved")
    assert keys.remove_key("groq") is True and not keys.path().exists()   # empty: file deleted


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        keys.get_key("skynet")


def test_redact():
    assert keys.redact("bad key sk-12345 sent", "sk-12345") == "bad key [key hidden] sent"
    assert keys.redact("nothing here", "sk-12345") == "nothing here"
    assert keys.redact("a b c", "a") == "a b c"        # too short to hide safely
    assert keys.redact("text", None) == "text"
