"""What we ask the model: the JSON schema, the messages and the variety lists."""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence, Tuple

from .._runner import ALLOWED_IMPORTS
from .targets import TargetInfo

# The reply's shape. The model does NOT write expected outputs, ids or levels: we compute those.
# "tests" items are objects that map parameter names to values (lists are accepted too).
SCHEMA: dict = {
    "type": "object",
    "properties": {
        "question_text": {"type": "string"},
        "function_name": {"type": "string"},
        "function_signature": {"type": "string"},
        "reference_solution": {"type": "string"},
        "tests": {"type": "array", "items": {"type": "object"}, "minItems": 4, "maxItems": 6},
        "hint": {"type": "string"},
    },
    "required": ["question_text", "function_name", "function_signature",
                 "reference_solution", "tests", "hint"],
}

SYSTEM = ("You write short Python practice exercises for first-year university students. "
          "Reply with ONE JSON object only, matching the given schema.")

LEVEL_NAMES = {1: "beginner", 2: "intermediate", 3: "advanced"}
LEVEL_RULES: Dict[int, str] = {
    1: "one short function, 1-3 lines, uses the target directly on simple lists or strings",
    2: "a small realistic task, 3-8 lines, combines the target with one other idea "
       "(a comprehension, a dict, a condition or another built-in)",
    3: "a tricky task, up to 15 lines, uses a less common feature of the target "
       "(an optional argument, an edge case, nested data) and needs careful thinking",
}

SCENARIOS: List[str] = [
    "a school gradebook", "a weather station", "a music playlist", "a small shop",
    "a sports league table", "a library catalogue", "a to-do app", "a train timetable",
    "a recipe book", "a game leaderboard", "a fitness tracker", "a chat log",
    "a bus route planner", "a cinema booking system", "a plant watering schedule",
    "a student club budget", "a bakery order list", "a hiking trail log", "a coffee shop menu",
    "a parking garage", "a photo album", "a quiz game", "a bike rental service",
    "a university course list", "a pet shelter", "a word game", "a bank statement",
    "a delivery tracker", "a football match report", "a space mission log",
]

FOCUS: List[str] = [
    "the most common way to use it", "an optional argument", "an edge case with empty input",
    "working with strings", "combining it with a dict", "nested lists",
    "numbers with negative values", "keeping the original order", "a list of pairs",
    "counting or totals",
]

USER_TEMPLATE = """Write one exercise that teaches `{target}`.

Level: {level_name} ({level}/3): {level_rule}.
Story idea for the question (keep it short): {scenario}.
Focus: {focus}.

What `{target}` is:
  signature: {signature}
  documentation: {doc}

Rules:
- The student writes ONE function. question_text says what it takes and returns, with one example.
- function_signature is exactly the def line, e.g. "def total(prices):".
- reference_solution is the full correct function and MUST call {call_form}.
- Put any import on its own line inside reference_solution. Only these imports are allowed: {imports}.
- No input(), print(), files, network or randomness. No type hints. Return a value.
- tests: 4 to 6 test cases. Each test case maps every parameter name to its value,
  e.g. for def total(prices, tax) a test case is {{"prices": [1.5, 2], "tax": 0.1}}.
  Use only JSON values (lists, not tuples). Include one edge case (empty list, single item...).
  The test cases must give different results.
- hint: one sentence that points at `{target}` without giving the answer.
{avoid_block}
Example of the exact shape (about `max`, NOT your target):
{fewshot}"""

FEWSHOT = json.dumps({
    "question_text": "Write a function 'best_player' that takes a list of [name, score] pairs and "
                     "returns the name with the highest score. Example: "
                     "best_player([[\"ana\", 3], [\"bo\", 7]]) returns \"bo\".",
    "function_name": "best_player",
    "function_signature": "def best_player(pairs):",
    "reference_solution": "def best_player(pairs):\n    return max(pairs, key=lambda p: p[1])[0]",
    "tests": [{"pairs": [["ana", 3], ["bo", 7]]}, {"pairs": [["x", 1]]},
              {"pairs": [["a", 2], ["b", 9], ["c", 1]]}, {"pairs": [["p", -1], ["q", -5]]}],
    "hint": "max() takes a key= function that says what to compare.",
})

REPAIR_TEMPLATE = ("That exercise was rejected: {problem}. "
                   "Return a corrected JSON object with the same fields.")


def build_messages(info: TargetInfo, level: int, *, scenario: str, focus: str,
                   avoid: Sequence[str] = (), repair: Optional[Tuple[str, str]] = None
                   ) -> List[dict]:
    """Chat messages for one attempt. repair = (previous raw reply, why it was rejected)."""
    level = level if level in LEVEL_NAMES else 1
    imports = sorted(ALLOWED_IMPORTS | set(info.allowed_imports))
    avoid_block = ""
    if avoid:
        lines = "\n".join(f"  - {a}" for a in list(avoid)[:5])
        avoid_block = f"- Do not repeat these earlier exercises; write something different:\n{lines}\n"
    user = USER_TEMPLATE.format(
        target=info.name, level=level, level_name=LEVEL_NAMES[level], level_rule=LEVEL_RULES[level],
        scenario=scenario, focus=focus, signature=info.signature or "(see documentation)",
        doc=info.doc or "(no documentation)", call_form=info.call_form,
        imports=", ".join(imports), avoid_block=avoid_block, fewshot=FEWSHOT)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    if repair:
        previous, problem = repair
        messages.append({"role": "assistant", "content": previous})
        messages.append({"role": "user", "content": REPAIR_TEMPLATE.format(problem=problem)})
    return messages
