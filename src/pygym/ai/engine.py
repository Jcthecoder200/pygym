"""The generate loop: prompt -> provider -> validate -> cache, inside a time budget."""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from .. import config
from ..models import Exercise
from . import cache, usecases
from .prompt import build_messages, schema_for
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


# Why a reply was rejected, in a few words for a progress line ("try 1 rejected: crashed on its
# tests"). First match wins; the full reason still goes back to the model and into the log.
_SHORT_REASONS = (
    ("JSON", "not valid JSON"),
    ("crashed", "crashed on its tests"),
    ("ran too long", "ran too long"),
    ("really call", "did not really use the function"),
    ("must call", "did not use the function"),
    ("repeated an earlier", "repeated an earlier exercise"),
    ("own tests", "failed its own tests"),
    ("different results", "gave different results"),
    ("syntax error", "had a syntax error"),
    ("test case", "had unusable tests"),
    ("not allow", "used something not allowed"),
    ("blocked", "used something not allowed"),
    ("is missing", "left out a part"),
)

# The stages of on_progress events, in the order they usually come ("ideas" only when the
# target's real-world use cases are not saved yet).
STAGES = ("ideas", "asking", "checking", "rejected", "done", "fallback")


def short_reason(problem: str) -> str:
    """'the reference solution crashed on the tests: IndexError ...' -> 'crashed on its tests'."""
    for needle, short in _SHORT_REASONS:
        if needle in problem:
            return short
    return "not usable"


ProgressCallback = Callable[[Dict[str, object]], None]


def _reporter(on_progress: Optional[ProgressCallback], budget: float):
    """report(stage, attempt, detail): builds the event dict and calls on_progress with it."""
    start = clock()

    def report(stage: str, attempt: int = 0, detail: str = "") -> None:
        if on_progress is None:
            return
        event = {"attempt": attempt, "max_attempts": MAX_ATTEMPTS,
                 "elapsed": round(max(0.0, clock() - start), 3), "budget": float(budget),
                 "stage": stage, "detail": detail}
        try:
            on_progress(event)
        except Exception:  # noqa: BLE001  (a broken display must not stop the generation)
            pass           # (a BaseException, e.g. Session's JobCancelled, does stop it)
    return report


def generate_exercise(target, difficulty, *, avoid_ids: Optional[Iterable[str]] = None,
                      settings: Optional[config.Settings] = None,
                      timeout: float = 20.0,
                      on_progress: Optional[ProgressCallback] = None) -> Exercise:
    """A new, validated exercise about `target` at level `difficulty` (1-3).

    The exercise is set in one of the target's real-world use cases (usecases.py), taking turns.
    Up to 4 model calls within `timeout` seconds (a rejected reply is sent back with the reason,
    so the model can fix it). If none works, an earlier saved exercise for the same target and
    level is reused. Otherwise raises GenerationUnavailable with a one-line reason.

    on_progress (optional) is called on this thread with a dict at every step:
        {"attempt": 2, "max_attempts": 4, "elapsed": 7.1, "budget": 20.0,
         "stage": "ideas" | "asking" | "checking" | "rejected" | "done" | "fallback",
         "detail": "..."}
    "ideas" (attempt 0): the model lists real-world use cases of the target first (once per
    target, then they are saved; at most usecases.TIME_SLICE seconds of the budget);
    "asking": attempt n is sent to the model; "checking": its reply is being validated (the
    sandbox runs it); "rejected": detail says why in a few words (short_reason); "done": the
    exercise is ready; "fallback": no new exercise, detail says what happens instead. A model
    gives no progress of its own, so a bar can only fill against elapsed / budget. An Exception
    raised by on_progress is ignored; a BaseException (a cancel) ends the generation.
    """
    if _turned_off():
        raise GenerationUnavailable("AI mode is turned off (PYGYM_NO_AI)")
    try:
        info = resolve(str(target))
    except UnknownTarget as e:
        raise GenerationUnavailable(str(e)) from None
    level = _level(difficulty)
    avoid = set(avoid_ids or ())
    report = _reporter(on_progress, timeout)
    try:
        return _generate(info, level, avoid, settings or config.load(), timeout, report)
    except GenerationUnavailable:
        raise
    except Exception as e:  # noqa: BLE001  (a bug here must not crash a lesson)
        raised = GenerationUnavailable(f"the AI engine failed ({type(e).__name__}: {e})")
        cached = cache.pick(info.name, level, avoid)
        if cached is not None:
            report("fallback", 0, "using an earlier saved exercise")
            return cached
        report("fallback", 0, "the AI engine failed")
        raise raised from e


def _use_cases(info: TargetInfo, provider, timeout: float, time_left, rng, report) -> List[str]:
    """The target's saved use cases, or new ones from the model (in a slice of the budget).
    [] when the model gives none: the exercise is then written without a given situation, and
    the next exercise asks again."""
    found = usecases.load(info.name)
    if found:
        return found
    slice_ = min(usecases.TIME_SLICE, float(timeout) * usecases.BUDGET_SHARE, time_left())
    if slice_ <= 0:
        return []
    report("ideas", 0, "finding real-world uses")
    return usecases.fetch(info, provider, slice_, seed=rng.randrange(2 ** 31)) or []


def _generate(info: TargetInfo, level: int, avoid: set, settings, timeout: float,
              report=None) -> Exercise:
    if report is None:
        report = _reporter(None, timeout)
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
    ideas = _use_cases(info, provider, timeout, time_left, rng, report)
    use_case = usecases.pick(info.name, ideas, rng) if ideas else None
    problem, repair, fresh_retries, tries = "", None, 0, 0
    stop_reason = ""

    while tries < MAX_ATTEMPTS and time_left() > 0 and (tries == 0 or time_left() > MIN_TIME_FOR_RETRY):
        if tries and repair is None and ideas:
            use_case = usecases.pick(info.name, ideas, rng)   # a fresh try: another situation
        messages = build_messages(info, level, use_case=use_case,
                                  avoid=_avoid_lines(earlier, avoid), repair=repair)
        tries += 1
        report("asking", tries)
        try:
            raw = provider.chat(messages, schema_for(info),
                                temperature=min(BASE_TEMPERATURE + 0.1 * fresh_retries, 1.2),
                                max_tokens=MAX_TOKENS, seed=rng.randrange(2 ** 31),
                                timeout=max(time_left(), 0.1))
        except ProviderError as e:
            stop_reason = str(e)
            break                                   # server down or model missing: stop now
        report("checking", tries)
        try:
            ex = to_exercise(raw, info, level, generator,
                             sandbox_timeout=min(SANDBOX_TIMEOUT, max(time_left(), 1.0)))
            if ex.id in known_ids:
                raise InvalidExercise("it repeated an earlier exercise", repairable=False)
        except InvalidExercise as e:
            problem = e.problem
            cache.log_rejected(info.name, level, generator, problem, raw)
            report("rejected", tries, short_reason(problem))
            if e.repairable:
                repair = (raw, e.problem)
            else:
                repair, fresh_retries = None, fresh_retries + 1
            continue
        cache.add(ex)
        report("done", tries)
        return ex

    cached = cache.pick(info.name, level, avoid)
    if cached is not None:
        report("fallback", tries, "using an earlier saved exercise")
        return cached
    report("fallback", tries, "the model is not reachable" if stop_reason else
           "no valid exercise from the model" if problem else "no time left to ask the model")
    if stop_reason:
        raise GenerationUnavailable(stop_reason)
    if problem:
        word = "try" if tries == 1 else "tries"
        raise GenerationUnavailable(f"no valid exercise after {tries} {word} ({problem})")
    raise GenerationUnavailable(f"no time to ask the model (timeout {timeout:g} s)")
