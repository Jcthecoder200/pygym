"""Real-world use cases: situations where a programmer would naturally reach for a target.

Exercises are written around one of these ("a bank account balance after each transaction" for
numpy.cumsum), so the student learns which everyday problems the function solves. They are asked
from the model once per target, checked, and saved in PYGYM_HOME/generated/usecases/<target>.json:

    {"version": 1, "target": "numpy.cumsum", "generator": "ollama:qwen2.5:1.5b",
     "use_cases": ["...", ...], "used": ["...", ...]}

"used" is the order they were used in (most recent last): pick() takes one that was never used,
else the one used longest ago, so every use case comes round before any repeats. If the model
gives nothing usable nothing is saved, and the next exercise asks again. Nothing here raises
except parse() (InvalidUseCases).
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import List, Optional

from .targets import TargetInfo

VERSION = 1
ASK_MIN, ASK_MAX = 6, 8      # how many we ask for
MIN_USE_CASES = 3            # fewer usable ones than this: the reply is rejected
MAX_LENGTH = 120             # characters per use case
TIME_SLICE = 8.0             # at most this many seconds of the exercise's budget ...
BUDGET_SHARE = 0.4           # ... and at most this share of it
MAX_TOKENS = 300
TEMPERATURE = 0.7

SCHEMA: dict = {
    "type": "object",
    "properties": {
        "use_cases": {"type": "array", "items": {"type": "string"},
                      "minItems": ASK_MIN, "maxItems": ASK_MAX},
    },
    "required": ["use_cases"],
}

SYSTEM = ("You help a teacher find real-world programming situations for practice exercises. "
          "Reply with ONE JSON object only, matching the given schema.")

TEMPLATE = """List {ask_min} to {ask_max} short real-world situations where a programmer would \
naturally reach for `{target}`.

What `{target}` is:
  signature: {signature}
  documentation: {doc}

Rules:
- Each situation is one short phrase (at most 12 words): everyday data and the result someone \
wants from it.
- `{target}` itself does the main work in every situation: it is the natural tool for it.
- Concrete situations from daily life, work or study (money, sport, travel, school, shopping, \
weather, text...). No code and no function names.
- All situations are different from each other.

Example of the exact shape (about `max`, NOT your target):
{fewshot}"""

FEWSHOT = json.dumps({"use_cases": [
    "the highest score in a game round", "the hottest day of the week",
    "the most expensive item in a shopping cart", "the latest arrival on a train timetable",
    "the longest word in a sentence", "the biggest monthly expense in a budget",
]})

_MARKER = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*")


class InvalidUseCases(ValueError):
    """The model's reply has no usable list of use cases."""


def build_messages(info: TargetInfo) -> List[dict]:
    user = TEMPLATE.format(target=info.name, ask_min=ASK_MIN, ask_max=ASK_MAX,
                           signature=info.signature or "(see documentation)",
                           doc=info.doc or "(no documentation)", fewshot=FEWSHOT)
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def clean(items) -> List[str]:
    """The usable use cases of a list, in order: strings only, list markers ("1.", "-") and a
    final full stop removed, spaces collapsed, not empty, at most MAX_LENGTH characters,
    duplicates (ignoring case) dropped, at most ASK_MAX of them."""
    out: List[str] = []
    seen = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, str):
            continue
        text = " ".join(_MARKER.sub("", item).split()).rstrip(".").strip()
        if not text or len(text) > MAX_LENGTH or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
        if len(out) == ASK_MAX:
            break
    return out


def parse(raw: str) -> List[str]:
    """The model's reply -> the cleaned use cases. Raises InvalidUseCases."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise InvalidUseCases("not valid JSON") from None
    items = data.get("use_cases") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise InvalidUseCases("no \"use_cases\" list")
    found = clean(items)
    if len(found) < MIN_USE_CASES:
        raise InvalidUseCases(f"only {len(found)} usable use cases (need {MIN_USE_CASES})")
    return found


def _dir() -> Path:
    return Path(os.environ.get("PYGYM_HOME", Path.home() / ".pygym")) / "generated" / "usecases"


def path(target: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.]", "_", target) or "_"
    return _dir() / f"{safe}.json"


def _read(target: str) -> dict:
    try:
        data = json.loads(path(target).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) and data.get("target") == target else {}


def load(target: str) -> List[str]:
    """The saved use cases of a target ([] if there are none or too few are usable)."""
    found = clean(_read(target).get("use_cases"))
    return found if len(found) >= MIN_USE_CASES else []


def save(target: str, use_cases: List[str], generator: str = "",
         used: Optional[List[str]] = None) -> None:
    """Write the file (atomically). Never raises."""
    try:
        file = path(target)
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_name(file.name + ".tmp")
        tmp.write_text(json.dumps({"version": VERSION, "target": target, "generator": generator,
                                   "use_cases": list(use_cases), "used": list(used or [])},
                                  indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, file)
    except Exception:  # noqa: BLE001
        pass


def fetch(info: TargetInfo, provider, timeout: float, seed: Optional[int] = None
          ) -> Optional[List[str]]:
    """Ask the model once (within `timeout` seconds) and save what it gives. None when the
    model is unreachable, too slow or gives fewer than MIN_USE_CASES usable use cases."""
    try:
        raw = provider.chat(build_messages(info), SCHEMA, temperature=TEMPERATURE,
                            max_tokens=MAX_TOKENS, seed=seed, timeout=max(timeout, 0.1))
        found = parse(raw)
    except Exception:  # noqa: BLE001  (ProviderError, InvalidUseCases: write without a story)
        return None
    save(info.name, found, f"{getattr(provider, 'name', '')}:{getattr(provider, 'model', '')}")
    return found


def pick(target: str, use_cases: List[str], rng: Optional[random.Random] = None) -> str:
    """The use case for the next exercise: one never used yet (at random), else the one used
    longest ago. Remembers the choice in the file."""
    if not use_cases:
        return ""
    rng = rng or random.Random()
    data = _read(target)
    used = [u for u in data.get("used") or [] if isinstance(u, str) and u in use_cases]
    fresh = [u for u in use_cases if u not in used]
    choice = rng.choice(fresh) if fresh else used[0]
    used = [u for u in used if u != choice] + [choice]
    save(target, use_cases, str(data.get("generator") or ""), used)
    return choice
