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
    # first /api/show (does the model think?), then the chat itself
    assert [r["url"] for r in http.requests] == ["http://localhost:11434/api/show",
                                                 "http://localhost:11434/api/chat"]
    req = http.requests[1]
    assert req["method"] == "POST"
    body = req["body"]
    assert body["model"] == "qwen2.5:1.5b" and body["stream"] is False
    assert body["format"] == SCHEMA and body["messages"] == MESSAGES
    assert body["options"] == {"num_predict": 700, "temperature": 0.8, "seed": 42,
                               "num_ctx": OllamaProvider.NUM_CTX}
    assert "think" not in body  # the answer to /api/show listed no "thinking" capability
    assert req["timeout"] == pytest.approx(7.0, abs=0.5)  # minus the time /api/show took


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


# ----- thinking models: "think": false ----------------------------------------------------------

@pytest.fixture
def routes(monkeypatch):
    """A fake Ollama: routes[path] = a dict, an exception, or a list of them (one per request).
    routes.requests = (path, body) of every request, in order."""
    class Routes(dict):
        requests = []
    state = Routes()

    def urlopen(req, timeout=None):
        path = req.full_url.split("11434", 1)[1]
        body = json.loads(req.data.decode()) if req.data else None
        state.requests.append((path, body))
        answer = state[path]
        if isinstance(answer, list):
            answer = answer.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(json.dumps(answer).encode())

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return state


REPLY = {"message": {"role": "assistant", "content": "{}"}}
THINKING = {"capabilities": ["completion", "vision", "tools", "thinking"]}
PLAIN = {"capabilities": ["completion", "tools"]}


def chats(routes):
    return [body for path, body in routes.requests if path == "/api/chat"]


def http_error(code, text):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(text.encode()))


def test_think_false_is_sent_to_a_thinking_model_only(routes):
    routes.update({"/api/show": THINKING, "/api/chat": REPLY})
    assert chat(OllamaProvider("qwen3.5:2b")) == "{}"
    assert chats(routes)[0]["think"] is False
    assert routes.requests[0] == ("/api/show", {"model": "qwen3.5:2b"})
    routes.requests.clear()
    routes["/api/show"] = PLAIN
    chat(OllamaProvider("qwen2.5:1.5b"))
    assert "think" not in chats(routes)[0]


def test_the_capability_lookup_is_done_once_per_model(routes):
    routes.update({"/api/show": THINKING, "/api/chat": REPLY})
    for _ in range(3):
        chat(OllamaProvider("qwen3.5:2b"))  # a new provider object every time, like the engine
    shows = [body for path, body in routes.requests if path == "/api/show"]
    assert shows == [{"model": "qwen3.5:2b"}]
    assert [b["think"] for b in chats(routes)] == [False, False, False]
    routes["/api/show"] = PLAIN
    chat(OllamaProvider("llama3.2:3b"))            # another model: asked once more
    assert [b for p, b in routes.requests if p == "/api/show"][-1] == {"model": "llama3.2:3b"}
    assert providers._thinking == {("http://localhost:11434", "qwen3.5:2b"): True,
                                   ("http://localhost:11434", "llama3.2:3b"): False}


def test_a_server_that_refuses_think_gets_the_request_again_without_it(routes):
    refused = http_error(400, '{"error":"model does not support thinking"}')
    routes.update({"/api/show": THINKING, "/api/chat": [refused, REPLY, REPLY]})
    p = OllamaProvider("m")
    assert chat(p) == "{}"
    first, second = chats(routes)
    assert first["think"] is False and "think" not in second
    assert chat(p) == "{}"                         # remembered: no third try with "think"
    assert len(chats(routes)) == 3 and "think" not in chats(routes)[2]


def test_other_http_400s_are_not_retried(routes):
    routes.update({"/api/show": THINKING,
                   "/api/chat": [http_error(400, '{"error":"invalid format"}'), REPLY]})
    with pytest.raises(ProviderError, match="invalid format"):
        chat(OllamaProvider("m"))
    assert len(chats(routes)) == 1


def test_an_unanswered_lookup_sends_no_think_and_is_asked_again(routes):
    routes.update({"/api/show": [TimeoutError("slow"), {"no": "capabilities"}],
                   "/api/chat": REPLY})
    chat(OllamaProvider("old"))
    assert "think" not in chats(routes)[0]
    assert ("http://localhost:11434", "old") not in providers._thinking
    chat(OllamaProvider("old"))                    # an old server: no "capabilities" at all
    assert providers._thinking[("http://localhost:11434", "old")] is False
    assert [p for p, _ in routes.requests].count("/api/show") == 2


def test_a_missing_model_is_reported_by_the_lookup(routes):
    routes["/api/show"] = http_error(404, '{"error":"model nope not found"}')
    with pytest.raises(ProviderError, match="ollama pull nope"):
        chat(OllamaProvider("nope"))
    assert chats(routes) == []


def test_the_openai_provider_never_sends_think(http):
    http.answer = {"choices": [{"message": {"content": "{}"}}]}
    chat(OpenAICompatProvider("qwen3.5:2b", "http://localhost:1234/v1"))
    assert len(http.requests) == 1 and "think" not in http.requests[0]["body"]
