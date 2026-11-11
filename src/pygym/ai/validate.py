"""Turn a model's raw reply into a checked Exercise, or raise InvalidExercise.

We never trust the model's claim that its code is right. Its reference solution runs in the
sandbox (a child process, like student code) on its own test inputs, and the values it returns
BECOME the expected outputs. This proves the reference runs and calls the target; it does not
prove the reference matches the question text (that is why every generated exercise carries
ai.WARNING).

LLM-written code is never executed in this process: only ast.parse() is used here. It runs in
pygym.sandbox (a new temporary folder, the audit hook and, where available, an OS sandbox); a
reference that tries something the sandbox blocks is rejected with the reason.
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import importlib.util
import json
import keyword
import re
from typing import Any, Dict, List, Optional, Tuple

from .. import grader, sandbox
from .._runner import ALLOWED_IMPORTS, import_permitted
from ..models import Exercise
from .targets import ALIASES, TargetInfo

MIN_TESTS = 3
MAX_TESTS = 8
MAX_QUESTION = 1200
MAX_REFERENCE = 2500
MAX_TEST_JSON = 2048
MAX_OUTPUT_JSON = 4096
_ANSWER_KEY = re.compile(r"^(expected|expect|result|output|answer|returns?)(_\w+)?$", re.IGNORECASE)
FORBIDDEN_NAMES = {"print", "input", "open", "exec", "eval", "compile", "__import__", "globals",
                   "locals", "vars", "breakpoint"}


class InvalidExercise(Exception):
    """The reply can't be used. repairable=True: worth sending the problem back to the model."""

    def __init__(self, problem: str, repairable: bool = True):
        super().__init__(problem)
        self.problem = problem
        self.repairable = repairable


def _text(d: dict, key: str) -> str:
    value = d.get(key)
    return value.strip() if isinstance(value, str) else ""


def _parse_reference(code: str) -> Tuple[str, ast.Module]:
    """Parse the reference; fix the common 'import x; def f(...)' one-liner on the way."""
    try:
        return code, ast.parse(code)
    except SyntaxError as first:
        fixed = re.sub(r";\s*(def |import |from )", r"\n\1", code)
        if fixed != code:
            try:
                return fixed, ast.parse(fixed)
            except SyntaxError:
                pass
        raise InvalidExercise(f"reference_solution has a syntax error ({first.msg}, "
                              f"line {first.lineno})")


def _top_function(tree: ast.Module, name: str) -> Optional[ast.FunctionDef]:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def _imports(tree: ast.AST) -> List[str]:
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "").split(".")[0])
    return found


class _NoAnnotations(ast.NodeTransformer):
    def visit_arg(self, node):
        node.annotation = None
        return node

    def visit_FunctionDef(self, node):
        node.returns = None
        self.generic_visit(node)
        return node

    def visit_AnnAssign(self, node):
        if node.value is None:
            return None
        return ast.copy_location(ast.Assign(targets=[node.target], value=node.value), node)


def _strip_type_hints(code: str, tree: ast.Module) -> str:
    """Small models like `from typing import List` + type hints. The student doesn't need them
    and typing isn't an allowed import, so drop both (only when typing is imported)."""
    if "typing" not in _imports(tree):
        return code
    tree.body = [n for n in tree.body
                 if not (isinstance(n, ast.Import) and all(a.name == "typing" for a in n.names))
                 and not (isinstance(n, ast.ImportFrom) and n.module == "typing")]
    tree = ast.fix_missing_locations(_NoAnnotations().visit(tree))
    return ast.unparse(tree)


def _add_missing_import(code: str, tree: ast.Module, info: TargetInfo) -> str:
    """'statistics.median(x)' without 'import statistics' -> add the import line
    ('np.cumsum(x)' without an import -> 'import numpy as np')."""
    if info.kind != "module" or info.package in _imports(tree):
        return code
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    if info.package in names:
        return f"import {info.owner}\n{code}"
    alias = ALIASES.get(info.package)
    if alias and alias in names:
        return f"import {info.package} as {alias}\n{code}"
    return code


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except Exception:  # noqa: BLE001
        return False


def _fixtures(data: dict) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """The optional "files" and "http_fixtures" fields, checked. A list of
    {"path": ..., "status": ..., "json": ...} items is accepted for http_fixtures too."""
    files = data.get("files") or {}
    fixtures = data.get("http_fixtures") or {}
    if isinstance(fixtures, list):
        as_dict = {}
        for item in fixtures:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                as_dict[item["path"]] = {k: v for k, v in item.items() if k != "path"}
        fixtures = as_dict
    problems = sandbox.check_files(files) + sandbox.check_fixtures(fixtures)
    if problems:
        raise InvalidExercise(problems[0])
    return files, fixtures


def _returns_value(fn: ast.FunctionDef) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and node.value is not None:
            return True
        if isinstance(node, (ast.Yield, ast.YieldFrom)):
            return True
    return False


def _params(fn: ast.FunctionDef) -> Tuple[List[str], int, bool]:
    """(positional parameter names, how many are required, has *args)."""
    pos = [a.arg for a in fn.args.posonlyargs + fn.args.args]
    required = len(pos) - len(fn.args.defaults)
    return pos, required, fn.args.vararg is not None


def _to_args(test: Any, pos: List[str], required: int, varargs: bool,
             flat: bool = False) -> Optional[list]:
    """One model test case -> a positional argument list, or None if it can't fit.

    flat: the function takes one parameter and the model wrote some tests "flat" ([1, 2, 3]
    instead of [[1, 2, 3]]), so a one-item test like [5] means the list [5], not 5.
    """
    most = None if varargs else len(pos)
    if isinstance(test, dict):
        # Models sometimes add the answer to a test case ({"orders": [...], "expected_result": 3.5}).
        # We compute answers ourselves, so such keys are dropped (unless they are a parameter).
        test = {k: v for k, v in test.items() if k in pos or not _ANSWER_KEY.match(str(k))}
        if pos and all(k in pos for k in test) and all(p in test for p in pos[:required]):
            args = []
            for p in pos:
                if p not in test:
                    break
                args.append(test[p])
            if len(args) == len(test):
                return args
            return None
        if len(pos) == 1 and not varargs and len(test) != 1:
            return [test]                     # the dict itself is the one argument
        values = list(test.values())          # names don't match: fall back to their order
        if len(values) >= required and (most is None or len(values) <= most):
            return values
        return None
    if not isinstance(test, list):
        test = [test]
    elif most == 1 and (len(test) != 1 or (flat and not isinstance(test[0], list))):
        test = [test]                         # def f(numbers) with [1, 2, 3] -> [[1, 2, 3]]
    if len(test) >= required and (most is None or len(test) <= most):
        return test
    return None


def _first_error(outputs: list) -> str:
    for o in outputs:
        if "error" in o:
            return o["error"][:200]
    return ""


# "function 'x'", "function `x`", "function x(" in a question: the function the student must write.
_ASKED_NAME = re.compile(r"\bfunctions?\s+(?:called\s+|named\s+)?[`'\"]?([A-Za-z_]\w*)[`'\"]?\s*(?:\(|[`'\"])")


def names_other_function(question: str, name: str, target: str = "") -> str:
    """The function name the question asks for, when it isn't `name` (else "").

    Small models sometimes write the question for one name and the code for another; the starter
    code always uses the code's name, so such an exercise contradicts itself."""
    ignore = {name, *target.split(".")}           # "the function `cumsum(`" names the target
    for asked in _ASKED_NAME.findall(question or ""):
        if asked not in ignore and not keyword.iskeyword(asked) and not hasattr(builtins, asked):
            return asked
    return ""


def make_id(info: TargetInfo, level: int, reference: str, test_inputs: list) -> str:
    digest = hashlib.sha1((reference + json.dumps(test_inputs, sort_keys=True)).encode("utf-8"))
    return f"ai-{info.name}-{level}-{digest.hexdigest()[:8]}"


def to_exercise(raw: str, info: TargetInfo, level: int, generator: str, *,
                sandbox_timeout: float = 5.0) -> Exercise:
    """Steps 1-10 of the validation pipeline."""
    # 1. JSON
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise InvalidExercise("the reply was not valid JSON", repairable=False)
    if not isinstance(data, dict):
        raise InvalidExercise("the reply was not a JSON object", repairable=False)

    # 2. fields and sizes
    question = _text(data, "question_text")
    reference = data.get("reference_solution")
    reference = reference.replace("\r\n", "\n").strip("\n") if isinstance(reference, str) else ""
    tests = data.get("tests", data.get("test_inputs"))
    if not question:
        raise InvalidExercise("question_text is missing")
    if not reference.strip():
        raise InvalidExercise("reference_solution is missing")
    if not isinstance(tests, list) or len(tests) < MIN_TESTS:
        raise InvalidExercise(f"tests must be a list of 4 to 6 test cases")
    if len(question) > MAX_QUESTION or len(reference) > MAX_REFERENCE:
        raise InvalidExercise("the question or the reference solution is too long")
    tests = tests[:MAX_TESTS]
    files, fixtures = _fixtures(data)

    # 4 (first, so step 3 can use the parsed code). The reference.
    reference, tree = _parse_reference(reference)
    reference = _strip_type_hints(reference, tree)
    reference = _add_missing_import(reference, ast.parse(reference), info)
    tree = ast.parse(reference)

    # 3. function name and signature
    name = _text(data, "function_name")
    if name.startswith("def "):
        name = name[4:].split("(")[0].strip()
    declared = name
    top = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
    if _top_function(tree, name) is None and len(top) == 1:
        name = top[0]                         # the name field disagrees with the code: trust the code
        if declared.isidentifier() and re.search(rf"\b{re.escape(declared)}\b", question):
            # ...and make the question agree, or the student is told to write a function the
            # starter code doesn't have
            question = re.sub(rf"\b{re.escape(declared)}\b", name, question)
    if not name.isidentifier() or keyword.iskeyword(name):
        raise InvalidExercise(f"function_name {name!r} is not a valid Python name")
    if hasattr(builtins, name) or name == info.short:
        raise InvalidExercise(f"function_name '{name}' hides a built-in or the target; "
                              "choose a descriptive name")
    fn = _top_function(tree, name)
    if fn is None:
        raise InvalidExercise(f"reference_solution does not define '{name}' at the top level")
    signature = f"def {name}({ast.unparse(fn.args)}):"
    other = names_other_function(question, name, info.name)
    if other:
        raise InvalidExercise(f"the question asks for a function '{other}' but the code defines "
                              f"'{name}'; use the same name in both")

    if not grader._uses_target(tree, info.name):
        raise InvalidExercise(f"reference_solution must call {info.call_form}")
    # Besides the defaults and the target's library, the reference may import any other library
    # the import policy allows (not a denied or file/network one); the student may then too.
    allowed = ALLOWED_IMPORTS | set(info.allowed_imports)
    extra = sorted({m for m in _imports(tree) if m not in allowed
                    and import_permitted(m, info.name) and _installed(m)})
    bad = sorted({m for m in _imports(tree) if m not in allowed and m not in extra})
    if bad:
        raise InvalidExercise(f"import of {', '.join(bad)} is not allowed "
                              f"(allowed: {', '.join(sorted(allowed))})")
    allowed_imports = list(info.allowed_imports) + extra
    forbidden = FORBIDDEN_NAMES - ({"open"} if files else set())
    used = sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} & forbidden)
    if used == ["open"] and info.needs_files:
        raise InvalidExercise("reference_solution uses open() but \"files\" is empty: list "
                              "every file the code reads in \"files\" ({\"name.txt\": \"text\"})")
    if used:
        raise InvalidExercise(f"reference_solution must not use {', '.join(used)}; "
                              "return the result instead")
    if not _returns_value(fn):
        raise InvalidExercise(f"'{name}' must return a value")
    if any(isinstance(n, ast.Name) and n.id == "BASE_URL" and isinstance(n.ctx, ast.Store)
           for n in ast.walk(tree)):
        raise InvalidExercise("do not assign BASE_URL: it already holds the test server's "
                              "address, use it as it is")

    # 5. tests -> positional argument lists
    pos, required, varargs = _params(fn)
    flat = (len(pos) == 1 and not varargs
            and any(isinstance(t, list) and len(t) != 1 for t in tests))
    inputs, seen = [], set()
    for t in tests:
        args = _to_args(t, pos, required, varargs, flat)
        if args is None:
            continue
        key = json.dumps(args, sort_keys=True)
        if key in seen or len(key) > MAX_TEST_JSON:
            continue
        seen.add(key)
        inputs.append(args)
    if len(inputs) < MIN_TESTS:
        raise InvalidExercise(f"fewer than {MIN_TESTS} test cases match the parameters of "
                              f"'{name}' ({', '.join(pos) or 'none'}); give one value per parameter")

    # 6. run the reference in the sandbox; its return values become the expected outputs
    res = grader.run_in_sandbox(reference, name, info.name, inputs, sandbox_timeout,
                                allowed_imports=allowed_imports, files=files,
                                http_fixtures=fixtures)
    if res.get("blocked"):
        raise InvalidExercise(f"the reference solution tried something pygym does not allow "
                              f"({res['blocked'][0]}); use only the current folder"
                              + (" and BASE_URL" if fixtures or info.needs_http else ""))
    if res.get("status") == "timeout":
        raise InvalidExercise("the reference solution ran too long (infinite loop?)")
    if res.get("status") != "ok":
        raise InvalidExercise(f"the reference solution crashed: {res.get('message', '')[:200]}")
    kept = [(args, o["value"]) for args, o in zip(inputs, res["outputs"]) if "error" not in o]
    if len(kept) < MIN_TESTS:
        raise InvalidExercise(f"the reference solution crashed on the tests: "
                              f"{_first_error(res['outputs'])}")
    # 7. the target is really called (-1: methods of built-in types can't be counted)
    if not grader.target_called(res.get("target_calls"), tree, info.name):
        raise InvalidExercise(f"reference_solution must really call {info.call_form}, "
                              "not just mention it")

    # 9. output quality
    test_inputs = [a for a, _ in kept]
    expected = [v for _, v in kept]
    as_json = [json.dumps(v, sort_keys=True) for v in expected]
    if len(set(as_json)) < 2:
        raise InvalidExercise("all test cases give the same result; use tests with "
                              "different answers")
    if any(" at 0x" in j for j in as_json):
        raise InvalidExercise("a result is an object, not a value; return lists, numbers "
                              "or strings")
    if any(len(j) > MAX_OUTPUT_JSON for j in as_json):
        raise InvalidExercise("a result is too large")

    hint = _text(data, "hint") or f"Use {info.call_form}."
    if not re.search(rf"\b{re.escape(info.short)}\b", question):
        question = f"{question.rstrip()}\nUse `{info.name}`."
    if fixtures and "BASE_URL" not in question:
        question = (f"{question.rstrip()}\nThe test server's address is in the variable "
                    "`BASE_URL` (a string like \"http://127.0.0.1:8000\").")
    missing = [f for f in files if f not in question]
    if missing:
        question = f"{question.rstrip()}\nFiles in the current folder: {', '.join(sorted(files))}."
    ex = Exercise(
        id=make_id(info, level, reference, test_inputs), target_function=info.name,
        difficulty=level, question_text=question, function_name=name,
        function_signature=signature, reference_solution=reference, test_inputs=test_inputs,
        expected_outputs=expected, hint=hint, allowed_imports=allowed_imports,
        source="ai", generator=generator, files=files, http_fixtures=fixtures)
    problems = grader.check_exercise(ex)
    if problems:
        raise InvalidExercise(problems[0])

    # 8 + 10. a second, independent run through the real grader: the reference must pass.
    # A new process has a new hash seed, so set-order dependent code fails here.
    verdict = grader.grade(reference, ex, timeout=sandbox_timeout)
    if not verdict.passed:
        if verdict.reason == "wrong_output":
            raise InvalidExercise("the reference solution gives different results on "
                                  "different runs", repairable=False)
        raise InvalidExercise(f"the reference solution does not pass its own tests "
                              f"({verdict.message})", repairable=False)
    return ex
