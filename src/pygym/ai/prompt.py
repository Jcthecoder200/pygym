"""What we ask the model: the JSON schema, the messages and the variety lists."""
from __future__ import annotations

import json
from typing import Dict, List, Optional, Sequence, Tuple

from .. import sandbox
from .._runner import ALLOWED_IMPORTS, SAFE_MODULES
from .targets import ALIASES, TargetInfo

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

FILES_SCHEMA = {"type": "object", "additionalProperties": {"type": "string"}}
FIXTURES_SCHEMA = {"type": "object", "additionalProperties": {
    "type": "object",
    "properties": {"status": {"type": "integer"}, "json": {}, "text": {"type": "string"}},
}}


def schema_for(info: TargetInfo) -> dict:
    """SCHEMA, plus "http_fixtures" (required) for network libraries and "files" (optional) for
    targets that work with files. Other targets keep the plain SCHEMA: small models do better
    with fewer fields."""
    if not (info.needs_http or info.needs_files):
        return SCHEMA
    schema = json.loads(json.dumps(SCHEMA))
    if info.needs_files:
        schema["properties"]["files"] = FILES_SCHEMA
    if info.needs_http:
        schema["properties"]["http_fixtures"] = FIXTURES_SCHEMA
        schema["required"].append("http_fixtures")
    return schema


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
- No input(), print(){no_io} or randomness. No type hints. Return a value.
- tests: 4 to 6 test cases. Each test case maps every parameter name to its value,
  e.g. for def total(prices, tax) a test case is {{"prices": [1.5, 2], "tax": 0.1}}.
  Use only JSON values (lists, not tuples). Include one edge case (empty list, single item...).
  The test cases must give different results.
- hint: one sentence that points at `{target}` without giving the answer.
{library_block}{avoid_block}
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

JSON_INPUTS = ("- Test values are plain JSON (lists, objects, numbers, strings, true/false/null). "
               "If `{target}` needs another type, the function converts its input itself, "
               "e.g. `{example}`. Returning arrays or tables is fine.\n")
FILES_RULE = ("- The code runs in an EMPTY temporary folder. If it needs files, put small text "
              "files in \"files\", e.g. {\"scores.csv\": \"name,points\\nana,3\\n\", "
              "\"shop/a.txt\": \"\"} (simple relative names only), and say in question_text "
              "which files exist. ONLY the files listed there exist (a folder exists when a "
              "file inside it is listed), so let the tests use different listed files or "
              "folders. Use only relative paths: never '/', '..' or '~'.\n")
HTTP_RULE = ("- There is no internet. A local test server runs while the code runs; its address "
             "is the global variable BASE_URL (a string like \"http://127.0.0.1:8000\"). "
             "Describe its answers in \"http_fixtures\", e.g. {\"/users/1\": {\"status\": 200, "
             "\"json\": {\"name\": \"Ana\"}}, \"/hello\": {\"status\": 200, \"text\": "
             "\"hi\"}}. Use simple paths without '?'. The function builds URLs like "
             "BASE_URL + \"/users/1\" (tests pass paths or ids, never full URLs). BASE_URL "
             "already exists: never assign it and never write a port number. question_text "
             "says to use BASE_URL.\n")
CONVERT_EXAMPLES = {"numpy": "np.array(values)", "pandas": "pd.DataFrame(rows)"}

REPAIR_TEMPLATE = ("That exercise was rejected: {problem}. "
                   "Return a corrected JSON object with the same fields.")


def build_messages(info: TargetInfo, level: int, *, scenario: str, focus: str,
                   avoid: Sequence[str] = (), repair: Optional[Tuple[str, str]] = None
                   ) -> List[dict]:
    """Chat messages for one attempt. repair = (previous raw reply, why it was rejected)."""
    level = level if level in LEVEL_NAMES else 1
    imports = sorted(ALLOWED_IMPORTS | set(info.allowed_imports))
    alias = ALIASES.get(info.package)
    if alias:
        imports = [f"{m} (as {alias})" if m == info.package else m for m in imports]
    library_block = ""
    if (info.kind == "module" and info.package not in SAFE_MODULES and not info.needs_http
            and not sandbox._is_stdlib(info.package)):
        example = CONVERT_EXAMPLES.get(info.package, f"turning a list into what {info.name} needs")
        library_block += JSON_INPUTS.format(target=info.name, example=example)
    if info.needs_files:
        library_block += FILES_RULE
    if info.needs_http:
        library_block += HTTP_RULE
    if info.needs_http:
        no_io = ", files outside the current folder, other internet addresses than BASE_URL"
    elif info.needs_files:
        no_io = ", files outside the current folder, network"
    else:
        no_io = ", files, network"
    avoid_block = ""
    if avoid:
        lines = "\n".join(f"  - {a}" for a in list(avoid)[:5])
        avoid_block = f"- Do not repeat these earlier exercises; write something different:\n{lines}\n"
    user = USER_TEMPLATE.format(
        target=info.name, level=level, level_name=LEVEL_NAMES[level], level_rule=LEVEL_RULES[level],
        scenario=scenario, focus=focus, signature=info.signature or "(see documentation)",
        doc=info.doc or "(no documentation)", call_form=info.call_form,
        imports=", ".join(imports), avoid_block=avoid_block, fewshot=FEWSHOT,
        library_block=library_block, no_io=no_io)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
    if repair:
        previous, problem = repair
        messages.append({"role": "assistant", "content": previous})
        messages.append({"role": "user", "content": REPAIR_TEMPLATE.format(problem=problem)})
    return messages
