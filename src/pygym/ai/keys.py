"""API keys for the AI providers. `get_key` is the ONLY place that reads key environment
variables and the key file.

Where a key comes from, first found wins:
  1. the provider's own environment variables (registry: OPENAI_API_KEY, GEMINI_API_KEY ...)
  2. the key saved with `pygym --set-key PROVIDER` in PYGYM_HOME/keys.json
  3. PYGYM_API_KEY (the one key of pygym 0.2, kept as a fallback for every keyed provider)

keys.json is created with owner-only permissions (0600) on POSIX. On Windows the file is only
protected by being in the user's profile folder (~/.pygym), like other per-user secrets.
A key is never put in config.json and never printed: `describe` says only where it is from.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from .registry import get_spec

FALLBACK_ENV = "PYGYM_API_KEY"


def path() -> Path:
    return Path(os.environ.get("PYGYM_HOME", Path.home() / ".pygym")) / "keys.json"


def _read() -> Dict[str, str]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  (missing or broken file: no saved keys)
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v}


def get_key(provider: str) -> Tuple[Optional[str], str]:
    """(key, where it is from) for a provider: ("...", "env OPENAI_API_KEY"), ("...", "saved")
    or (None, ""). Never raises for a known provider."""
    spec = get_spec(provider)
    if not spec.accepts_key:
        return None, ""
    for name in spec.key_env:
        value = os.environ.get(name, "").strip()
        if value:
            return value, f"env {name}"
    saved = _read().get(spec.name, "").strip()
    if saved:
        return saved, "saved"
    value = os.environ.get(FALLBACK_ENV, "").strip()
    if value:
        return value, f"env {FALLBACK_ENV}"
    return None, ""


def describe(provider: str) -> str:
    """Where the key is from, never the key: 'key set (OPENAI_API_KEY)', 'key saved (keys.json)',
    'no key', 'no key (optional)', 'no key needed'."""
    spec = get_spec(provider)
    if not spec.accepts_key:
        return "no key needed"
    _key, source = get_key(spec.name)
    if source == "saved":
        return f"key saved ({path().name})"
    if source:
        return f"key set ({source[len('env '):] if source.startswith('env ') else source})"
    return "no key" if spec.key_required else "no key (optional)"


def _write(data: Dict[str, str]) -> None:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_name(file.name + ".tmp")
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    # created 0600 from the start: the key is never readable by others, not even for a moment
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    if os.name == "posix":
        os.chmod(str(tmp), 0o600)
    os.replace(str(tmp), str(file))


def save_key(provider: str, key: str) -> None:
    """Save a key for a provider in keys.json. Raises ValueError (no key needed, empty key)."""
    spec = get_spec(provider)
    if not spec.accepts_key:
        raise ValueError(f"{spec.name} needs no API key")
    key = (key or "").strip()
    if not key:
        raise ValueError("the key is empty")
    if any(ch.isspace() for ch in key) or not key.isprintable():
        raise ValueError("the key has spaces or invisible characters in it")
    data = _read()
    data[spec.name] = key
    _write(data)


def remove_key(provider: str) -> bool:
    """Delete a saved key. True if there was one. The file is deleted when it gets empty."""
    spec = get_spec(provider)
    data = _read()
    if spec.name not in data:
        return False
    del data[spec.name]
    if data:
        _write(data)
    else:
        path().unlink()
    return True


def redact(text: str, key: Optional[str]) -> str:
    """The text with every copy of the key replaced (for messages that echo a server reply)."""
    if key and len(key) >= 4 and key in text:
        return text.replace(key, "[key hidden]")
    return text
