"""LLM providers. Network code uses the standard library (urllib) only.

  OllamaProvider        a local Ollama server (default), POST /api/chat with a JSON schema
  OpenAICompatProvider  any OpenAI-compatible server (LM Studio, llama.cpp, vLLM, a hosted API)
  FakeProvider          queued replies for tests; never touches the network

Only the prompt (the target's name and documentation) is sent, never the student's code.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Sequence, Tuple, Union

API_KEY_ENV = "PYGYM_API_KEY"


class ProviderError(Exception):
    """The provider could not answer (server down, HTTP error, bad reply...)."""


class Provider:
    name = "provider"
    model = ""

    def ping(self, timeout: float = 0.5) -> Tuple[bool, str]:  # pragma: no cover - interface
        raise NotImplementedError

    def chat(self, messages: List[dict], schema: dict, *, temperature: float, max_tokens: int,
             seed: Optional[int], timeout: float) -> str:  # pragma: no cover - interface
        raise NotImplementedError


def _http(method: str, url: str, body: Optional[dict], headers: dict, timeout: float) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=max(timeout, 0.1)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            detail = ""
        raise ProviderError(f"HTTP {e.code} from {url}: {detail}".strip()) from e
    except urllib.error.URLError as e:
        raise ProviderError(f"can't reach {url} ({e.reason})") from e
    except (OSError, ValueError) as e:  # timeouts, resets, bad JSON
        raise ProviderError(f"no usable answer from {url} ({type(e).__name__})") from e


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def ping(self, timeout: float = 0.5) -> Tuple[bool, str]:
        try:
            tags = _http("GET", f"{self.base_url}/api/tags", None, {}, timeout)
        except ProviderError:
            return False, f"Ollama is not running at {self.base_url}"
        except Exception:  # noqa: BLE001
            return False, f"Ollama is not running at {self.base_url}"
        names = {m.get("name", "") for m in tags.get("models", []) if isinstance(m, dict)}
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in names and self.model not in names:
            return False, f"model '{self.model}' is not installed (run: ollama pull {self.model})"
        return True, f"Ollama is ready with {self.model}"

    def chat(self, messages, schema, *, temperature, max_tokens, seed, timeout) -> str:
        options = {"num_predict": max_tokens, "temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        body = {"model": self.model, "stream": False, "format": schema, "options": options,
                "keep_alive": "10m", "messages": messages}
        try:
            reply = _http("POST", f"{self.base_url}/api/chat", body, {}, timeout)
        except ProviderError as e:
            text = str(e)
            if "HTTP 404" in text and "not found" in text:
                raise ProviderError(f"model '{self.model}' is not installed "
                                    f"(run: ollama pull {self.model})") from e
            if "can't reach" in text:
                raise ProviderError(f"Ollama is not running at {self.base_url}") from e
            raise
        try:
            return reply["message"]["content"]
        except (KeyError, TypeError) as e:
            raise ProviderError("Ollama sent an answer without a message") from e


class OpenAICompatProvider(Provider):
    """POST {base_url}/chat/completions. base_url usually ends in /v1.

    The API key comes from the PYGYM_API_KEY environment variable (never from config.json).
    """
    name = "openai"

    def __init__(self, model: str, base_url: str, api_key: Optional[str] = None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def ping(self, timeout: float = 0.5) -> Tuple[bool, str]:
        try:
            listing = _http("GET", f"{self.base_url}/models", None, self._headers(), timeout)
        except Exception:  # noqa: BLE001
            return False, f"no OpenAI-compatible server at {self.base_url}"
        ids = {m.get("id") for m in listing.get("data", []) if isinstance(m, dict)} \
            if isinstance(listing, dict) else set()
        if ids and self.model not in ids:
            return False, f"model '{self.model}' is not offered by {self.base_url}"
        return True, f"{self.base_url} is ready with {self.model}"

    def chat(self, messages, schema, *, temperature, max_tokens, seed, timeout) -> str:
        body = {"model": self.model, "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_schema",
                                    "json_schema": {"name": "exercise", "schema": schema}}}
        if seed is not None:
            body["seed"] = seed
        reply = _http("POST", f"{self.base_url}/chat/completions", body, self._headers(), timeout)
        try:
            return reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError("the server sent an answer without a message") from e


Reply = Union[str, Exception, Callable[[List[dict]], str]]


class FakeProvider(Provider):
    """For tests: returns queued replies in order (a string, an exception to raise, or a
    function of the messages). Records every chat() call in `.calls`."""
    name = "fake"

    def __init__(self, replies: Sequence[Reply] = (), ok: bool = True, reason: str = "fake",
                 model: str = "fake"):
        self.replies = list(replies)
        self.ok = ok
        self.reason = reason
        self.model = model
        self.calls: List[dict] = []

    def ping(self, timeout: float = 0.5) -> Tuple[bool, str]:
        return self.ok, self.reason

    def chat(self, messages, schema, *, temperature, max_tokens, seed, timeout) -> str:
        self.calls.append({"messages": messages, "schema": schema, "temperature": temperature,
                           "max_tokens": max_tokens, "seed": seed, "timeout": timeout})
        if not self.replies:
            raise ProviderError("the fake provider has no more replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply(messages) if callable(reply) else reply


def get_provider(settings) -> Provider:
    """The provider named in the settings. Raises ProviderError for an unknown name."""
    kind = (getattr(settings, "provider", "") or "").strip().lower()
    model = getattr(settings, "model", "") or ""
    base_url = getattr(settings, "base_url", "") or ""
    if kind == "ollama":
        return OllamaProvider(model, base_url or "http://localhost:11434")
    if kind in ("openai", "openai-compatible", "openai_compat"):
        if not base_url:
            raise ProviderError("the openai provider needs a base_url")
        return OpenAICompatProvider(model, base_url)
    raise ProviderError(f"unknown AI provider '{kind}' (use 'ollama' or 'openai')")
