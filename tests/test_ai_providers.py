"""ai.providers: request bodies and error handling with a fake urlopen (no network)."""
import io
import json
import urllib.error
import urllib.request

import pytest

from pygym import config
from pygym.ai import providers
from pygym.ai.prompt import SCHEMA
from pygym.ai.providers import (FakeProvider, OllamaProvider, OpenAICompatProvider,
                                ProviderError, get_provider)

MESSAGES = [{"role": "user", "content": "hi"}]


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture
def http(monkeypatch):
    """Replace urlopen. http.answer = dict or exception; http.requests = what was sent."""
    class State:
        answer = {}
        requests = []
    state = State()

    def urlopen(req, timeout=None):
        body = json.loads(req.data.decode()) if req.data else None
        state.requests.append({"url": req.full_url, "method": req.get_method(), "body": body,
                               "headers": {k.lower(): v for k, v in req.header_items()},
                               "timeout": timeout})
        if isinstance(state.answer, Exception):
            raise state.answer
        return FakeResponse(json.dumps(state.answer).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return state


def chat(p):
    return p.chat(MESSAGES, SCHEMA, temperature=0.8, max_tokens=700, seed=42, timeout=7.0)


def test_ollama_request_body(http):
    http.answer = {"message": {"role": "assistant", "content": "{\"a\": 1}"}}
    p = OllamaProvider("qwen2.5:1.5b", "http://localhost:11434/")
    assert chat(p) == "{\"a\": 1}"
    req = http.requests[0]
    assert req["url"] == "http://localhost:11434/api/chat" and req["method"] == "POST"
    body = req["body"]
    assert body["model"] == "qwen2.5:1.5b" and body["stream"] is False
    assert body["format"] == SCHEMA and body["messages"] == MESSAGES
    assert body["options"] == {"num_predict": 700, "temperature": 0.8, "seed": 42}
    assert req["timeout"] == 7.0


def test_ollama_errors(http):
    p = OllamaProvider("qwen2.5:1.5b", "http://localhost:11434")
    http.answer = urllib.error.URLError(ConnectionRefusedError(111, "refused"))
    with pytest.raises(ProviderError, match="not running at http://localhost:11434"):
        chat(p)
    http.answer = urllib.error.HTTPError("u", 404, "nf", {},
                                         io.BytesIO(b'{"error":"model \'x\' not found"}'))
    with pytest.raises(ProviderError, match="ollama pull qwen2.5:1.5b"):
        chat(p)
    http.answer = TimeoutError("timed out")
    with pytest.raises(ProviderError):
        chat(p)
    http.answer = {"no": "message"}
    with pytest.raises(ProviderError):
        chat(p)


def test_ollama_ping(http):
    p = OllamaProvider("qwen2.5:1.5b", "http://localhost:11434")
    http.answer = {"models": [{"name": "qwen2.5:1.5b"}, {"name": "llama3.2:3b"}]}
    assert p.ping() == (True, "Ollama is ready with qwen2.5:1.5b")
    assert http.requests[0]["url"].endswith("/api/tags") and http.requests[0]["timeout"] == 0.5
    http.answer = {"models": [{"name": "llama3.2:3b"}]}
    ok, why = p.ping()
    assert not ok and "ollama pull qwen2.5:1.5b" in why
    http.answer = urllib.error.URLError("refused")
    assert p.ping() == (False, "Ollama is not running at http://localhost:11434")
    assert OllamaProvider("llama3.2", "http://x").ping() == (False, "Ollama is not running at http://x")


def test_openai_request_body_and_key_from_environment(http, monkeypatch):
    monkeypatch.setenv("PYGYM_API_KEY", "secret-123")
    http.answer = {"choices": [{"message": {"content": "{}"}}]}
    p = OpenAICompatProvider("local-model", "http://localhost:1234/v1")
    assert chat(p) == "{}"
    req = http.requests[0]
    assert req["url"] == "http://localhost:1234/v1/chat/completions"
    assert req["headers"]["authorization"] == "Bearer secret-123"
    body = req["body"]
    assert body["model"] == "local-model" and body["max_tokens"] == 700 and body["seed"] == 42
    assert body["response_format"] == {"type": "json_schema",
                                       "json_schema": {"name": "exercise", "schema": SCHEMA}}


def test_openai_without_key_sends_no_authorization(http, monkeypatch):
    monkeypatch.delenv("PYGYM_API_KEY", raising=False)
    http.answer = {"choices": [{"message": {"content": "{}"}}]}
    chat(OpenAICompatProvider("m", "http://localhost:1234/v1"))
    assert "authorization" not in http.requests[0]["headers"]


def test_openai_errors_and_ping(http):
    p = OpenAICompatProvider("m", "http://localhost:1234/v1", api_key="k")
    http.answer = {"choices": []}
    with pytest.raises(ProviderError):
        chat(p)
    http.answer = urllib.error.HTTPError("u", 401, "no", {}, io.BytesIO(b"bad key"))
    with pytest.raises(ProviderError, match="HTTP 401"):
        chat(p)
    http.answer = {"data": [{"id": "m"}]}
    assert p.ping()[0] is True
    http.answer = {"data": [{"id": "other"}]}
    assert p.ping()[0] is False


def test_api_key_is_never_saved_in_config():
    assert "api_key" not in {f for f in vars(config.Settings())}


def test_get_provider():
    s = config.Settings()
    s.provider, s.model, s.base_url = "ollama", "qwen2.5:1.5b", "http://localhost:11434"
    p = get_provider(s)
    assert isinstance(p, OllamaProvider) and (p.name, p.model) == ("ollama", "qwen2.5:1.5b")
    s.provider, s.base_url = "openai", "http://localhost:1234/v1"
    assert isinstance(get_provider(s), OpenAICompatProvider)
    s.provider = "cloud-magic"
    with pytest.raises(ProviderError, match="unknown AI provider"):
        get_provider(s)


def test_fake_provider_queue():
    f = FakeProvider(["a", ProviderError("down"), lambda msgs: msgs[0]["content"]], ok=False,
                     reason="off")
    assert f.ping() == (False, "off")
    assert chat(f) == "a"
    with pytest.raises(ProviderError, match="down"):
        chat(f)
    assert chat(f) == "hi"
    with pytest.raises(ProviderError):
        chat(f)
    assert len(f.calls) == 4 and f.calls[0]["seed"] == 42
    assert providers.FakeProvider().name == "fake"
