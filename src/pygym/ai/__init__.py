"""AI mode: generate exercises with a local LLM (stub; the engine is implemented here).

Contract (used by Session and the menu; keep these names and signatures):

    WARNING: str                       one-line warning shown with every generated exercise
    class GenerationUnavailable(Exception)
        raised when no exercise could be generated and validated in time
    def available() -> bool            is a provider reachable right now? (fast, never raises)
    def generate_exercise(target: str, difficulty: int, *, avoid_ids=None, settings=None,
                          timeout: float = 20.0) -> Exercise
        target: a built-in name ("zip") or a dotted importable name ("statistics.median",
        "str.join"). Returns a VALIDATED Exercise with source="ai" and generator="<provider:model>",
        or raises GenerationUnavailable.
"""
from __future__ import annotations

WARNING = "AI-generated exercise: it may be incorrect. Use it carefully."


class GenerationUnavailable(Exception):
    """No exercise could be generated and validated (no provider, timeout, invalid output...)."""


def available() -> bool:
    return False


def generate_exercise(target, difficulty, *, avoid_ids=None, settings=None, timeout=20.0):
    raise NotImplementedError("the AI engine is not implemented yet")
