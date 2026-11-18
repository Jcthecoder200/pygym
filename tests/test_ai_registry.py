"""ai.registry: the provider table, names and aliases, and how settings are read (resolve)."""
import pytest

from pygym import config
from pygym.ai import registry
from pygym.ai.registry import PROVIDERS, UnknownProvider, get_spec, resolve


def settings(provider, model="", base_url=""):
    return config.Settings(provider=provider, model=model, base_url=base_url)


def test_every_provider_is_complete():
    assert set(PROVIDERS) == {"ollama", "openai-compatible", "lmstudio", "openai", "anthropic",
                              "gemini", "openrouter", "groq", "hermes", "hermes-agent",
                              "claude-code"}
    for name, spec in PROVIDERS.items():
        assert spec.name == name == name.lower() and spec.description and spec.title
        assert spec.kind in ("ollama", "openai", "anthropic", "claude-code")
        if spec.key_required:
            assert spec.accepts_key and spec.key_env, name
        if spec.cloud and spec.kind != "claude-code":
            assert spec.base_url.startswith("https://") and spec.key_required and spec.company
        if spec.base_url:
            assert spec.base_url.startswith(("http://", "https://")) and not spec.base_url.endswith("/")
        assert spec.model or spec.name in ("lmstudio", "openai-compatible"), name


@pytest.mark.parametrize("typed, name", [
    ("ollama", "ollama"), ("  OpenAI ", "openai"), ("custom", "openai-compatible"),
    ("openai_compat", "openai-compatible"), ("lm-studio", "lmstudio"), ("claude", "claude-code"),
    ("google", "gemini"), ("GROQ", "groq"),
])
def test_get_spec_names_and_aliases(typed, name):
    assert get_spec(typed).name == name


@pytest.mark.parametrize("bad", ["skynet", "", None, "open ai"])
def test_unknown_provider_lists_the_names(bad):
    with pytest.raises(UnknownProvider) as e:
        get_spec(bad)
    assert "ollama" in str(e.value) and "claude-code" in str(e.value)
    assert isinstance(e.value, ValueError)


def test_key_env_names_cover_every_provider():
    names = registry.key_env_names()
    assert names[0] == "PYGYM_API_KEY" and len(names) == len(set(names))
    for spec in PROVIDERS.values():
        assert set(spec.key_env) <= set(names)
    assert {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
            "OPENROUTER_API_KEY", "GROQ_API_KEY", "API_SERVER_KEY"} <= set(names)


def test_empty_model_and_address_are_the_provider_defaults():
    for name, spec in PROVIDERS.items():
        chosen = resolve(settings(name))
        assert chosen.spec is spec and chosen.model == spec.model and chosen.note == ""
        assert chosen.base_url == ("" if spec.kind == "claude-code" else spec.base_url)


def test_chosen_model_and_address_win():
    chosen = resolve(settings("groq", "llama-x", "https://proxy.example.com/v1/"))
    assert (chosen.model, chosen.base_url) == ("llama-x", "https://proxy.example.com/v1")


def test_the_untouched_settings_default_is_ollama():
    chosen = resolve(config.Settings())
    assert (chosen.spec.name, chosen.base_url, chosen.model) == (
        "ollama", "http://localhost:11434", "qwen2.5:1.5b")


# ----- old config files keep their meaning -------------------------------------------------------

def test_old_openai_with_another_address_means_openai_compatible():
    chosen = resolve(settings("openai", "my-model", "http://localhost:1234/v1"))
    assert chosen.spec.name == "openai-compatible" and chosen.base_url == "http://localhost:1234/v1"
    assert chosen.model == "my-model" and "openai-compatible" in chosen.note


def test_old_openai_with_the_openai_address_stays_openai():
    chosen = resolve(settings("openai", "gpt-x", "https://api.openai.com/v1"))
    assert chosen.spec.name == "openai" and chosen.note == ""


def test_left_over_ollama_address_and_model_do_not_leak():
    """Every config.json since 0.1 holds Ollama's address and model, even if never chosen."""
    for name in ("openai", "anthropic", "gemini", "groq", "openrouter", "hermes"):
        chosen = resolve(settings(name, "qwen2.5:1.5b", "http://localhost:11434"))
        spec = PROVIDERS[name]
        assert (chosen.spec.name, chosen.base_url, chosen.model) == (name, spec.base_url, spec.model)


def test_claude_code_never_has_an_address():
    chosen = resolve(settings("claude", "opus", "http://localhost:9999"))
    assert (chosen.spec.name, chosen.base_url, chosen.model) == ("claude-code", "", "opus")


def test_resolve_raises_for_an_unknown_provider():
    with pytest.raises(UnknownProvider):
        resolve(settings("skynet"))
