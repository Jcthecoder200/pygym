"""Static check that the code still runs on Python 3.9 (pyproject: requires-python >= 3.9).

  python tools/check_py39.py            scan src/, tools/, tests/ and the reference solutions in
                                        data/exercises.json; exit 1 when something is found

It runs on ANY Python (it never imports the code it scans), so CI on 3.12 can guard the 3.9
promise. It is a heuristic: it catches the usual slips, not every possible one. The real test is
still running the suite on a 3.9 interpreter.

What it looks for:
  1. syntax newer than 3.9 (ast.parse with feature_version=(3, 9): match, except*, ...);
  2. `X | Y` types that are evaluated at run time (in a module without
     `from __future__ import annotations`, or inside isinstance()/a plain assignment);
  3. standard-library names and keyword arguments added in 3.10 or later (table below).
"""
import ast
import json
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]

# "module.name" -> first Python version that has it.
NEW_NAMES: Dict[str, str] = {
    "itertools.pairwise": "3.10", "itertools.batched": "3.12",
    "sys.stdlib_module_names": "3.10", "sys.orig_argv": "3.10",
    "math.cbrt": "3.11", "math.exp2": "3.11", "math.nextafter": "3.9", "math.sumprod": "3.12",
    "statistics.correlation": "3.10", "statistics.covariance": "3.10",
    "statistics.linear_regression": "3.10",
    "types.NoneType": "3.10", "types.EllipsisType": "3.10", "types.UnionType": "3.10",
    "types.NotImplementedType": "3.10",
    "typing.TypeAlias": "3.10", "typing.ParamSpec": "3.10", "typing.Concatenate": "3.10",
    "typing.TypeGuard": "3.10", "typing.Self": "3.11", "typing.LiteralString": "3.11",
    "typing.Never": "3.11", "typing.assert_never": "3.11", "typing.reveal_type": "3.11",
    "typing.Required": "3.11", "typing.NotRequired": "3.11", "typing.Unpack": "3.11",
    "typing.TypeVarTuple": "3.11", "typing.override": "3.12",
    "enum.StrEnum": "3.11", "datetime.UTC": "3.11", "tomllib": "3.11",
    "contextlib.chdir": "3.11", "contextlib.aclosing": "3.10",
    "asyncio.timeout": "3.11", "asyncio.TaskGroup": "3.11", "asyncio.Runner": "3.11",
    "inspect.get_annotations": "3.10", "logging.getLevelNamesMapping": "3.11",
    "builtins.aiter": "3.10", "builtins.anext": "3.10", "builtins.EncodingWarning": "3.10",
    "builtins.ExceptionGroup": "3.11", "builtins.BaseExceptionGroup": "3.11",
    "ast.Match": "3.10", "ast.match_case": "3.10", "ast.MatchValue": "3.10",
    "ast.MatchAs": "3.10", "ast.MatchOr": "3.10", "ast.MatchSequence": "3.10",
    "ast.MatchMapping": "3.10", "ast.MatchClass": "3.10", "ast.MatchStar": "3.10",
    "ast.MatchSingleton": "3.10", "ast.TryStar": "3.11",
    "random.binomialvariate": "3.12", "zipfile.Path": "3.8",
}
# method names that exist only on newer versions (matched on any object; a heuristic).
NEW_METHODS = {"bit_count": "3.10", "add_note": "3.11", "is_integer": "3.12 (int)"}
# call keywords: (callee name, keyword) -> version.
NEW_KWARGS = {("zip", "strict"): "3.10", ("dataclass", "slots"): "3.10",
              ("dataclass", "kw_only"): "3.10", ("dataclass", "match_args"): "3.10",
              ("rmtree", "onexc"): "3.12", ("bisect", "key"): "3.10",
              ("bisect_left", "key"): "3.10", ("bisect_right", "key"): "3.10",
              ("insort", "key"): "3.10", ("open", "encoding_errors"): "3.x"}
SKIP_METHODS = {"is_integer"}          # float.is_integer is old; too many false hits


def _aliases(tree: ast.AST) -> Dict[str, str]:
    """local name -> dotted origin, from import / import-from statements."""
    out: Dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                out[(a.asname or a.name).split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            for a in n.names:
                out[a.asname or a.name] = f"{n.module}.{a.name}"
    return out


def _dotted(node: ast.AST, aliases: Dict[str, str]) -> str:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(aliases.get(node.id, node.id))
        return ".".join(reversed(parts))
    return ""


def _has_future_annotations(tree: ast.Module) -> bool:
    return any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names) for n in tree.body)


def _typeish(node: ast.AST) -> bool:
    """Could this expression be a type in `A | B`? (None, a Name, a Subscript, a dotted name)"""
    return (isinstance(node, ast.Constant) and node.value is None) or \
        isinstance(node, (ast.Name, ast.Subscript, ast.Attribute))


def scan_source(source: str, where: str) -> List[str]:
    problems: List[str] = []
    try:
        tree = ast.parse(source, filename=where, feature_version=(3, 9))
    except SyntaxError as e:
        return [f"{where}:{e.lineno}: syntax newer than 3.9: {e.msg}"]
    aliases = _aliases(tree)
    lazy = _has_future_annotations(tree)
    seen: Set[Tuple[int, str]] = set()

    def add(node, msg):
        key = (getattr(node, "lineno", 0), msg)
        if key not in seen:
            seen.add(key)
            problems.append(f"{where}:{key[0]}: {msg}")

    # 2. `X | Y` evaluated at run time
    annotation_nodes: Set[int] = set()
    for n in ast.walk(tree):          # `if sys.version_info >= (3, 12): <new API>` is guarded
        if (isinstance(n, ast.If) and "version_info" in ast.dump(n.test)
                and isinstance(n.test, ast.Compare)
                and isinstance(n.test.ops[0], (ast.GtE, ast.Gt))):
            for stmt in n.body:
                annotation_nodes.update(id(x) for x in ast.walk(stmt))
    if lazy:      # annotations are never evaluated: skip their whole subtrees
        for n in ast.walk(tree):
            ann = []
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                ann = [a.annotation for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs
                       + [x for x in (n.args.vararg, n.args.kwarg) if x]] + [n.returns]
            elif isinstance(n, ast.AnnAssign) and not isinstance(n.target, ast.Attribute):
                ann = [n.annotation]
            for a in ann:
                if a is not None:
                    annotation_nodes.update(id(x) for x in ast.walk(a))
    for n in ast.walk(tree):
        if id(n) in annotation_nodes:
            continue
        if (isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr)
                and _typeish(n.left) and _typeish(n.right)
                and (isinstance(n.left, ast.Constant) or isinstance(n.right, ast.Constant)
                     or any(isinstance(x, ast.Subscript) for x in (n.left, n.right)))):
            add(n, "`X | Y` type evaluated at run time needs 3.10 (use Optional/Union or "
                   "`from __future__ import annotations` for annotations)")
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id in ("isinstance", "issubclass") and len(n.args) == 2
                and isinstance(n.args[1], ast.BinOp) and isinstance(n.args[1].op, ast.BitOr)):
            add(n, "isinstance(x, A | B) needs 3.10 (use a tuple: isinstance(x, (A, B)))")
        if isinstance(n, ast.ImportFrom) and n.module and not n.level:
            for a in n.names:
                need = NEW_NAMES.get(f"{n.module}.{a.name}")
                if need and need not in ("3.8", "3.9"):
                    add(n, f"{n.module}.{a.name} needs Python {need}")
        # 3. names, methods, keywords
        if isinstance(n, (ast.Attribute, ast.Name)):
            dotted = _dotted(n, aliases) if isinstance(n, ast.Attribute) else aliases.get(n.id, n.id)
            if dotted in NEW_NAMES and NEW_NAMES[dotted] not in ("3.8", "3.9"):
                add(n, f"{dotted} needs Python {NEW_NAMES[dotted]}")
        if isinstance(n, ast.Attribute) and n.attr in NEW_METHODS and n.attr not in SKIP_METHODS:
            add(n, f".{n.attr}() needs Python {NEW_METHODS[n.attr]}")
        if isinstance(n, ast.Call):
            callee = _dotted(n.func, aliases).rsplit(".", 1)[-1] if isinstance(
                n.func, (ast.Attribute, ast.Name)) else ""
            if isinstance(n.func, ast.Name):
                callee = n.func.id
            for kw in n.keywords:
                need = NEW_KWARGS.get((callee, kw.arg))
                if need:
                    add(n, f"{callee}(..., {kw.arg}=) needs Python {need}")
    return problems


def scan_paths(paths: List[Path]) -> List[str]:
    problems: List[str] = []
    for path in paths:
        try:
            problems += scan_source(path.read_text(encoding="utf-8"),
                                    str(path.relative_to(ROOT)))
        except (OSError, UnicodeDecodeError) as e:
            problems.append(f"{path}: cannot read ({e})")
    return problems


def scan_exercises() -> List[str]:
    """Reference solutions run in the student's Python, whatever it is."""
    problems: List[str] = []
    data = json.loads((ROOT / "src" / "pygym" / "data" / "exercises.json").read_text("utf-8"))
    for d in data:
        problems += scan_source(d["reference_solution"], f"exercises.json:{d['id']}")
    return problems


def main() -> int:
    files = sorted(p for folder in ("src", "tools", "tests", "experiments")
                   for p in (ROOT / folder).rglob("*.py"))
    problems = scan_paths(files) + scan_exercises()
    for line in problems:
        print(line)
    print(f"checked {len(files)} files and the exercise solutions: "
          f"{len(problems)} possible Python 3.9 problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
