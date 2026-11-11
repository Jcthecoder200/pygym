"""AI mode: generate exercises with a local LLM.

Contract (used by Session and the menu; keep these names and signatures):

    WARNING: str                       one-line warning shown with every generated exercise
    class GenerationUnavailable(Exception)
        raised when no exercise could be generated and validated in time
    def available() -> bool            is a provider reachable right now? (fast, never raises)
    def status(settings=None) -> AIStatus   the same, with a one-line message for the menu
    def generate_exercise(target: str, difficulty: int, *, avoid_ids=None, settings=None,
                          timeout: Optional[float] = None, on_progress=None, bigger=False) -> Exercise
        target: a built-in name ("zip") or a dotted importable name ("statistics.median",
        "str.join"). Returns a VALIDATED Exercise with source="ai" and generator="<provider:model>",
        or raises GenerationUnavailable. on_progress(event_dict), if given, is called at every
        step (ideas / asking / checking / rejected / done / fallback; see
        engine.generate_exercise). The reference fits the level (levels.py); bigger=True puts
        a target too small for advanced on its own (targets.TargetInfo.simple) into a bigger task.

Modules: targets (what can be a target), levels (what each level means), usecases (real-world situations each exercise is set
in, asked once per target and saved in PYGYM_HOME/generated/usecases/), prompt (what we ask),
providers (Ollama, an OpenAI-compatible server, a fake for tests), validate (reply -> checked
Exercise), cache (saved exercises in PYGYM_HOME/generated/), engine (the loop and the time
budget).
Set the environment variable PYGYM_NO_AI=1 to turn AI mode off (no network at all).
"""
from __future__ import annotations

from .engine import (AIStatus, GenerationUnavailable, WARNING, available, generate_exercise,
                     status)

__all__ = ["WARNING", "GenerationUnavailable", "AIStatus", "available", "status",
           "generate_exercise"]
