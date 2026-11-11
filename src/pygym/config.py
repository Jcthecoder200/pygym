"""User settings in a small JSON file: PYGYM_HOME/config.json (default ~/.pygym/config.json).

Contract (keep names): Settings dataclass, load() -> Settings, save(settings) -> None.
Fields may be added (with defaults), never removed or renamed.

load() never raises: a missing or broken file gives the defaults, unknown keys are ignored and a
bad value is replaced by that field's default (the other fields are kept).
save() refuses values that load() would throw away (it raises ValueError), so a bug in the program
can not write a file that silently changes meaning.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

MODES = ("preset", "ai")
AI_TIMEOUT_RANGE = (10, 300)  # seconds
UIS = ("auto", "editor", "text", "tk")
LEVELS = (1, 2, 3)


@dataclass
class Settings:
    mode: str = "preset"          # "preset" or "ai"
    level: int = 1                # 1 beginner, 2 intermediate, 3 advanced
    provider: str = "ollama"      # LLM provider for AI mode
    model: str = "qwen2.5:1.5b"   # model name for that provider
    base_url: str = "http://localhost:11434"
    ui: str = "auto"              # window for the exercise: one of UIS
    library: str = "builtins"     # the library used last in the menu
    prefetch: bool = True         # write the next AI exercise while the student works
    ai_timeout: int = 60          # seconds the AI may spend on one exercise (10-300)


_DEFAULTS = Settings()


def _path() -> Path:
    return Path(os.environ.get("PYGYM_HOME", Path.home() / ".pygym")) / "config.json"


def _clean(name: str, value: Any) -> Any:
    """The value if it is allowed for this field, else the field's default."""
    default = getattr(_DEFAULTS, name)
    if name in ("mode", "ui"):
        allowed = MODES if name == "mode" else UIS
        text = value.strip().lower() if isinstance(value, str) else None
        return text if text in allowed else default
    if name == "prefetch":
        return value if isinstance(value, bool) else default
    if name == "ai_timeout":
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)
              and AI_TIMEOUT_RANGE[0] <= value <= AI_TIMEOUT_RANGE[1])
        return int(value) if ok else default
    if name == "level":
        ok = isinstance(value, int) and not isinstance(value, bool) and value in LEVELS
        return value if ok else default
    # provider, model, base_url, library: any non-empty text
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def load() -> Settings:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return Settings()
        names = {f.name for f in fields(Settings)}
        return Settings(**{k: _clean(k, v) for k, v in data.items() if k in names})
    except Exception:
        return Settings()


def problems(settings: Settings) -> list:
    """What is wrong with these settings (an empty list means fine)."""
    found = []
    for f in fields(Settings):
        value = getattr(settings, f.name)
        if value != _clean(f.name, value) or type(value) is not type(getattr(_DEFAULTS, f.name)):
            found.append(f"{f.name}={value!r}")
    return found


def save(settings: Settings) -> None:
    bad = problems(settings)
    if bad:
        raise ValueError("invalid settings: " + ", ".join(bad))
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp, path)
