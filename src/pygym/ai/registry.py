"""The AI providers pygym knows: one table, the only place with their names, addresses, key
environment variables and default models.

Model names change over time: the defaults below are suggestions that worked when this was
written (November 2026). Where a server lists its models (/models), the menu offers that list.

    kind "ollama"       OllamaProvider (Ollama's own API)
    kind "openai"       OpenAICompatProvider (POST {base_url}/chat/completions)
    kind "anthropic"    AnthropicProvider (POST {base_url}/v1/messages)
    kind "claude-code"  ClaudeCodeProvider (the `claude -p` command, the student's Claude plan)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

OPENAI_URL = "https://api.openai.com/v1"
HERMES_START = "start it with `hermes proxy start` (and `hermes portal` to log in)"


@dataclass(frozen=True)
class ProviderSpec:
    name: str                       # what the student types: lowercase
    kind: str                       # "ollama", "openai", "anthropic", "claude-code"
    description: str                # one line for the provider screen and --providers
    base_url: str = ""              # default address ("" = none: the student must give one)
    model: str = ""                 # default model ("" = the first the server lists, or required)
    key_env: Tuple[str, ...] = ()   # environment variables with the key, first found wins
    key_required: bool = False      # no key -> not ready
    accepts_key: bool = False       # --set-key works for it
    local: bool = False             # runs on the student's computer (or their own server)
    start_hint: str = ""            # how to start it when it is not running
    company: str = ""               # who receives the prompt (docs and the provider screen)
    dummy_key: str = ""             # a bearer sent when there is no key (servers that want one)
    check_timeout: float = 0.0      # seconds the "is AI ready?" check may take; 0 = automatic
                                    # (providers.ping_timeout: 0.5 s on this computer, 6 s else)
    model_required: bool = False    # no default model: --set-model is needed
    note: str = ""                  # an extra line on the provider screen

    @property
    def cloud(self) -> bool:
        return not self.local

    @property
    def title(self) -> str:
        """The provider's name in messages: 'OpenAI', 'the Hermes proxy'."""
        return _TITLES.get(self.name, self.name)


_TITLES = {"ollama": "Ollama", "openai-compatible": "the OpenAI-compatible server",
           "lmstudio": "LM Studio", "openai": "OpenAI", "anthropic": "Anthropic",
           "gemini": "Gemini", "openrouter": "OpenRouter", "groq": "Groq",
           "hermes": "the Hermes proxy", "hermes-agent": "the Hermes API server",
           "claude-code": "Claude Code"}


_SPECS = (
    ProviderSpec("ollama", "ollama", "local Ollama on this computer (free, private)",
                 base_url="http://localhost:11434", model="qwen2.5:1.5b", local=True,
                 start_hint="start Ollama (`ollama serve`), then `ollama pull <model>`"),
    ProviderSpec("openai-compatible", "openai",
                 "any OpenAI-compatible server (llama.cpp, vLLM, Ollama /v1, a hosted API)",
                 key_env=("PYGYM_API_KEY",), accepts_key=True, local=True,
                 model_required=True,
                 start_hint="check the address (pygym --set-base-url URL)"),
    ProviderSpec("lmstudio", "openai", "LM Studio's local server (free, private)",
                 base_url="http://localhost:1234/v1", local=True,
                 start_hint="start the server in LM Studio (Developer tab) or `lms server start`"),
    ProviderSpec("openai", "openai", "OpenAI API (your API key, paid per use)",
                 base_url=OPENAI_URL, model="gpt-6-luna", key_env=("OPENAI_API_KEY",),
                 key_required=True, accepts_key=True, company="OpenAI"),
    ProviderSpec("anthropic", "anthropic", "Anthropic API, Claude models (your API key, paid per use)",
                 base_url="https://api.anthropic.com", model="claude-haiku-5-5",
                 key_env=("ANTHROPIC_API_KEY",), key_required=True, accepts_key=True,
                 company="Anthropic"),
    ProviderSpec("gemini", "openai", "Google Gemini API (your API key; has a free tier)",
                 base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                 model="gemini-3.8-flash", key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
                 key_required=True, accepts_key=True, company="Google"),
    ProviderSpec("openrouter", "openai", "OpenRouter: many models with one API key",
                 base_url="https://openrouter.ai/api/v1", model="openrouter/auto",
                 key_env=("OPENROUTER_API_KEY",), key_required=True, accepts_key=True,
                 company="OpenRouter (and the model's maker)"),
    ProviderSpec("groq", "openai", "Groq API: fast open models (your API key; has a free tier)",
                 base_url="https://api.groq.com/openai/v1", model="openai/gpt-oss-20b",
                 key_env=("GROQ_API_KEY",), key_required=True, accepts_key=True,
                 company="Groq"),
    ProviderSpec("hermes", "openai",
                 "Hermes subscription proxy: models through your Nous Portal login",
                 base_url="http://127.0.0.1:8645/v1", model="stepfun/step-3.7-flash:free",
                 local=True, dummy_key="pygym", start_hint=HERMES_START,
                 company="Nous Research (and the model's provider)", check_timeout=10.0,
                 note="free models end in :free; paid models need Nous Portal credits"),
    ProviderSpec("hermes-agent", "openai",
                 "Hermes API server: your full Hermes agent (slower)",
                 base_url="http://127.0.0.1:8642/v1", model="hermes-agent",
                 key_env=("API_SERVER_KEY",), accepts_key=True, local=True,
                 start_hint="start it with `hermes gateway` (API_SERVER_ENABLED=true)",
                 company="the model provider your Hermes agent uses", check_timeout=10.0,
                 note="runs your whole agent for each exercise: slower than the proxy"),
    ProviderSpec("claude-code", "claude-code",
                 "your Claude plan through the Claude Code CLI (no API key)",
                 model="sonnet", local=False, company="Anthropic",
                 start_hint="install Claude Code and run `claude` once to log in",
                 note="each exercise uses some of your Claude plan's usage limits"),
)

PROVIDERS: Dict[str, ProviderSpec] = {spec.name: spec for spec in _SPECS}
ALIASES = {"custom": "openai-compatible", "openai_compat": "openai-compatible",
           "lm-studio": "lmstudio", "claude": "claude-code", "google": "gemini"}
# Short model names that Claude Code understands (it also takes full model ids).
CLAUDE_CODE_MODELS = ("sonnet", "haiku", "opus")


class UnknownProvider(ValueError):
    """A provider name pygym does not know."""


def names() -> Tuple[str, ...]:
    return tuple(PROVIDERS)


def key_env_names() -> Tuple[str, ...]:
    """Every environment variable that may hold an AI key (all providers, plus PYGYM_API_KEY).
    The sandbox and the `claude` child process never get these."""
    found = ["PYGYM_API_KEY"]
    for spec in _SPECS:
        found.extend(name for name in spec.key_env if name not in found)
    return tuple(found)


def get_spec(name: str) -> ProviderSpec:
    """The provider called `name` (case and spaces ignored; aliases accepted)."""
    key = (name or "").strip().lower()
    key = ALIASES.get(key, key)
    if key in PROVIDERS:
        return PROVIDERS[key]
    raise UnknownProvider(f"unknown AI provider '{(name or '').strip()}' "
                          f"(one of: {', '.join(PROVIDERS)})")


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


# Saved by every config.json since 0.1 (the Settings default), even when it was never chosen.
_OLLAMA_DEFAULT = PROVIDERS["ollama"].base_url
_OLLAMA_MODEL = PROVIDERS["ollama"].model


@dataclass(frozen=True)
class Resolved:
    spec: ProviderSpec
    base_url: str      # the address to use ("" only for claude-code / an unset custom server)
    model: str         # the model to use ("" = ask the server for its first model)
    note: str = ""     # how an old setting was read, for --status


def resolve(settings) -> Resolved:
    """Settings -> the provider, address and model to use. Raises UnknownProvider.

    Old config files keep their meaning: provider "openai" with an address that is not
    api.openai.com meant "any OpenAI-compatible server" before 0.3, so it still does. An empty
    base_url or model means the provider's default; so does the untouched Ollama address that
    every config.json holds, for the other providers."""
    raw = str(getattr(settings, "provider", "") or "")
    base_url = str(getattr(settings, "base_url", "") or "").strip().rstrip("/")
    model = str(getattr(settings, "model", "") or "").strip()
    spec = get_spec(raw)
    note = ""
    if spec.name == "openai" and base_url and _host(base_url) != "api.openai.com" \
            and base_url != _OLLAMA_DEFAULT:
        spec = PROVIDERS["openai-compatible"]
        note = (f"provider 'openai' with the address {base_url} is read as "
                "'openai-compatible' (pygym --set-provider openai-compatible)")
    if spec.name not in ("ollama", "openai-compatible") and base_url == _OLLAMA_DEFAULT:
        base_url = ""
    if spec.name not in ("ollama", "openai-compatible", "lmstudio") and model == _OLLAMA_MODEL:
        model = ""     # the Settings default model, left over from Ollama
    if spec.kind == "claude-code":
        base_url = ""
    return Resolved(spec, base_url or spec.base_url, model or spec.model, note)
