"""pygym --set-provider / --set-key / --forget-key / --providers / --status. No network, no LLM,
never the real ~/.pygym (conftest), and the key never appears in the output."""
import io
import os
import stat
import sys

import pytest
from rich.console import Console

from pygym import ai, config, menu
from pygym.ai import keys, registry


@pytest.fixture(autouse=True)
def _no_ai(monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: False)


class Starts:
    def __init__(self):
        self.choices = []

    def __call__(self, choice, console):
        self.choices.append(choice)
        return 0


def cli(*argv):
    console = Console(file=io.StringIO(), width=200, color_system=None, force_terminal=False,
                      highlight=False, legacy_windows=False)
    starts = Starts()
    rc = menu.main(list(argv), console=console, start_fn=starts)
    return rc, console.file.getvalue(), starts


class Terminal(io.StringIO):
    def isatty(self):
        return True


class Typed:
    """What the student types at the hidden prompt (.value) and the prompts getpass got."""

    def __init__(self):
        self.value = ""
        self.prompts = []

    def __call__(self, prompt=""):
        self.prompts.append(prompt)
        return self.value


@pytest.fixture
def typed_key(monkeypatch):
    """A terminal: the key is read with getpass (hidden), never with input()."""
    typed = Typed()
    monkeypatch.setattr(sys, "stdin", Terminal())
    monkeypatch.setattr(menu.getpass, "getpass", typed)
    return typed


def piped(monkeypatch, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


# ----- --set-provider -------------------------------------------------------------------------------

@pytest.mark.parametrize("typed, saved", [
    ("anthropic", "anthropic"), ("Gemini", "gemini"), ("google", "gemini"),
    ("custom", "openai-compatible"), ("claude", "claude-code"), ("lm-studio", "lmstudio"),
])
def test_set_provider_takes_names_and_aliases(typed, saved):
    rc, out, starts = cli("--set-provider", typed)
    assert rc == 0 and starts.choices == [] and config.load().provider == saved


def test_unknown_provider_lists_them_all():
    rc, out, _ = cli("--set-provider", "skynet")
    assert rc == 2 and config.load().provider == "ollama"
    for name in registry.PROVIDERS:
        assert name in out


def test_help_lists_the_providers(capsys):
    assert menu.main(["--help"]) == 0
    text = " ".join(capsys.readouterr().out.split())
    for word in ("--set-key", "--forget-key", "--providers", "anthropic", "openai-compatible",
                 "claude-code"):
        assert word in text


def test_another_provider_gets_its_own_address_and_model():
    cli("--set-provider", "ollama", "--set-base-url", "http://192.168.1.5:11434",
        "--set-model", "llama3.2:3b")
    rc, out, _ = cli("--set-provider", "groq")
    s = config.load()
    assert rc == 0 and (s.provider, s.base_url, s.model) == ("groq", "", "")
    chosen = registry.resolve(s)
    assert chosen.base_url == "https://api.groq.com/openai/v1"
    assert "192.168.1.5" not in out and "llama3.2" not in out
    assert "openai/gpt-oss-20b (default)" in out


def test_given_address_and_model_are_kept_when_the_provider_changes():
    cli("--set-provider", "custom", "--set-base-url", "http://localhost:8080/v1",
        "--set-model", "my-model")
    s = config.load()
    assert (s.provider, s.base_url, s.model) == ("openai-compatible", "http://localhost:8080/v1",
                                                 "my-model")


def test_same_provider_again_keeps_the_settings():
    cli("--set-provider", "openai", "--set-model", "gpt-x")
    rc, out, _ = cli("--set-provider", "openai")
    assert rc == 0 and config.load().model == "gpt-x"


def test_old_openai_setting_renamed_keeps_its_address():
    config.save(config.Settings(provider="openai", base_url="http://localhost:1234/v1",
                                model="local-model"))
    cli("--set-provider", "openai-compatible")
    s = config.load()
    assert (s.provider, s.base_url, s.model) == ("openai-compatible", "http://localhost:1234/v1",
                                                 "local-model")


def test_default_word_resets_model_and_address():
    cli("--set-provider", "openai", "--set-model", "gpt-x", "--set-base-url", "https://a.b/v1")
    cli("--set-model", "default", "--set-base-url", "default")
    s = config.load()
    assert (s.model, s.base_url) == ("", "")
    assert registry.resolve(s).model == registry.PROVIDERS["openai"].model


def test_hints_say_what_is_missing():
    rc, out, _ = cli("--set-provider", "anthropic")
    assert "No API key yet for Anthropic: pygym --set-key anthropic, or set ANTHROPIC_API_KEY" in out
    rc, out, _ = cli("--set-provider", "openai-compatible")
    assert "--set-base-url" in out and "--set-model" in out
    rc, out, _ = cli("--set-provider", "ollama")
    assert "No API key" not in out and "--set-base-url URL" not in out


def test_no_key_hint_when_a_key_is_there(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-123456")
    rc, out, _ = cli("--set-provider", "gemini")
    assert rc == 0 and "No API key" not in out and "AIza-123456" not in out


def test_claude_code_hint():
    rc, out, _ = cli("--set-provider", "claude-code")
    assert "log in" in out and "usage limits" in out and "--set-key" not in out


# ----- --set-key / --forget-key ---------------------------------------------------------------------

def test_set_key_from_a_terminal_is_hidden(typed_key):
    typed_key.value = "sk-typed-secret-1"
    rc, out, starts = cli("--set-key", "openai")
    assert rc == 0 and starts.choices == []
    assert keys.get_key("openai") == ("sk-typed-secret-1", "saved")
    assert len(typed_key.prompts) == 1 and "OpenAI" in typed_key.prompts[0]
    assert "hidden" in typed_key.prompts[0]
    assert "Key saved for OpenAI" in out and "sk-typed-secret-1" not in out
    assert "pygym --set-provider openai" in out           # it is not the provider in use yet
    cli("--set-provider", "openai")
    assert "sk-typed-secret-1" not in (keys.path().parent / "config.json").read_text()


def test_set_key_from_a_pipe(monkeypatch):
    piped(monkeypatch, "sk-test\nsomething else\n")
    rc, out, _ = cli("--set-key", "anthropic")
    assert rc == 0 and keys.get_key("anthropic") == ("sk-test", "saved")
    assert "sk-test" not in out
    if os.name == "posix":
        assert stat.S_IMODE(os.stat(keys.path()).st_mode) == 0o600


def test_set_key_with_set_provider_then_status_shows_saved(monkeypatch):
    piped(monkeypatch, "gsk-pipe-424242\n")
    rc, out, _ = cli("--set-provider", "groq", "--set-key", "groq")
    assert rc == 0 and "No API key" not in out and "--set-provider groq" not in out
    rc, out, _ = cli("--status")
    assert "key saved (keys.json)" in out and "gsk-pipe-424242" not in out


@pytest.mark.parametrize("text", ["", "\n", "   \n"])
def test_empty_key_is_not_saved(monkeypatch, text):
    piped(monkeypatch, text)
    rc, out, _ = cli("--set-key", "openai")
    assert rc == 2 and not keys.path().exists()


def test_key_with_spaces_is_refused_without_showing_it(monkeypatch):
    piped(monkeypatch, "sk-abc def-123\n")
    rc, out, _ = cli("--set-key", "openai")
    assert rc == 2 and "spaces" in out and "def-123" not in out and not keys.path().exists()


def test_cancelled_key_prompt(typed_key, monkeypatch):
    def interrupted(prompt=""):
        raise KeyboardInterrupt
    monkeypatch.setattr(menu.getpass, "getpass", interrupted)
    rc, out, _ = cli("--set-key", "openai")
    assert rc == 1 and "Cancelled" in out and not keys.path().exists()


def test_key_on_the_command_line_is_refused_and_never_echoed(monkeypatch):
    piped(monkeypatch, "")
    rc, out, starts = cli("--set-key", "openai", "sk-on-the-line-99")
    assert rc == 2 and starts.choices == [] and "sk-on-the-line-99" not in out
    assert "shell history" in out and not keys.path().exists()
    rc, out, _ = cli("--set-key", "sk-on-the-line-99")         # the key typed as the name
    assert rc == 2 and "sk-on-the-line-99" not in out and "openai" in out
    rc, out, _ = cli("--set-key=sk-on-the-line-99")
    assert rc == 2 and "sk-on-the-line-99" not in out


def test_set_key_for_a_provider_without_keys():
    rc, out, _ = cli("--set-key", "ollama")
    assert rc == 2 and "takes no API key" in out


def test_environment_variable_wins_and_is_named(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-1")
    piped(monkeypatch, "sk-saved-2\n")
    rc, out, _ = cli("--set-key", "openai")
    assert rc == 0 and "OPENAI_API_KEY is set in your environment and is used first" in out
    assert "sk-env-1" not in out and "sk-saved-2" not in out


def test_forget_key(monkeypatch):
    keys.save_key("openai", "sk-old-1")
    rc, out, starts = cli("--forget-key", "openai")
    assert rc == 0 and starts.choices == [] and "deleted" in out
    assert keys.get_key("openai") == (None, "") and "sk-old-1" not in out
    rc, out, _ = cli("--forget-key", "openai")
    assert rc == 0 and "No saved key for OpenAI" in out
    monkeypatch.setenv("PYGYM_API_KEY", "x-123")
    rc, out, _ = cli("--forget-key", "groq")
    assert "still gets a key from PYGYM_API_KEY" in out and "x-123" not in out
    assert cli("--forget-key", "skynet")[0] == 2


# ----- --providers ------------------------------------------------------------------------------------

def test_providers_table(monkeypatch):
    def no_network(*a, **kw):
        raise AssertionError("--providers must not use the network")
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    monkeypatch.setattr("socket.create_connection", no_network)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-in-env-1")
    keys.save_key("anthropic", "sk-ant-saved-1")
    cli("--set-provider", "gemini")
    rc, out, starts = cli("--providers")
    assert rc == 0 and starts.choices == []
    for spec in registry.PROVIDERS.values():
        assert spec.name in out
    for word in ("key set (GROQ_API_KEY)", "key saved (keys.json)", "no key needed",
                 "gemini *", "claude-haiku-5-5", "Google", "never your code", "custom ="):
        assert word in out, word
    assert "gsk-in-env-1" not in out and "sk-ant-saved-1" not in out


# ----- --status ---------------------------------------------------------------------------------------

def test_status_with_a_cloud_provider_and_no_key(monkeypatch):
    monkeypatch.delenv("PYGYM_NO_AI", raising=False)
    calls = []
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: calls.append(a))
    ai.engine._status_cache.clear()
    cli("--set-provider", "anthropic")
    rc, out, _ = cli("--status")
    assert rc == 0 and calls == []                  # a cloud ready check never waits on the network
    for word in ("Anthropic (anthropic)", "claude-haiku-5-5 (default)",
                 "https://api.anthropic.com (default)",
                 "no key: pygym --set-key anthropic, or set ANTHROPIC_API_KEY",
                 "prompt goes to", "never your code", "not ready",
                 "no API key for Anthropic"):
        assert word in out, word
    ai.engine._status_cache.clear()


def test_status_ready_with_resolved_defaults(monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    cli("--set-provider", "openai-compatible", "--set-base-url", "http://localhost:11434/v1",
        "--set-model", "qwen2.5:1.5b")
    rc, out, _ = cli("--status")
    assert "http://localhost:11434/v1" in out and "qwen2.5:1.5b" in out
    assert "nobody else: it stays on this computer" in out and "ready" in out
    cli("--set-provider", "ollama")
    rc, out, _ = cli("--status")
    assert "Ollama (ollama)" in out and "http://localhost:11434 (default)" in out
    assert "no key needed" in out


def test_status_explains_an_old_openai_setting():
    config.save(config.Settings(provider="openai", base_url="https://llm.example.com/v1"))
    rc, out, _ = cli("--status")
    assert "read as 'openai-compatible'" in out and "the server at llm.example.com" in out


def test_status_with_an_unknown_provider_never_crashes():
    path = keys.path().parent / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"provider": "skynet"}')
    rc, out, _ = cli("--status")
    assert rc == 0 and "skynet: unknown" in out


def test_home_panel_shows_the_resolved_model():
    s = config.Settings(provider="groq", model="", base_url="")
    console = Console(file=io.StringIO(), width=120, color_system=None)
    console.print(menu.home_panel(s, True))
    assert "groq · openai/gpt-oss-20b" in console.file.getvalue()
    console = Console(file=io.StringIO(), width=120, color_system=None)
    console.print(menu.home_panel(s, False))
    assert "no answer from https://api.groq.com/openai/v1" in console.file.getvalue()
