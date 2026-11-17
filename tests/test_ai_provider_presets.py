"""The provider presets (ai.registry + ai.providers): request shapes, error explanations, keys
never shown, the ready-check timeouts, and Claude Code with a fake subprocess. No network."""
import io
import json
import subprocess
import urllib.error
import urllib.request

import pytest

from pygym import config
from pygym.ai import keys, providers, registry
from pygym.ai.prompt import SCHEMA
from pygym.ai.providers import (AnthropicProvider, ClaudeCodeProvider, OllamaProvider,
                                OpenAICompatProvider, ProviderError, get_provider, make_provider)
from pygym.ai.registry import PROVIDERS

MESSAGES = [{"role": "system", "content": "You write exercises."},
            {"role": "user", "content": "zip, level 1"}]
EXERCISE = {"question": "q"}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def http_error(code, body=b""):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))


@pytest.fixture
def http(monkeypatch):
    """urlopen replaced: http.answers is a queue (dict = JSON reply, Exception = raised); the
    last answer repeats. http.requests = what was sent."""
    class State:
        answers = [{}]
        requests = []
    state = State()

    def urlopen(req, timeout=None):
        body = json.loads(req.data.decode()) if req.data else None
        state.requests.append({"url": req.full_url, "method": req.get_method(), "body": body,
                               "headers": {k.lower(): v for k, v in req.header_items()},
                               "timeout": timeout})
        answer = state.answers.pop(0) if len(state.answers) > 1 else state.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(json.dumps(answer).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return state


def chat(p, timeout=20.0):
    return p.chat(MESSAGES, SCHEMA, temperature=0.8, max_tokens=900, seed=7, timeout=timeout)


def openai_reply(content="{\"question\": \"q\"}"):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def provider(name, model="", base_url=""):
    return get_provider(config.Settings(provider=name, model=model, base_url=base_url))


# ----- choosing ----------------------------------------------------------------------------------

@pytest.mark.parametrize("name, cls", [
    ("ollama", OllamaProvider), ("openai-compatible", OpenAICompatProvider),
    ("lmstudio", OpenAICompatProvider), ("openai", OpenAICompatProvider),
    ("anthropic", AnthropicProvider), ("gemini", OpenAICompatProvider),
    ("openrouter", OpenAICompatProvider), ("groq", OpenAICompatProvider),
    ("hermes", OpenAICompatProvider), ("hermes-agent", OpenAICompatProvider),
    ("claude-code", ClaudeCodeProvider),
])
def test_each_registry_entry_makes_its_provider(name, cls):
    p = provider(name)
    spec = PROVIDERS[name]
    assert type(p) is cls and p.name == name and p.model == spec.model
    if spec.kind != "claude-code":
        assert p.base_url == spec.base_url


def test_unknown_provider_is_a_provider_error():
    with pytest.raises(ProviderError, match="unknown AI provider 'skynet'"):
        provider("skynet")


# ----- OpenAI-compatible kind: OpenAI, Gemini, OpenRouter, Groq -----------------------------------

@pytest.mark.parametrize("name, env, url", [
    ("openai", "OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions"),
    ("gemini", "GEMINI_API_KEY",
     "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"),
    ("openrouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions"),
    ("groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions"),
])
def test_cloud_openai_kind_request_shape(http, monkeypatch, name, env, url):
    monkeypatch.setenv(env, "key-for-" + name)
    http.answers = [openai_reply()]
    p = provider(name)
    assert json.loads(chat(p)) == EXERCISE
    req = http.requests[0]
    assert req["url"] == url and req["method"] == "POST"
    assert req["headers"]["authorization"] == f"Bearer key-for-{name}"
    body = req["body"]
    assert body["model"] == PROVIDERS[name].model
    assert body["max_tokens"] == 900 and body["temperature"] == 0.8 and body["seed"] == 7
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    assert providers.SCHEMA_INTRO in body["messages"][0]["content"]   # schema in the prompt too
    assert body["messages"][1:] == MESSAGES[1:]


def test_a_saved_key_is_used_and_never_in_repr(http):
    keys.save_key("groq", "gsk-saved-999")
    http.answers = [openai_reply()]
    p = provider("groq")
    chat(p)
    assert http.requests[0]["headers"]["authorization"] == "Bearer gsk-saved-999"
    assert "gsk-saved-999" not in repr(p) and "gsk-saved-999" not in str(p)


def test_cloud_ping_is_instant_and_says_what_is_missing(http):
    ok, why = provider("openai").ping()
    assert not ok and "pygym --set-key openai" in why and "OPENAI_API_KEY" in why
    keys.save_key("openai", "sk-1234567")
    ok, why = provider("openai").ping()
    assert ok and "OpenAI" in why and "sk-1234567" not in why
    assert http.requests == []                       # the menu never waits on a cloud server


def test_chat_without_a_required_key_does_not_call_the_server(http):
    with pytest.raises(ProviderError, match="no API key for Groq: pygym --set-key groq"):
        chat(provider("groq"))
    assert http.requests == []


@pytest.mark.parametrize("refused, fix, absent", [
    (b'{"error": "Unsupported parameter: max_tokens; use max_completion_tokens"}',
     "max_completion_tokens", "max_tokens"),
    (b'{"error": "temperature does not support 0.8 with this model"}', None, "temperature"),
    (b'{"error": "unknown field seed"}', None, "seed"),
])
def test_a_refused_field_is_dropped_once_and_remembered(http, monkeypatch, refused, fix, absent):
    monkeypatch.setenv("OPENAI_API_KEY", "k-abcdef")
    http.answers = [http_error(400, refused), openai_reply()]
    p = provider("openai")
    chat(p)
    first, second = http.requests[0]["body"], http.requests[1]["body"]
    assert absent in first and absent not in second
    if fix:
        assert second[fix] == 900
    http.requests.clear()
    http.answers = [openai_reply()]
    chat(provider("openai"))                          # remembered: no refused request again
    assert len(http.requests) == 1 and absent not in http.requests[0]["body"]


def test_response_format_falls_back_to_json_object_then_none(http, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k-abcdef")
    no = http_error(400, b'{"error": "response_format json_schema is not supported"}')
    no2 = http_error(400, b'{"error": "response_format is not supported"}')
    http.answers = [no, no2, openai_reply()]
    chat(provider("groq"))
    formats = [r["body"].get("response_format") for r in http.requests]
    assert formats[0]["type"] == "json_schema" and formats[1] == {"type": "json_object"}
    assert formats[2] is None


def test_the_reply_is_cleaned(http, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k-abcdef")
    http.answers = [openai_reply("<think>hmm</think>\n```json\n{\"question\": \"q\"}\n```")]
    assert json.loads(chat(provider("openrouter"))) == EXERCISE
    http.answers = [openai_reply([{"type": "text", "text": "{\"question\": \"q\"}"}])]
    assert json.loads(chat(provider("openrouter"))) == EXERCISE
    http.answers = [openai_reply("  ")]
    with pytest.raises(ProviderError, match="empty answer"):
        chat(provider("openrouter"))


@pytest.mark.parametrize("error, words", [
    (http_error(401, b"invalid api key"), ["refused the API key", "HTTP 401",
                                          "pygym --set-key openai", "OPENAI_API_KEY"]),
    (http_error(403, b"forbidden"), ["refused the API key", "HTTP 403"]),
    (http_error(429, b"rate limit reached"), ["rate limit or no credits", "HTTP 429"]),
    (http_error(402, b"payment required"), ["no credits"]),
    (http_error(400, b'{"error": "insufficient_quota"}'), ["no credits"]),
    (http_error(404, b'{"error": "The model gpt-x does not exist"}'),
     ["model 'gpt-6-luna' is not available", "--set-model"]),
    (http_error(404, b"Not Found"), ["has nothing at", "--set-base-url"]),
    (http_error(503, b"overloaded"), ["problem on its side", "HTTP 503"]),
    (urllib.error.URLError(ConnectionRefusedError(111, "refused")),
     ["can't reach OpenAI", "internet"]),
    (urllib.error.URLError(TimeoutError("timed out")), ["did not answer in time"]),
    (TimeoutError("timed out"), ["did not answer in time"]),
])
def test_errors_are_explained_and_never_show_the_key(http, monkeypatch, error, words):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-topsecret-77")
    http.answers = [error]
    with pytest.raises(ProviderError) as e:
        chat(provider("openai"))
    for word in words:
        assert word in str(e.value)
    assert "sk-topsecret-77" not in str(e.value) and "sk-topsecret-77" not in e.value.detail


def test_a_server_that_echoes_the_key_has_it_hidden(http, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-echoed-4242")
    http.answers = [http_error(418, b'{"error": "teapot got key gsk-echoed-4242"}')]
    with pytest.raises(ProviderError) as e:
        chat(provider("groq"))
    assert "gsk-echoed-4242" not in str(e.value) and "[key hidden]" in str(e.value)


def test_hermes_rate_limit_suggests_a_free_model(http):
    http.answers = [http_error(429, b"no credits")]
    with pytest.raises(ProviderError, match=":free"):
        chat(provider("hermes"))
    assert http.requests[0]["headers"]["authorization"] == "Bearer pygym"   # its dummy key


# ----- local OpenAI-compatible servers ------------------------------------------------------------

def test_custom_server_needs_an_address_and_a_model(http):
    ok, why = provider("openai-compatible").ping()
    assert not ok and "--set-base-url" in why
    ok, why = provider("openai-compatible", base_url="http://localhost:8080/v1").ping()
    assert not ok and "--set-model" in why
    assert http.requests == []


def test_custom_server_ready_check_lists_the_models(http):
    http.answers = [{"data": [{"id": "my-model"}, {"id": "other"}]}]
    p = provider("custom", "my-model", "http://localhost:8080/v1")
    assert p.ping(0.5) == (True, "the OpenAI-compatible server is ready with my-model")
    assert http.requests[0]["url"] == "http://localhost:8080/v1/models"
    assert http.requests[0]["timeout"] == 0.5
    http.answers = [{"data": [{"id": "other"}]}]
    ok, why = p.ping()
    assert not ok and "model 'my-model' is not offered" in why


def test_lmstudio_uses_the_first_loaded_model(http):
    http.answers = [{"data": [{"id": "qwen-local"}]}, openai_reply()]
    p = provider("lmstudio")
    assert p.model == ""
    chat(p)
    assert http.requests[0]["url"] == "http://localhost:1234/v1/models"
    assert http.requests[1]["body"]["model"] == "qwen-local"
    assert "authorization" not in http.requests[1]["headers"]
    http.answers = [{"data": []}]
    ok, why = provider("lmstudio").ping()
    assert not ok and "no model is loaded in LM Studio" in why


def test_lmstudio_not_running_says_how_to_start_it(http):
    http.answers = [urllib.error.URLError(ConnectionRefusedError(111, "refused"))]
    ok, why = provider("lmstudio").ping()
    assert not ok and "LM Studio is not running" in why and "lms server start" in why


def test_hermes_ready_check_only_looks_at_the_port(http, monkeypatch):
    seen = []
    monkeypatch.setattr(providers, "_port_open", lambda url, timeout: seen.append((url, timeout)) or True)
    ok, why = provider("hermes").ping(10.0)
    assert ok and seen == [("http://127.0.0.1:8645/v1", 10.0)] and http.requests == []
    monkeypatch.setattr(providers, "_port_open", lambda url, timeout: False)
    ok, why = provider("hermes").ping()
    assert not ok and "hermes proxy start" in why


def test_hermes_agent_sends_its_optional_key(http, monkeypatch):
    monkeypatch.setenv("API_SERVER_KEY", "agent-key-1")
    http.answers = [openai_reply()]
    chat(provider("hermes-agent"))
    assert http.requests[0]["headers"]["authorization"] == "Bearer agent-key-1"


# ----- Anthropic -----------------------------------------------------------------------------------

def anthropic_reply(text="{\"question\": \"q\"}"):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def test_anthropic_request_shape(http, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123")
    http.answers = [anthropic_reply()]
    assert json.loads(chat(provider("anthropic"))) == EXERCISE
    req = http.requests[0]
    assert req["url"] == "https://api.anthropic.com/v1/messages" and req["method"] == "POST"
    assert req["headers"]["x-api-key"] == "sk-ant-abc123"
    assert req["headers"]["anthropic-version"] == providers.ANTHROPIC_VERSION
    assert "authorization" not in req["headers"]
    body = req["body"]
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 900
    assert body["temperature"] == 0.8 and "seed" not in body
    assert body["system"].startswith("You write exercises.")
    assert providers.SCHEMA_INTRO in body["system"]
    assert body["messages"] == [{"role": "user", "content": "zip, level 1"}]
    fmt = body["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False


def test_anthropic_without_output_config_support_is_asked_again_once(http, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123")
    http.answers = [http_error(400, b'{"error": "output_config: Extra inputs are not permitted"}'),
                    anthropic_reply()]
    chat(provider("anthropic"))
    assert "output_config" in http.requests[0]["body"]
    assert "output_config" not in http.requests[1]["body"]
    http.requests.clear()
    chat(provider("anthropic"))                       # remembered
    assert len(http.requests) == 1 and "output_config" not in http.requests[0]["body"]


def test_anthropic_errors(http, monkeypatch):
    with pytest.raises(ProviderError, match="pygym --set-key anthropic, or set ANTHROPIC_API_KEY"):
        chat(provider("anthropic"))
    assert http.requests == []
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123")
    http.answers = [http_error(401, b'{"error": {"message": "invalid x-api-key"}}')]
    with pytest.raises(ProviderError) as e:
        chat(provider("anthropic"))
    assert "Anthropic refused the API key (HTTP 401)" in str(e.value)
    assert "sk-ant-abc123" not in str(e.value)
    http.answers = [{"content": [], "stop_reason": "max_tokens"}]
    with pytest.raises(ProviderError, match="max_tokens"):
        chat(provider("anthropic"))


def test_anthropic_ping_and_check(http, monkeypatch):
    assert provider("anthropic").ping()[0] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-abc123")
    assert provider("anthropic").ping()[0] is True and http.requests == []
    http.answers = [{"data": [{"id": "claude-haiku-5-5"}]}]
    ok, why = provider("anthropic").check()
    assert ok and "accepts the key" in why
    assert http.requests[0]["url"].startswith("https://api.anthropic.com/v1/models")


# ----- 401 / 403 at the ready check (tonight's fix, now per provider) -----------------------------

def test_ready_check_explains_a_missing_or_refused_key_per_provider(http):
    http.answers = [http_error(401, b"no")]
    ok, why = provider("custom", "m", "https://llm.example.com/v1").ping()
    assert not ok and "needs an API key (HTTP 401)" in why
    assert "pygym --set-key openai-compatible" in why and "PYGYM_API_KEY" in why
    keys.save_key("openai-compatible", "wrong-key-55")
    ok, why = provider("custom", "m", "https://llm.example.com/v1").ping()
    assert not ok and "refused the API key (HTTP 401)" in why and "wrong-key-55" not in why


# ----- timeouts ------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, base_url, wait", [
    ("ollama", "", 0.5), ("lmstudio", "", 0.5), ("openai", "", 6.0), ("anthropic", "", 6.0),
    ("gemini", "", 6.0), ("custom", "https://llm.example.com/v1", 6.0),
    ("custom", "http://localhost:8080/v1", 0.5), ("ollama", "http://192.168.1.5:11434", 6.0),
    ("hermes", "", 10.0), ("hermes-agent", "", 10.0),
])
def test_ping_timeout_per_provider(name, base_url, wait):
    p = provider(name, "m", base_url)
    assert providers.ping_timeout(p.base_url, p.spec if hasattr(p, "spec") else None) == wait
    assert providers.ping_timeout(p.base_url, registry.get_spec(name)) == wait


# ----- Claude Code (fake subprocess, `claude` never runs) ------------------------------------------

class FakePopen:
    runs = []
    reply = ("", "", 0)

    def __init__(self, cmd, **kw):
        self.cmd, self.kw = cmd, kw
        self.pid = 4242
        self.returncode = None
        FakePopen.runs.append(self)

    def communicate(self, data=None, timeout=None):
        self.stdin_data, self.timeout = data, timeout
        out, err, code = FakePopen.reply
        if isinstance(out, BaseException):
            raise out
        self.returncode = code
        return out, err


@pytest.fixture
def claude(monkeypatch):
    FakePopen.runs = []
    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/usr/bin/claude")
    return FakePopen


def test_claude_code_command_and_environment(claude, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-billing")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-other")
    monkeypatch.setenv("PYGYM_API_KEY", "sk-pygym")
    claude.reply = (json.dumps({"is_error": False, "structured_output": EXERCISE}), "", 0)
    p = provider("claude-code")
    assert json.loads(chat(p, timeout=30)) == EXERCISE
    run = claude.runs[0]
    cmd = run.cmd
    assert cmd[:2] == ["/usr/bin/claude", "-p"]
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    assert cmd[cmd.index("--tools") + 1] == "" and "--no-session-persistence" in cmd
    assert "--bare" not in cmd
    assert cmd[cmd.index("--system-prompt") + 1] == "You write exercises."
    assert run.stdin_data == "zip, level 1" and run.timeout == 30
    env = run.kw["env"]
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "PYGYM_API_KEY"):
        assert name not in env
    assert run.kw["cwd"] != "." and "pygym-claude-" in run.kw["cwd"]


def test_claude_code_errors(claude):
    p = provider("claude")
    claude.reply = (json.dumps({"is_error": True, "result": "usage limit reached"}), "", 1)
    with pytest.raises(ProviderError, match="usage limit reached"):
        chat(p)
    claude.reply = ("not json", "boom\nlast line", 1)
    with pytest.raises(ProviderError, match="last line"):
        chat(p)
    claude.reply = (json.dumps({"result": "```json\n{\"question\": \"q\"}\n```"}), "", 0)
    assert json.loads(chat(p)) == EXERCISE                # no structured_output: the text


def test_claude_code_that_takes_too_long_is_killed(claude, monkeypatch):
    killed = []
    monkeypatch.setattr(providers, "_kill", killed.append)

    class Slow(FakePopen):
        def communicate(self, data=None, timeout=None):
            if not killed:
                raise subprocess.TimeoutExpired("claude", timeout)
            return "", ""
    monkeypatch.setattr(subprocess, "Popen", Slow)
    with pytest.raises(ProviderError, match="did not answer in time") as e:
        chat(provider("claude-code"), timeout=3)
    assert e.value.timed_out and len(killed) == 1


def test_claude_code_not_installed(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda name: None)
    p = provider("claude-code")
    ok, why = p.ping()
    assert not ok and "not installed" in why
    with pytest.raises(ProviderError, match="not installed"):
        chat(p)


def test_claude_code_login_check_is_remembered(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/usr/bin/claude")
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"loggedIn": True}), "")
    monkeypatch.setattr(subprocess, "run", run)
    p = provider("claude-code")
    assert p.ping() == (True, "Claude Code is ready (Claude plan) with sonnet")
    assert p.ping()[0] is True and len(calls) == 1
    assert calls[0][0] == ["/usr/bin/claude", "auth", "status"]
    assert "ANTHROPIC_API_KEY" not in calls[0][1]["env"]


def test_claude_code_not_logged_in(monkeypatch):
    monkeypatch.setattr(providers.shutil, "which", lambda name: "/opt/claude")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, json.dumps({"loggedIn": False}), ""))
    ok, why = provider("claude-code").ping()
    assert not ok and "not logged in" in why


def test_claude_prompt_writes_out_a_repair_conversation():
    system, prompt = providers.claude_prompt(MESSAGES + [
        {"role": "assistant", "content": "{bad"}, {"role": "user", "content": "fix it"}])
    assert system == "You write exercises."
    assert "YOUR EARLIER REPLY" in prompt and prompt.rstrip().endswith("fix it")


def test_make_provider_defaults():
    p = make_provider(PROVIDERS["gemini"])
    assert (p.model, p.base_url) == (PROVIDERS["gemini"].model, PROVIDERS["gemini"].base_url)


def test_openrouter_check_tests_the_key_on_its_key_endpoint(http, monkeypatch):
    # OpenRouter's /models answers without a key, so a made-up key looked fine; /key refuses it
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-made-up")
    http.answers = [http_error(401, b'{"error": "No auth credentials found"}')]
    ok, why = provider("openrouter").check(6.0)
    assert ok is False and "sk-made-up" not in why
    assert http.requests[0]["url"] == "https://openrouter.ai/api/v1/key"
    http.requests.clear()
    http.answers = [{"data": {"label": "x"}}, {"data": [{"id": "openrouter/auto"}]}]
    ok, why = provider("openrouter").check(6.0)
    assert ok is True and [r["url"].rsplit("/", 1)[1] for r in http.requests] == ["key", "models"]
