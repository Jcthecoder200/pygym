"""LLM providers. Network code uses the standard library (urllib) only.

  OllamaProvider        a local Ollama server (default), POST /api/chat with a JSON schema
  OpenAICompatProvider  any OpenAI-compatible server: OpenAI, Gemini, OpenRouter, Groq, LM Studio,
                        the Hermes proxy and API server, llama.cpp, vLLM, Ollama's /v1 ...
  AnthropicProvider     the Anthropic Messages API (POST /v1/messages)
  ClaudeCodeProvider    the student's Claude plan through the Claude Code CLI (`claude -p`)
  FakeProvider          queued replies for tests; never touches the network

Every provider has the same two methods: ping(timeout) -> (ok, message) and chat(...) -> the raw
JSON text of the reply. The engine, the validator, the cache and prefetch do not know which
provider answered. The providers, their addresses and default models are in registry.py; API
keys are read only by keys.get_key.

Only the prompt (the target's name and documentation) is sent, never the student's code.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple, Union
from urllib.parse import urlparse

from . import keys
from .registry import (CLAUDE_CODE_MODELS, PROVIDERS, ProviderSpec, UnknownProvider,
                       get_spec, key_env_names, resolve)

API_KEY_ENV = keys.FALLBACK_ENV   # "PYGYM_API_KEY" (pygym 0.2 name, still read by keys.get_key)
DETAIL_LIMIT = 300                # characters of a server's error reply kept in a message
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")
LOCAL_PING_TIMEOUT = 0.5    # seconds: a server on this computer answers at once
REMOTE_PING_TIMEOUT = 6.0   # a cloud API took 2.6-4.6 s for GET /models (measured from Hong Kong)


def is_local(base_url: str) -> bool:
    """True if the address is this computer (localhost, 127.0.0.1, ::1)."""
    try:
        host = (urllib.parse.urlparse(base_url or "").hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS


def ping_timeout(base_url: str, spec: Optional[ProviderSpec] = None) -> float:
    """How long the "is AI ready?" check waits. 0.5 s was too short for every cloud API, so AI
    mode silently fell back to the hand-written exercises there. A server on this computer gets
    0.5 s, any other address 6 s; a provider whose spec sets a longer check_timeout (the Hermes
    servers: 10 s) gets that."""
    wait = LOCAL_PING_TIMEOUT if is_local(base_url) else REMOTE_PING_TIMEOUT
    if spec is not None and spec.check_timeout > wait:
        wait = spec.check_timeout
    return wait


class ProviderError(Exception):
    """The provider could not answer (server down, HTTP error, bad reply...).

    status: the HTTP status (None when there was none), detail: the server's reply (cut, with
    the key hidden), unreachable / timed_out: no answer at all."""

    def __init__(self, message: str = "", *, status: Optional[int] = None, detail: str = "",
                 unreachable: bool = False, timed_out: bool = False):
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.unreachable = unreachable
        self.timed_out = timed_out


class Provider:
    name = "provider"
    model = ""

    def ping(self, timeout: float = 0.5) -> Tuple[bool, str]:  # pragma: no cover - interface
        raise NotImplementedError

    def chat(self, messages: List[dict], schema: dict, *, temperature: float, max_tokens: int,
             seed: Optional[int], timeout: float) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def check(self, timeout: float = 5.0) -> Tuple[bool, str]:
        """A live check (it may call the network; never a model call). Default: ping."""
        return self.ping(timeout)

    def list_models(self, timeout: float = 5.0) -> List[str]:
        """The models the server offers ([] when it has no list). Raises ProviderError."""
        return []


def _http(method: str, url: str, body: Optional[dict], headers: dict, timeout: float,
          secret: Optional[str] = None) -> dict:
    """One JSON request. Raises ProviderError; its message and .detail never hold `secret`."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=max(timeout, 0.1)) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            detail = ""
        detail = keys.redact(detail, secret)[:DETAIL_LIMIT]   # hidden first, then cut
        raise ProviderError(f"HTTP {e.code} from {url}: {detail}".strip(), status=e.code,
                            detail=detail) from e
    except urllib.error.URLError as e:
        slow = isinstance(e.reason, (socket.timeout, TimeoutError))
        raise ProviderError(f"can't reach {url} ({e.reason})", unreachable=not slow,
                            timed_out=slow) from e
    except (socket.timeout, TimeoutError) as e:
        raise ProviderError(f"no usable answer from {url} ({type(e).__name__})",
                            timed_out=True) from e
    except (OSError, ValueError) as e:  # resets, bad JSON
        raise ProviderError(f"no usable answer from {url} ({type(e).__name__})") from e


def _port_open(url: str, timeout: float) -> bool:
    """Something listens at the URL's host and port (no HTTP request)."""
    try:
        parts = urlparse(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        with socket.create_connection((parts.hostname or "localhost", port),
                                      timeout=max(timeout, 0.05)):
            return True
    except (OSError, ValueError):
        return False


# ----- replies ---------------------------------------------------------------------------------

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


def _parses(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (TypeError, ValueError):
        return False


def clean_reply(text: str) -> str:
    """The JSON object inside a model's reply. A reply that is JSON already is returned as it
    is; otherwise <think>...</think>, ```json fences and text around the outermost {...} are
    removed. When nothing parses the reply is returned unchanged (the validator says why)."""
    if not isinstance(text, str) or _parses(text):
        return text
    body = _THINK.sub("", text)
    if "</think>" in body.lower():                       # an opening tag the server cut off
        body = body[body.lower().rindex("</think>") + len("</think>"):]
    body = body.strip()
    candidates = [body]
    fence = _FENCE.search(body)
    if fence:
        candidates.append(fence.group(1).strip())
    start, end = body.find("{"), body.rfind("}")
    if 0 <= start < end:
        candidates.append(body[start:end + 1])
    for candidate in candidates:
        if _parses(candidate):
            return candidate
    return text


SCHEMA_INTRO = "The reply is ONE JSON object that follows this JSON Schema:"


def with_schema(messages: List[dict], schema: dict) -> List[dict]:
    """The messages with the JSON schema added to the system message (many models ignore the
    API's schema field). Not added twice; a copy, the input is not changed."""
    text = json.dumps(schema, separators=(",", ":"))
    if any(text in str(m.get("content", "")) for m in messages):
        return list(messages)
    out = [dict(m) for m in messages]
    if out and out[0].get("role") == "system":
        out[0]["content"] = f"{out[0].get('content', '')}\n\n{SCHEMA_INTRO}\n{text}"
    else:
        out.insert(0, {"role": "system", "content": f"{SCHEMA_INTRO}\n{text}"})
    return out


# ----- clear messages for HTTP failures --------------------------------------------------------

_CREDIT_WORDS = ("quota", "credit", "billing", "rate limit", "rate_limit", "ratelimit",
                 "payment", "insufficient_funds", "resource_exhausted", "too many requests")


def key_fix(spec: ProviderSpec) -> str:
    """How to give a provider its key: 'pygym --set-key openai, or set OPENAI_API_KEY'."""
    if not spec.accepts_key:
        return (f"pygym sends no key to {spec.name}; for a server that wants one use "
                "pygym --set-provider openai-compatible")
    env = " or ".join(spec.key_env) if spec.key_env else keys.FALLBACK_ENV
    return f"pygym --set-key {spec.name}, or set {env}"


def no_key_message(spec: ProviderSpec) -> str:
    return f"no API key for {spec.title}: {key_fix(spec)}"


def explain(error: ProviderError, spec: ProviderSpec, base_url: str, model: str,
            has_key: bool = True) -> ProviderError:
    """A ProviderError from _http -> one clear line for the student (the same class, so the
    engine treats it the same way). has_key: a key was sent (401/403 then means "refused",
    otherwise "needed")."""
    text = (error.detail or "").lower()
    title = spec.title
    status = error.status
    if error.timed_out:
        msg = f"{title} did not answer in time"
    elif error.unreachable:
        if spec.local:
            msg = f"{title} is not running at {base_url}"
            if spec.start_hint:
                msg += f": {spec.start_hint}"
        else:
            msg = f"can't reach {title} at {base_url} (is the internet connection working?)"
    elif status is None:
        msg = f"{title} sent an answer pygym could not read"
    elif status in (402, 429) or any(word in text for word in _CREDIT_WORDS):
        msg = f"rate limit or no credits left at {title} (HTTP {status})"
        if spec.name == "hermes":
            msg += "; pick a free model (its name ends in :free) with pygym --set-model"
    elif status in (401, 403):     # the server is there; the key is missing or not ok
        if has_key:
            msg = f"{title} refused the API key (HTTP {status}); give the right one: {key_fix(spec)}"
        else:
            msg = f"{title} at {base_url} needs an API key (HTTP {status}): {key_fix(spec)}"
    elif status == 404 and "model" in text:
        msg = (f"model '{model}' is not available at {title} "
               f"(choose another: pygym --set-model NAME)")
    elif status == 404:
        msg = (f"{title} has nothing at {base_url} (HTTP 404); check the address "
               f"(pygym --set-base-url URL)")
    elif status >= 500:
        msg = f"{title} had a problem on its side (HTTP {status}); try again later"
    else:
        msg = f"{title} refused the request (HTTP {status}): {error.detail}".rstrip(": ")
    return ProviderError(msg, status=status, detail=error.detail,
                         unreachable=error.unreachable, timed_out=error.timed_out)


# ----- Ollama ----------------------------------------------------------------------------------

# Does a model "think" before answering (qwen3, qwen3.5, deepseek-r1...)? Asked once per server
# and model per process with /api/show; True / False. Missing = not known yet.
_thinking: Dict[Tuple[str, str], bool] = {}


def _think_error(error: ProviderError) -> bool:
    """An HTTP 400 that complains about the "think" field (a server or model without it)."""
    text = str(error)
    return "HTTP 400" in text and "think" in text.lower()


class OllamaProvider(Provider):
    """A thinking model spends its whole budget thinking unless told not to, so chat() sends
    "think": false to models whose /api/show capabilities include "thinking". Other models get
    no "think" field at all (Ollama 0.18 accepts false for them too, but an older server may
    not). If the server still answers HTTP 400 about "think", the call is repeated once without
    it and the model is remembered as non-thinking.

    Every request sets the context size (options.num_ctx = NUM_CTX). Ollama 0.18.1 loads
    qwen2.5:1.5b with a 4096-token context by default (/api/ps "context_length") and cuts a
    longer prompt from the START without an error, which would drop the system message and
    the rules. The exercise prompt with its examples is about 1,600-2,600 tokens (measured
    with qwen2.5:1.5b), up to about 3,200 with a repair, plus up to engine.MAX_TOKENS for the
    answer: close to 4096, so 8192 leaves room. The same
    value goes with every call, so Ollama does not reload the model between calls."""
    name = "ollama"
    NUM_CTX = 8192

    def __init__(self, model: str, base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url.rstrip("/")

    def thinks(self, timeout: float = 2.0) -> Optional[bool]:
        """True if the model can think (and so needs "think": false), False if not, None when
        the server could not be asked (not remembered: asked again next time). Raises
        ProviderError only when the server says the model is not installed."""
        key = (self.base_url, self.model)
        if key in _thinking:
            return _thinking[key]
        try:
            info = _http("POST", f"{self.base_url}/api/show", {"model": self.model}, {},
                         min(timeout, 2.0))
        except ProviderError as e:
            if "HTTP 404" in str(e) and "not found" in str(e):
                raise ProviderError(f"model '{self.model}' is not installed "
                                    f"(run: ollama pull {self.model})") from e
            return None  # chat() reports the real problem
        except Exception:  # noqa: BLE001
            return None
        caps = info.get("capabilities") if isinstance(info, dict) else None
        # A server without "capabilities" (older than Ollama 0.6) also predates thinking models.
        _thinking[key] = isinstance(caps, list) and "thinking" in caps
        return _thinking[key]

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

    def list_models(self, timeout: float = 5.0) -> List[str]:
        try:
            tags = _http("GET", f"{self.base_url}/api/tags", None, {}, timeout)
        except ProviderError as e:
            raise explain(e, PROVIDERS["ollama"], self.base_url, self.model) from e
        return sorted(m.get("name", "") for m in tags.get("models", [])
                      if isinstance(m, dict) and m.get("name"))

    def chat(self, messages, schema, *, temperature, max_tokens, seed, timeout) -> str:
        options = {"num_predict": max_tokens, "temperature": temperature,
                   "num_ctx": self.NUM_CTX}
        if seed is not None:
            options["seed"] = seed
        body = {"model": self.model, "stream": False, "format": schema, "options": options,
                "keep_alive": "10m", "messages": messages}
        started = time.monotonic()
        if self.thinks(timeout):
            body["think"] = False
        timeout = max(0.1, timeout - (time.monotonic() - started))  # /api/show is in the budget
        try:
            try:
                reply = _http("POST", f"{self.base_url}/api/chat", body, {}, timeout)
            except ProviderError as e:
                if "think" not in body or not _think_error(e):
                    raise
                _thinking[(self.base_url, self.model)] = False
                del body["think"]
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


# ----- OpenAI-compatible servers ---------------------------------------------------------------

# Request fields a server refused (HTTP 400/422 naming the field), per (base_url, model) for the
# rest of the process: "max_completion_tokens" (newer OpenAI models reject max_tokens),
# "no_temperature", "no_seed", "json_object" (no json_schema), "no_response_format".
_quirks: Dict[Tuple[str, str], Set[str]] = {}
# The first model a server lists, for providers without a default model (LM Studio).
_first_models: Dict[str, str] = {}


def _quirk_for(error: ProviderError, active: Set[str]) -> Optional[str]:
    """The request change a 400/422 asks for, or None (not a field we can drop or rename)."""
    if error.status not in (400, 422):
        return None
    text = (error.detail or "").lower()
    if "max_tokens" in text and "max_completion_tokens" not in active:
        return "max_completion_tokens"
    if "temperature" in text and "no_temperature" not in active:
        return "no_temperature"
    if re.search(r"\bseed\b", text) and "no_seed" not in active:
        return "no_seed"
    if any(word in text for word in ("response_format", "json_schema", "json schema")):
        if "json_object" not in active:
            return "json_object"
        if "no_response_format" not in active:
            return "no_response_format"
    return None


def _ids(listing) -> List[str]:
    if not isinstance(listing, dict) or not isinstance(listing.get("data"), list):
        return []
    found = []
    for item in listing["data"]:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            mid = item["id"]
            found.append(mid[len("models/"):] if mid.startswith("models/") else mid)  # Gemini
    return found


class OpenAICompatProvider(Provider):
    """POST {base_url}/chat/completions. base_url usually ends in /v1.

    The API key comes from keys.get_key (environment variable or keys.json), never from
    config.json. A field the server refuses is dropped or renamed once and remembered (_quirks).
    The JSON schema also goes into the system message: many models ignore response_format.
    """

    def __init__(self, model: str, base_url: str, api_key: Optional[str] = None, *,
                 spec: Optional[ProviderSpec] = None):
        self.spec = spec or PROVIDERS["openai-compatible"]
        self.name = self.spec.name
        self.model = model
        self.base_url = (base_url or "").rstrip("/")
        if api_key is None and self.spec.accepts_key:
            api_key, _source = keys.get_key(self.spec.name)
        self._key = api_key or None

    def __repr__(self) -> str:   # never the key
        return f"OpenAICompatProvider({self.name!r}, {self.model!r}, {self.base_url!r})"

    def _headers(self) -> dict:
        bearer = self._key or self.spec.dummy_key
        return {"Authorization": f"Bearer {bearer}"} if bearer else {}

    def _explain(self, error: ProviderError) -> ProviderError:
        return explain(error, self.spec, self.base_url, self.model, has_key=bool(self._key))

    def _missing(self) -> Optional[str]:
        """Why chat() can not even start: no address, no key, no model."""
        if not self.base_url:
            return (f"{self.spec.title} needs an address "
                    f"(pygym --set-base-url http://host:port/v1)")
        if self.spec.key_required and not self._key:
            return no_key_message(self.spec)
        if not self.model and self.spec.model_required:
            return f"no model chosen for {self.spec.title} (pygym --set-model NAME)"
        return None

    def list_models(self, timeout: float = 5.0) -> List[str]:
        try:
            listing = _http("GET", f"{self.base_url}/models", None, self._headers(), timeout,
                            secret=self._key)
        except ProviderError as e:
            raise self._explain(e) from e
        return _ids(listing)

    def ping(self, timeout: float = 0.5) -> Tuple[bool, str]:
        missing = self._missing()
        if missing:
            return False, missing
        if self.spec.cloud:                 # instant: the menu never waits on the network
            return True, f"{self.spec.title} is ready (key set) with {self.model}"
        if self.spec.name in ("hermes", "hermes-agent"):
            # their /models takes seconds (the proxy lists ~400 models): only see if it listens
            if _port_open(self.base_url, timeout):
                return True, f"{self.spec.title} is running at {self.base_url} with {self.model}"
            return False, self._explain(ProviderError(unreachable=True)).args[0]
        return self._listed(timeout)

    def check(self, timeout: float = 5.0) -> Tuple[bool, str]:
        missing = self._missing()
        if missing:
            return False, missing
        if self.spec.name == "openrouter":
            # OpenRouter lists its models without a key, so /models can not test one; /key can
            # (401 for a wrong key, checked against the real API).
            try:
                _http("GET", f"{self.base_url}/key", None, self._headers(), timeout, secret=self._key)
            except ProviderError as e:
                return False, str(self._explain(e))
            except Exception:  # noqa: BLE001  (the ready check never raises)
                return False, f"no answer from {self.spec.title} at {self.base_url}"
        return self._listed(timeout)

    def _listed(self, timeout: float) -> Tuple[bool, str]:
        """GET /models: is the server there, does the key work, is the model offered?"""
        try:
            ids = self.list_models(timeout)
        except ProviderError as e:
            # 401/403: explain() says whether a key is missing or refused, and how to set one
            if self.spec.name == "openai-compatible" and e.status is None and not e.timed_out:
                return False, f"no OpenAI-compatible server at {self.base_url}"
            return False, str(e)
        except Exception:  # noqa: BLE001  (the ready check never raises)
            return False, f"no answer from {self.spec.title} at {self.base_url}"
        model = self.model or (ids[0] if ids else "")
        if not model:
            return False, f"no model is loaded in {self.spec.title} (load one, or pygym --set-model)"
        if ids and model not in ids:
            if self.spec.local:
                return False, f"model '{model}' is not offered by {self.base_url}"
            return True, (f"{self.spec.title} accepts the key; model '{model}' is not in its "
                          f"list, it may not work")
        what = "accepts the key" if self._key and self.spec.accepts_key else "is ready"
        return True, f"{self.spec.title} {what} with {model}"

    def _model_for_chat(self, timeout: float) -> str:
        if self.model:
            return self.model
        if self.base_url in _first_models:
            self.model = _first_models[self.base_url]
            return self.model
        ids = self.list_models(min(timeout, 5.0))
        if not ids:
            raise ProviderError(f"no model is loaded in {self.spec.title} "
                                f"(load one, or pygym --set-model NAME)")
        _first_models[self.base_url] = self.model = ids[0]
        return self.model

    def _body(self, messages, schema, temperature, max_tokens, seed, quirks: Set[str]) -> dict:
        body = {"model": self.model, "messages": with_schema(messages, schema)}
        if "no_temperature" not in quirks:
            body["temperature"] = temperature
        body["max_completion_tokens" if "max_completion_tokens" in quirks
             else "max_tokens"] = max_tokens
        if seed is not None and "no_seed" not in quirks:
            body["seed"] = seed
        if "no_response_format" in quirks:
            pass
        elif "json_object" in quirks:
            body["response_format"] = {"type": "json_object"}
        else:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "exercise", "schema": schema}}
        return body

    def chat(self, messages, schema, *, temperature, max_tokens, seed, timeout) -> str:
        missing = self._missing()
        if missing:
            raise ProviderError(missing)
        deadline = time.monotonic() + timeout
        self._model_for_chat(timeout)
        remembered = _quirks.get((self.base_url, self.model), set())
        quirks = set(remembered)
        while True:
            body = self._body(messages, schema, temperature, max_tokens, seed, quirks)
            try:
                reply = _http("POST", f"{self.base_url}/chat/completions", body,
                              self._headers(), max(deadline - time.monotonic(), 0.1),
                              secret=self._key)
                break
            except ProviderError as e:
                fix = _quirk_for(e, quirks)
                if fix is None:
                    raise self._explain(e) from e
                quirks.add(fix)                     # once per field: then it is never asked
        if quirks != remembered:
            _quirks[(self.base_url, self.model)] = quirks
        return clean_reply(self._content(reply))

    def _content(self, reply) -> str:
        try:
            content = reply["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"{self.spec.title} sent an answer without a message") from e
        if isinstance(content, list):                  # a list of parts on some servers
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(f"{self.spec.title} sent an empty answer")
        return content


# ----- Anthropic -------------------------------------------------------------------------------

ANTHROPIC_VERSION = "2023-06-01"
# (base_url, model) pairs whose server refused output_config (then the schema is in the prompt).
_no_output_config: Set[Tuple[str, str]] = set()


def strict_schema(schema):
    """A copy of the schema with "additionalProperties": false on every object that lists its
    properties (Anthropic's structured output asks for it). Free-form objects stay free."""
    if isinstance(schema, list):
        return [strict_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: strict_schema(v) for k, v in schema.items()}
    if out.get("type") == "object" and "properties" in out and "additionalProperties" not in out:
        out["additionalProperties"] = False
    return out


class AnthropicProvider(Provider):
    """POST {base_url}/v1/messages with x-api-key. The reply's shape is asked for with
    output_config (structured output); a server that refuses it gets the request once more
    without it (remembered), and the schema is in the system text anyway."""

    def __init__(self, model: str, base_url: str = "https://api.anthropic.com",
                 api_key: Optional[str] = None, *, spec: Optional[ProviderSpec] = None):
        self.spec = spec or PROVIDERS["anthropic"]
        self.name = self.spec.name
        self.model = model
        self.base_url = (base_url or self.spec.base_url).rstrip("/")
        if api_key is None:
            api_key, _source = keys.get_key(self.spec.name)
        self._key = api_key or None

    def __repr__(self) -> str:
        return f"AnthropicProvider({self.model!r}, {self.base_url!r})"

    def _headers(self) -> dict:
        return {"x-api-key": self._key or "", "anthropic-version": ANTHROPIC_VERSION}

    def _explain(self, error: ProviderError) -> ProviderError:
        return explain(error, self.spec, self.base_url, self.model, has_key=bool(self._key))

    def ping(self, timeout: float = 0.5) -> Tuple[bool, str]:
        if not self._key:
            return False, no_key_message(self.spec)
        return True, f"{self.spec.title} is ready (key set) with {self.model}"

    def list_models(self, timeout: float = 5.0) -> List[str]:
        if not self._key:
            raise ProviderError(no_key_message(self.spec))
        try:
            listing = _http("GET", f"{self.base_url}/v1/models?limit=100", None,
                            self._headers(), timeout, secret=self._key)
        except ProviderError as e:
            raise self._explain(e) from e
        return _ids(listing)

    def check(self, timeout: float = 5.0) -> Tuple[bool, str]:
        try:
            ids = self.list_models(timeout)
        except ProviderError as e:
            return False, str(e)
        if ids and self.model not in ids:
            return True, (f"{self.spec.title} accepts the key; model '{self.model}' is not in "
                          f"its list, it may not work")
        return True, f"{self.spec.title} accepts the key with {self.model}"

    def chat(self, messages, schema, *, temperature, max_tokens, seed, timeout) -> str:
        if not self._key:
            raise ProviderError(no_key_message(self.spec))
        deadline = time.monotonic() + timeout
        full = with_schema(messages, schema)
        system = "\n\n".join(str(m.get("content", "")) for m in full if m.get("role") == "system")
        turns = [{"role": m["role"], "content": str(m.get("content", ""))}
                 for m in full if m.get("role") in ("user", "assistant")]
        key = (self.base_url, self.model)
        quirks: Set[str] = set(_quirks.get(key, set()))
        structured = key not in _no_output_config
        while True:
            body: dict = {"model": self.model, "max_tokens": max_tokens, "system": system,
                          "messages": turns}
            if "no_temperature" not in quirks:
                body["temperature"] = temperature
            if structured:
                body["output_config"] = {"format": {"type": "json_schema",
                                                    "schema": strict_schema(schema)}}
            try:
                reply = _http("POST", f"{self.base_url}/v1/messages", body, self._headers(),
                              max(deadline - time.monotonic(), 0.1), secret=self._key)
                break
            except ProviderError as e:
                text = (e.detail or "").lower()
                if structured and e.status == 400 and ("output_config" in text
                                                       or "output_format" in text):
                    structured = False
                    _no_output_config.add(key)
                    continue
                if e.status == 400 and "temperature" in text and "no_temperature" not in quirks:
                    quirks.add("no_temperature")
                    _quirks[key] = set(quirks)
                    continue
                raise self._explain(e) from e
        blocks = reply.get("content") if isinstance(reply, dict) else None
        if not isinstance(blocks, list):
            raise ProviderError("Anthropic sent an answer without content")
        text = "".join(b.get("text", "") for b in blocks
                       if isinstance(b, dict) and b.get("type") == "text")
        if not text.strip():
            why = reply.get("stop_reason") if isinstance(reply, dict) else None
            raise ProviderError(f"Anthropic sent an empty answer (stop_reason: {why})")
        return clean_reply(text)


# ----- Claude Code (the student's Claude plan) -------------------------------------------------

CLAUDE_ENV_REMOVED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
CLAUDE_AUTH_TIMEOUT = 15.0
# `claude auth status` per executable path: (ok, message). It takes about a second, so once
# per process.
_claude_auth: Dict[str, Tuple[bool, str]] = {}


def claude_prompt(messages: List[dict]) -> Tuple[str, str]:
    """pygym's messages -> (system prompt, one user prompt). `claude -p` takes one prompt, so a
    conversation (a repair: system, user, assistant, user) is written out with clearly marked
    earlier turns, then the last message."""
    system = "\n\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "system")
    turns = [m for m in messages if m.get("role") in ("user", "assistant")]
    if not turns:
        return system, ""
    if len(turns) == 1:
        return system, str(turns[0].get("content", ""))
    parts = ["Earlier turns of this conversation, for context:"]
    for m in turns[:-1]:
        who = "YOUR EARLIER REPLY" if m.get("role") == "assistant" else "MESSAGE TO YOU"
        parts.append(f"===== {who} =====\n{m.get('content', '')}\n===== END =====")
    parts.append(f"===== THE MESSAGE TO ANSWER NOW =====\n{turns[-1].get('content', '')}")
    return system, "\n\n".join(parts)


def _child_env() -> dict:
    env = dict(os.environ)
    # CLAUDE_ENV_REMOVED: so the plan login is used, never API billing; the other providers'
    # keys: `claude` does not need them
    for name in CLAUDE_ENV_REMOVED + key_env_names():
        env.pop(name, None)
    return env


def _kill(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        pass


class ClaudeCodeProvider(Provider):
    """`claude -p` with structured output, the student's own Claude plan (no API key).

    The prompt goes in on stdin, the run happens in an empty temporary folder (deleted after)
    with no tools, no MCP servers, no settings files and no saved session. ANTHROPIC_API_KEY and
    ANTHROPIC_AUTH_TOKEN are removed from the child's environment so the plan login is used.
    Never --bare: it ignores the plan login. temperature, seed and max_tokens can not be set."""

    def __init__(self, model: str = "sonnet", *, spec: Optional[ProviderSpec] = None,
                 executable: Optional[str] = None):
        self.spec = spec or PROVIDERS["claude-code"]
        self.name = self.spec.name
        self.model = model or self.spec.model
        self.base_url = ""
        self.executable = executable

    def _exe(self) -> Optional[str]:
        return self.executable or shutil.which("claude")

    def ping(self, timeout: float = 0.5) -> Tuple[bool, str]:
        exe = self._exe()
        if not exe:
            return False, "Claude Code is not installed (https://claude.com/claude-code)"
        if exe in _claude_auth:
            return _claude_auth[exe]
        try:
            done = subprocess.run([exe, "auth", "status"], capture_output=True, text=True,
                                  timeout=CLAUDE_AUTH_TIMEOUT, env=_child_env(),
                                  stdin=subprocess.DEVNULL)
            info = json.loads(done.stdout)
        except subprocess.TimeoutExpired:
            return False, "Claude Code did not answer `claude auth status`"   # not remembered
        except (OSError, ValueError):
            info = {}
        if isinstance(info, dict) and info.get("loggedIn") is True:
            result = (True, f"Claude Code is ready (Claude plan) with {self.model}")
        else:
            result = (False, "Claude Code is not logged in: run `claude` once to log in")
        _claude_auth[exe] = result
        return result

    def list_models(self, timeout: float = 5.0) -> List[str]:
        return list(CLAUDE_CODE_MODELS)

    def command(self, schema: dict, system: str) -> List[str]:
        return [self._exe() or "claude", "-p", "--output-format", "json",
                "--json-schema", json.dumps(schema, separators=(",", ":")),
                "--model", self.model, "--system-prompt", system,
                "--tools", "", "--strict-mcp-config", "--setting-sources", "",
                "--no-session-persistence", "--disable-slash-commands"]

    def chat(self, messages, schema, *, temperature, max_tokens, seed, timeout) -> str:
        if not self._exe():
            raise ProviderError("Claude Code is not installed (https://claude.com/claude-code)")
        system, prompt = claude_prompt(messages)
        workdir = tempfile.mkdtemp(prefix="pygym-claude-")
        try:
            try:
                proc = subprocess.Popen(self.command(schema, system), cwd=workdir,
                                        env=_child_env(), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding="utf-8", errors="replace",
                                        start_new_session=(os.name == "posix"))
            except OSError as e:
                raise ProviderError(f"Claude Code could not start ({e.strerror or e})") from e
            try:
                out, err = proc.communicate(prompt, timeout=max(timeout, 0.1))
            except subprocess.TimeoutExpired:
                _kill(proc)
                proc.communicate()
                raise ProviderError(f"Claude Code did not answer in time ({timeout:.0f} s)",
                                    timed_out=True) from None
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return self._parse(out, err, proc.returncode)

    def _parse(self, out: str, err: str, code: Optional[int]) -> str:
        try:
            envelope = json.loads(out)
        except (TypeError, ValueError):
            envelope = None
        if not isinstance(envelope, dict):
            why = (err or out or "").strip().splitlines()
            line = why[-1][:DETAIL_LIMIT] if why else f"exit code {code}"
            raise ProviderError(f"Claude Code sent an answer pygym could not read ({line})")
        if envelope.get("is_error"):
            text = str(envelope.get("result") or envelope.get("subtype") or "unknown error")
            raise ProviderError(f"Claude Code: {text.strip()[:DETAIL_LIMIT]}")
        structured = envelope.get("structured_output")
        if isinstance(structured, dict):
            return json.dumps(structured, ensure_ascii=False)
        result = envelope.get("result")
        if isinstance(result, str) and result.strip():
            return clean_reply(result)
        raise ProviderError("Claude Code sent an empty answer")


# ----- tests -----------------------------------------------------------------------------------

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


# ----- choosing ----------------------------------------------------------------------------------

def make_provider(spec: ProviderSpec, model: str = "", base_url: str = "") -> Provider:
    """The provider object for a registry entry ("" = the entry's default model / address)."""
    model = model or spec.model
    base_url = base_url or spec.base_url
    if spec.kind == "ollama":
        return OllamaProvider(model, base_url)
    if spec.kind == "anthropic":
        return AnthropicProvider(model, base_url, spec=spec)
    if spec.kind == "claude-code":
        return ClaudeCodeProvider(model, spec=spec)
    return OpenAICompatProvider(model, base_url, spec=spec)


def get_provider(settings) -> Provider:
    """The provider named in the settings. Raises ProviderError for an unknown name."""
    try:
        chosen = resolve(settings)
    except UnknownProvider as e:
        raise ProviderError(str(e)) from None
    return make_provider(chosen.spec, chosen.model, chosen.base_url)


__all__ = ["ProviderError", "Provider", "OllamaProvider", "OpenAICompatProvider",
           "AnthropicProvider", "ClaudeCodeProvider", "FakeProvider", "get_provider",
           "make_provider", "clean_reply", "with_schema", "explain", "get_spec"]
