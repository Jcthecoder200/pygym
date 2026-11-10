"""User settings in a small JSON file: PYGYM_HOME/config.json (default ~/.pygym/config.json).

Contract (keep names): Settings dataclass, load() -> Settings, save(settings) -> None.
Unknown or broken files fall back to defaults; load() never raises.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class Settings:
    mode: str = "preset"          # "preset" or "ai"
    level: int = 1                # 1 beginner, 2 intermediate, 3 advanced
    provider: str = "ollama"      # LLM provider for AI mode
    model: str = "qwen2.5:3b"     # model name for that provider
    base_url: str = "http://localhost:11434"


def _path() -> Path:
    return Path(os.environ.get("PYGYM_HOME", Path.home() / ".pygym")) / "config.json"


def load() -> Settings:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        names = {f.name for f in fields(Settings)}
        return Settings(**{k: v for k, v in data.items() if k in names})
    except Exception:
        return Settings()


def save(settings: Settings) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
