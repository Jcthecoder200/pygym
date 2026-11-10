"""Probe: can a small local model write ONE valid pygym exercise?

Asks an Ollama model (POST /api/chat with a JSON schema in "format", i.e. structured outputs) for one
exercise about a target function at a given level, then checks it the way AI mode would:

  1. the reply is valid JSON              4. the reference solution mentions the target
  2. all required fields are present      5. it runs in the sandbox on every test input without errors
  3. the reference defines function_name  6. (info) the target was really called at runtime

The model's code only ever runs in a child process (pygym.grader.run_in_sandbox). For dotted targets
whose module the current sandbox does not allow (e.g. statistics), the same _runner.py is started in a
child process with that one module added to its allowed imports, so we can measure the model and not
the sandbox's import list. That gap is reported as "needs_import".

Usage (stdlib only, needs Ollama on localhost:11434):
  python experiments/llm_probe.py                         default 3 models x 3 targets = 9 calls
  python experiments/llm_probe.py --models qwen2.5:3b --targets zip:2
  python experiments/llm_probe.py --json out.json         also save raw results
"""
from __future__ import annotations

import argparse
import ast
import builtins
import importlib
import inspect
import json
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
from pygym import grader  # noqa: E402
from pygym._runner import ALLOWED_IMPORTS  # noqa: E402

BASE_URL = "http://localhost:11434"
DEFAULT_MODELS = ["qwen2.5:1.5b", "qwen2.5:3b", "llama3.2:3b"]
DEFAULT_TARGETS = ["zip:2", "statistics.median:1", "str.join:3"]
LEVELS = {1: "beginner", 2: "intermediate", 3: "advanced"}
LEVEL_RULES = {
    1: "one short function, 1-3 lines, uses the target directly on simple lists or strings",
    2: "a small realistic task, 3-8 lines, combines the target with one other idea "
       "(a comprehension, a dict, a condition or another built-in)",
    3: "a tricky task, up to 15 lines, uses a less common feature of the target "
       "(an optional argument, an edge case, nested data) and needs careful thinking",
}
SCENARIOS = ["a school gradebook", "a weather station", "a music playlist", "a small shop",
             "a sports league table", "a library catalogue", "a to-do app", "a train timetable",
             "a recipe book", "a game leaderboard", "a fitness tracker", "a chat log"]

# What the model must return. expected_outputs is NOT asked for: we compute it from the reference.
SCHEMA = {
    "type": "object",
    "properties": {
        "question_text": {"type": "string"},
        "function_name": {"type": "string"},
        "function_signature": {"type": "string"},
        "reference_solution": {"type": "string"},
        "test_inputs": {"type": "array", "items": {"type": "array"}},
        "hint": {"type": "string"},
    },
    "required": ["question_text", "function_name", "function_signature",
                 "reference_solution", "test_inputs", "hint"],
}

SYSTEM = (
    "You write short Python practice exercises for first-year university students. "
    "Reply with ONE JSON object only, matching the given schema."
)

USER_TEMPLATE = """Write one exercise that teaches `{target}`.

Level: {level_name} ({level}/3): {level_rule}.
Story idea for the question (optional, keep it short): {scenario}.

What `{target}` is:
  signature: {signature}
  documentation: {doc}

Rules:
- The student writes ONE function. question_text says what it takes and returns, with one example.
- function_signature is exactly the def line, e.g. "def total(prices):".
- reference_solution is the full correct function and MUST call `{call_form}`.
- Only these imports are allowed: {imports}.
- No input(), print(), files, network or randomness. Return a value.
- test_inputs: 4 to 6 test cases. Each test case is the LIST OF ARGUMENTS for one call,
  e.g. for def f(a, b) a test case is [[1, 2], "x"]. Use only JSON values (lists, not tuples).
  Include one edge case (empty list, single item...).
- hint: one sentence that points at `{target}` without giving the answer.
"""

# --fewshot: one worked example (about a different function) showing the exact JSON shape.
FEWSHOT = """
Example of the exact shape (about `max`, NOT your target):
{"question_text": "Write a function 'best_score' that takes a list of (name, score) pairs and returns the
name with the highest score. Example: best_score([["ana", 3], ["bo", 7]]) returns "bo".",
 "function_name": "best_score",
 "function_signature": "def best_score(pairs):",
 "reference_solution": "def best_score(pairs):\\n    return max(pairs, key=lambda p: p[1])[0]",
 "test_inputs": [[[["ana", 3], ["bo", 7]]], [[["x", 1]]], [[["a", 2], ["b", 2], ["c", 1]]], [[["p", -1], ["q", -5]]]],
 "hint": "max() takes a key= function that says what to compare."}
Note: best_score has ONE parameter, so every test case is a list with ONE element (the list of pairs).
"""


# ----- target resolution (same idea as the AI engine design) ------------------------------

def resolve(target: str):
    """'zip' -> builtins.zip, 'str.join' -> str.join, 'statistics.median' -> module attribute."""
    head, _, tail = target.rpartition(".")
    if not head:
        return getattr(builtins, target)
    owner = getattr(builtins, head, None)
    if owner is None:
        owner = importlib.import_module(head)
    return getattr(owner, tail)


def describe(target: str):
    obj = resolve(target)
    try:
        sig = f"{target.rpartition('.')[2]}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        sig = "(see documentation)"
    doc = " ".join((inspect.getdoc(obj) or "").split())[:600]
    return sig, doc


def module_of(target: str):
    head = target.rpartition(".")[0]
    if head and not hasattr(builtins, head):
        return head.split(".")[0]
    return None


def call_form(target: str) -> str:
    head, _, tail = target.rpartition(".")
    if not head:
        return f"{target}(...)"
    if hasattr(builtins, head):
        return f"<a {head}>.{tail}(...)"
    return f"{target}(...)  (after `import {head}`)"


def build_prompt(target: str, level: int, rng: random.Random, fewshot: bool = False) -> str:
    sig, doc = describe(target)
    mod = module_of(target)
    imports = sorted(ALLOWED_IMPORTS | ({mod} if mod else set()))
    text = USER_TEMPLATE.format(target=target, level=level, level_name=LEVELS[level],
                                level_rule=LEVEL_RULES[level], scenario=rng.choice(SCENARIOS),
                                signature=sig, doc=doc, call_form=call_form(target),
                                imports=", ".join(imports))
    return text + FEWSHOT if fewshot else text


# ----- Ollama -----------------------------------------------------------------------------

def chat(model: str, prompt: str, timeout: float = 120.0) -> str:
    body = {
        "model": model,
        "stream": False,
        "format": SCHEMA,
        "options": {"num_predict": 700, "temperature": 0.7},
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(f"{BASE_URL}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())["message"]["content"]


# ----- sandbox ----------------------------------------------------------------------------

def sandbox(code, function_name, target, inputs, extra_module=None, timeout=10.0):
    """Run model code in a child process only. extra_module: allow one more import (probe shim)."""
    short = target.rpartition(".")[2]
    if not extra_module or extra_module in ALLOWED_IMPORTS:
        return grader.run_in_sandbox(code, function_name, short, inputs, timeout)
    shim = ("import sys; sys.path.insert(0, %r); import pygym._runner as r; "
            "r.ALLOWED_IMPORTS.add(%r); r.main()" % (str(SRC), extra_module))
    job = json.dumps({"code": code, "function_name": function_name,
                      "target": short, "inputs": inputs})
    try:
        proc = subprocess.run([sys.executable, "-I", "-c", shim], input=job,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "message": proc.stderr[-300:]}


# ----- checks -----------------------------------------------------------------------------

def check(target: str, raw: str) -> dict:
    out = {"valid_json": False, "fields": False, "defines": False, "mentions": False,
           "runs_clean": False, "needs_import": False, "target_calls": None, "problem": ""}
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        out["problem"] = f"invalid JSON: {e}"
        return out
    out["valid_json"] = True
    missing = [k for k in SCHEMA["required"] if not d.get(k)]
    if missing or not isinstance(d.get("test_inputs"), list) \
            or not all(isinstance(t, list) for t in d["test_inputs"]):
        out["problem"] = f"missing/bad fields: {missing or 'test_inputs'}"
        return out
    out["fields"] = True
    code, fn = d["reference_solution"], d["function_name"]
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        out["problem"] = f"syntax error: {e.msg}"
        return out
    out["defines"] = any(isinstance(n, ast.FunctionDef) and n.name == fn for n in ast.walk(tree))
    out["mentions"] = grader._uses_target(tree, target.rpartition(".")[2])
    if not out["defines"]:
        out["problem"] = f"reference does not define {fn}()"
        return out
    mod = module_of(target)
    res = sandbox(code, fn, target, d["test_inputs"])
    if res.get("status") == "error" and "not allowed" in res.get("message", "") and mod:
        out["needs_import"] = True
        res = sandbox(code, fn, target, d["test_inputs"], extra_module=mod)
    if res.get("status") != "ok":
        out["problem"] = f"sandbox {res.get('status')}: {res.get('message', '')[:120]}"
        return out
    errors = [o["error"] for o in res["outputs"] if "error" in o]
    if errors:
        out["problem"] = f"{len(errors)}/{len(res['outputs'])} tests raised: {errors[0][:100]}"
        return out
    out["runs_clean"] = True
    out["target_calls"] = res.get("target_calls")
    out["expected_outputs"] = [o["value"] for o in res["outputs"]]
    if not out["mentions"]:
        out["problem"] = "reference never mentions the target"
    return out


def _arity(code: str, fn: str):
    """(required positional, max positional or None for *args) of fn in code, or None."""
    for node in ast.walk(ast.parse(code)):
        if isinstance(node, ast.FunctionDef) and node.name == fn:
            pos = node.args.posonlyargs + node.args.args
            required = len(pos) - len(node.args.defaults)
            return required, (None if node.args.vararg else len(pos))
    return None


def salvage(target: str, raw: str) -> dict:
    """Deterministic repair, no model call: rewrap test cases of one-parameter functions,
    then keep only the test cases that run without error. Usable if >= 3 survive and they
    do not all give the same output."""
    try:
        d = json.loads(raw)
        code, fn, tests = d["reference_solution"], d["function_name"], d["test_inputs"]
        ar = _arity(code, fn)
    except Exception:  # noqa: BLE001
        return {"salvaged": False, "kept": 0}
    if ar is None or not isinstance(tests, list):
        return {"salvaged": False, "kept": 0}
    required, most = ar
    fixed = []
    for t in tests:
        if not isinstance(t, list):
            t = [t]
        if most == 1 and len(t) != 1:
            t = [t]
        if len(t) >= required and (most is None or len(t) <= most):
            fixed.append(t)
    mod = module_of(target)
    res = sandbox(code, fn, target, fixed, extra_module=mod) if fixed else {"status": "empty"}
    if res.get("status") != "ok":
        return {"salvaged": False, "kept": 0}
    keep = [(t, o["value"]) for t, o in zip(fixed, res["outputs"]) if "error" not in o]
    distinct = len({json.dumps(v, sort_keys=True) for _, v in keep})
    ok = len(keep) >= 3 and distinct >= 2 and grader._uses_target(ast.parse(code),
                                                                   target.rpartition(".")[2])
    return {"salvaged": ok, "kept": len(keep), "of": len(tests)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    ap.add_argument("--targets", nargs="+", default=DEFAULT_TARGETS, help="name:level")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", help="save raw results to this file")
    ap.add_argument("--fewshot", action="store_true", help="add one worked example to the prompt")
    ap.add_argument("--rescore", help="re-check the replies saved in this --json file (no model calls)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows = []
    if args.rescore:
        for r in json.loads(Path(args.rescore).read_text(encoding="utf-8")):
            r.update(check(r["target"], r["raw"]) if r["raw"] else {})
            rows.append(r)
        args.models = list(dict.fromkeys(r["model"] for r in rows))
    for model in [] if args.rescore else args.models:
        for spec in args.targets:
            target, _, level = spec.rpartition(":")
            prompt = build_prompt(target, int(level), rng, fewshot=args.fewshot)
            t0 = time.monotonic()
            try:
                raw = chat(model, prompt)
            except (urllib.error.URLError, OSError, KeyError, ValueError) as e:
                raw, err = "", f"request failed: {e}"
            else:
                err = ""
            secs = time.monotonic() - t0
            res = check(target, raw) if raw else {"problem": err}
            row = {"model": model, "target": target, "level": int(level), "fewshot": args.fewshot,
                   "seconds": round(secs, 1), "raw": raw, **res}
            rows.append(row)
            ok = "CLEAN" if res.get("runs_clean") else "FAIL "
            print(f"{model:14} {spec:22} {secs:6.1f}s  {ok} {res.get('problem', '')}", flush=True)
    for r in rows:
        r.update(salvage(r["target"], r["raw"]) if r["raw"] else {"salvaged": False})

    print("\nper model: avg s/call | valid JSON+fields | runs clean | clean+on-target | "
          "usable after salvage")
    for model in args.models:
        rs = [r for r in rows if r["model"] == model]
        n = len(rs)
        avg = sum(r["seconds"] for r in rs) / n
        print(f"  {model:14} {avg:5.1f}s | {sum(bool(r.get('fields')) for r in rs)}/{n} | "
              f"{sum(bool(r.get('runs_clean')) for r in rs)}/{n} | "
              f"{sum(bool(r.get('runs_clean') and r.get('mentions')) for r in rs)}/{n} | "
              f"{sum(bool(r.get('salvaged')) for r in rs)}/{n}")
    for r in rows:
        print(f"    {r['model']:14} {r['target']:18} salvage: kept {r.get('kept', 0)}/"
              f"{r.get('of', '?')} -> {'usable' if r.get('salvaged') else 'reject'}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
