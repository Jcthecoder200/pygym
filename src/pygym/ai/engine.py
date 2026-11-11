"""The generate loop: prompt -> provider -> validate -> cache, inside a time budget."""
from __future__ import annotations

import os
import random
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple

from .. import config
from ..models import Exercise
from . import cache
from .prompt import FOCUS, SCENARIOS, build_messages, schema_for
from .providers import ProviderError, get_provider
from .targets import TargetInfo, UnknownTarget, resolve
from .validate import InvalidExercise, to_exercise

WARNING = "AI-generated exercise: it may be incorrect. Use it carefully."
NO_AI_ENV = "PYGYM_NO_AI"
MAX_ATTEMPTS = 4
MIN_TIME_FOR_RETRY = 3.0      # seconds left that are worth another model call
SANDBOX_TIMEOUT = 5.0
MAX_TOKENS = 700
BASE_TEMPERATURE = 0.8
STATUS_TTL = 10.0

clock: Callable[[], float] = time.monotonic   # tests replace this with a fake clock


class GenerationUnavailable(Exception):
    """No exercise could be generated and validated (no provider, timeout, invalid output...)."""


@dataclass
class AIStatus:
    ok: bool
    provider: str
    model: str
    message: str      # one line for the menu


_status_cache: Dict[Tuple[str, str, str], Tuple[float, AIStatus]] = {}
_recent_scenarios: Deque[str] = deque(maxlen=len(SCENARIOS) // 2)


def _turned_off() -> bool:
    return os.environ.get(NO_AI_ENV, "").strip() not in ("", "0", "false", "no")


def status(settings: Optional[config.Settings] = None) -> AIStatus:
    """Is the provider reachable and the model installed? Remembered for 10 s. Never raises."""
    try:
        settings = settings or config.load()
        provider_name = str(getattr(settings, "provider", ""))
        model = str(getattr(settings, "model", ""))
        if _turned_off():
            return AIStatus(False, provider_name, model, "AI mode is turned off (PYGYM_NO_AI)")
        key = (provider_name, model, str(getattr(settings, "base_url", "")))
        now = time.monotonic()
        hit = _status_cache.get(key)
        if hit and now - hit[0] < STATUS_TTL:
            return hit[1]
        try:
            provider = get_provider(settings)
            ok, message = provider.ping(0.5)
        except ProviderError as e:
            ok, message = False, str(e)
        result = AIStatus(bool(ok), provider_name, model, message)
        _status_cache[key] = (now, result)
        return result
    except Exception as e:  # noqa: BLE001
        return AIStatus(False, "", "", f"AI status unknown ({type(e).__name__})")


def available() -> bool:
    """True if AI mode can reach its provider right now. Fast; never raises."""
    try:
        return status().ok
    except Exception:  # noqa: BLE001
        return False


def _scenario(rng: random.Random) -> str:
    choices = [s for s in SCENARIOS if s not in _recent_scenarios] or SCENARIOS
    pick = rng.choice(choices)
    _recent_scenarios.append(pick)
    return pick


def _avoid_lines(earlier: List[Exercise], avoid_ids: set) -> List[str]:
    """Up to 5 'name: first sentence' lines about exercises the model should not repeat."""
    chosen = [e for e in earlier if e.id in avoid_ids] + [e for e in earlier if e.id not in avoid_ids]
    lines = []
    for e in reversed(chosen):
        first = e.question_text.split(". ")[0].strip()[:160]
        line = f"{e.function_name}: {first}"
        if line not in lines:
            lines.append(line)
        if len(lines) == 5:
            break
    return lines


def _level(difficulty) -> int:
    try:
        level = int(difficulty)
    except (TypeError, ValueError):
        return 1
    return level if level in (1, 2, 3) else 1


def generate_exercise(target, difficulty, *, avoid_ids: Optional[Iterable[str]] = None,
                      settings: Optional[config.Settings] = None,
                      timeout: float = 20.0) -> Exercise:
    """A new, validated exercise about `target` at level `difficulty` (1-3).

    Up to 4 model calls within `timeout` seconds (a rejected reply is sent back with the reason,
    so the model can fix it). If none works, an earlier saved exercise for the same target and
    level is reused. Otherwise raises GenerationUnavailable with a one-line reason.
    """
    if _turned_off():
        raise GenerationUnavailable("AI mode is turned off (PYGYM_NO_AI)")
    try:
        info = resolve(str(target))
    except UnknownTarget as e:
        raise GenerationUnavailable(str(e)) from None
    level = _level(difficulty)
    avoid = set(avoid_ids or ())
    try:
        return _generate(info, level, avoid, settings or config.load(), timeout)
    except GenerationUnavailable:
        raise
    except Exception as e:  # noqa: BLE001  (a bug here must not crash a lesson)
        raised = GenerationUnavailable(f"the AI engine failed ({type(e).__name__}: {e})")
        cached = cache.pick(info.name, level, avoid)
        if cached is not None:
            return cached
        raise raised from e


def _generate(info: TargetInfo, level: int, avoid: set, settings, timeout: float) -> Exercise:
    deadline = clock() + max(float(timeout), 0.0)

    def time_left() -> float:
        return deadline - clock()

    try:
        provider = get_provider(settings)
    except ProviderError as e:
        raise GenerationUnavailable(str(e)) from None
    generator = f"{provider.name}:{provider.model}"
    earlier = cache.load(info.name)
    known_ids = {e.id for e in earlier} | avoid
    rng = random.Random()
    focus = rng.sample(FOCUS, len(FOCUS))
    problem, repair, fresh_retries, tries = "", None, 0, 0
    stop_reason = ""

    while tries < MAX_ATTEMPTS and time_left() > 0 and (tries == 0 or time_left() > MIN_TIME_FOR_RETRY):
        messages = build_messages(info, level, scenario=_scenario(rng), focus=focus[tries % len(focus)],
                                  avoid=_avoid_lines(earlier, avoid), repair=repair)
        tries += 1
        try:
            raw = provider.chat(messages, schema_for(info),
                                temperature=min(BASE_TEMPERATURE + 0.1 * fresh_retries, 1.2),
                                max_tokens=MAX_TOKENS, seed=rng.randrange(2 ** 31),
                                timeout=max(time_left(), 0.1))
        except ProviderError as e:
            stop_reason = str(e)
            break                                   # server down or model missing: stop now
        try:
            ex = to_exercise(raw, info, level, generator,
                             sandbox_timeout=min(SANDBOX_TIMEOUT, max(time_left(), 1.0)))
            if ex.id in known_ids:
                raise InvalidExercise("it repeated an earlier exercise", repairable=False)
        except InvalidExercise as e:
            problem = e.problem
            cache.log_rejected(info.name, level, generator, problem, raw)
            if e.repairable:
                repair = (raw, e.problem)
            else:
                repair, fresh_retries = None, fresh_retries + 1
            continue
        cache.add(ex)
        return ex

    cached = cache.pick(info.name, level, avoid)
    if cached is not None:
        return cached
    if stop_reason:
        raise GenerationUnavailable(stop_reason)
    if problem:
        word = "try" if tries == 1 else "tries"
        raise GenerationUnavailable(f"no valid exercise after {tries} {word} ({problem})")
    raise GenerationUnavailable(f"no time to ask the model (timeout {timeout:g} s)")
