"""What each level means, checked on the reference solution's code (ast only, nothing runs).

    beginner (1)      one logical line (a comprehension or `a if c else b` is fine there), or
                      straight-line code: no if/else, no conditional expression, no loop, no
                      comprehension, no match. `and` / `or` inside an expression are allowed:
                      they combine values, they are not a branch the student has to write.
    intermediate (2)  at least one loop (for / while / a comprehension) AND at least one branch
                      (an if statement, `a if c else b`, or an `if` inside a comprehension),
                      in at most MAX_INTERMEDIATE_LINES logical lines.
    advanced (3)      everything intermediate needs (without the line limit), plus data
                      structures or algorithms: MARKERS_NEEDED different markers, or one marker
                      in a solution of at least LINES_WITH_ONE_MARKER logical lines.

Markers (MARKERS): a dict or set the code builds or updates, collections, heapq, bisect, a stack
or queue (.append + .pop), nested loops, recursion, sorting with key=.

Only the student's function counts, plus the helper functions it defines and calls (imports and
other top-level code do not). "Logical lines" are statements: a for line, an if line, an
assignment, a return... (a docstring and the def line itself are not counted).

This module is the one place for these rules: the prompt (describe, example_idea), the
validator (check_level), the content report (tools/validate_exercises.py --levels) and
docs/AI_MODE.md all follow it.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

LEVEL_NAMES = {1: "beginner", 2: "intermediate", 3: "advanced"}
MAX_INTERMEDIATE_LINES = 20
MARKERS_NEEDED = 2
# 7, not 8: a recursive flatten (the hand-written isinstance-03) or grouping items into a dict
# by a key are real algorithms in 7 logical lines; an even/odd counter in a dict (6) is not.
LINES_WITH_ONE_MARKER = 7

# Marker key -> how a message names it. The order is the order messages list them in.
MARKERS: Dict[str, str] = {
    "dict_set": "a dict or set it fills",
    "collections": "collections (Counter, defaultdict, deque...)",
    "heapq": "heapq",
    "bisect": "bisect",
    "stack": "a stack or queue (.append + .pop)",
    "nested": "nested loops",
    "recursion": "recursion",
    "sort_key": "sorting with key=",
}
MARKER_LIST = ("a dict or set it fills, collections (Counter, defaultdict, deque), heapq, "
               "bisect, a stack (.append + .pop), nested loops, recursion, or sorting with key=")

_LOOPS = (ast.For, ast.AsyncFor, ast.While)
_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
_MATCH = getattr(ast, "Match", None)  # Python 3.10+
_PUSH = {"append", "appendleft"}
_POP = {"pop", "popleft"}
_FILL = {"add", "setdefault"}         # set.add, dict.setdefault


@dataclass
class Features:
    """What a solution uses, in words a message can repeat."""
    loops: List[str] = field(default_factory=list)      # "a for loop", "a comprehension"
    branches: List[str] = field(default_factory=list)   # "an if statement", ...
    markers: Set[str] = field(default_factory=set)      # keys of MARKERS
    lines: int = 0

    def marker_words(self) -> List[str]:
        return [MARKERS[k] for k in MARKERS if k in self.markers]


def _scope(tree: ast.AST, fn: ast.FunctionDef) -> List[ast.FunctionDef]:
    """fn, plus the top-level functions it calls (and the ones those call)."""
    top = {n.name: n for n in getattr(tree, "body", [])
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    found, todo = [fn], [fn]
    while todo:
        f = todo.pop()
        for node in ast.walk(f):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                helper = top.get(node.func.id)
                if helper is not None and helper not in found:
                    found.append(helper)
                    todo.append(helper)
    return found


def _imported(tree: ast.AST) -> Dict[str, str]:
    """Local name -> what it is: {"np": "numpy", "Counter": "collections.Counter"}."""
    names: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    names[a.asname] = a.name
                else:
                    top = a.name.split(".")[0]
                    names[top] = top
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                names[a.asname or a.name] = f"{node.module}.{a.name}"
    return names


def _dotted(node: ast.AST, imported: Dict[str, str]) -> str:
    """The full name of what a call calls: "collections.Counter", "sorted", "" if unknown."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return ""
    parts.append(imported.get(node.id, node.id))
    return ".".join(reversed(parts))


def _has_loop(nodes) -> bool:
    for node in nodes:
        for inner in ast.walk(node):
            if isinstance(inner, _LOOPS + _COMPREHENSIONS):
                return True
    return False


def _is_docstring(node: ast.AST) -> bool:
    return (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str))


def analyse(tree: ast.AST, fn: ast.FunctionDef) -> Features:
    """The loops, branches, markers and logical lines of fn and its helpers."""
    feats = Features()
    imported = _imported(tree)
    modules = {local for local, full in imported.items() if "." not in full}
    pushes = pops = False
    for f in _scope(tree, fn):
        for node in ast.walk(f):
            if isinstance(node, ast.stmt) and node is not fn and not _is_docstring(node):
                feats.lines += 1
            if isinstance(node, (ast.For, ast.AsyncFor)):
                feats.loops.append("a for loop")
            elif isinstance(node, ast.While):
                feats.loops.append("a while loop")
            elif isinstance(node, _COMPREHENSIONS):
                feats.loops.append("a comprehension")
                if any(g.ifs for g in node.generators):
                    feats.branches.append("an if inside a comprehension")
            elif isinstance(node, ast.If):
                feats.branches.append("an if statement")
            elif isinstance(node, ast.IfExp):
                feats.branches.append("a conditional expression (x if c else y)")
            elif _MATCH is not None and isinstance(node, _MATCH):
                feats.branches.append("a match statement")

            # markers
            if isinstance(node, (ast.Dict, ast.Set, ast.DictComp, ast.SetComp)):
                feats.markers.add("dict_set")
            elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store):
                feats.markers.add("dict_set")              # d[k] = v, d[k] += 1
            if isinstance(node, _LOOPS) and _has_loop(node.body + node.orelse):
                feats.markers.add("nested")
            elif isinstance(node, _COMPREHENSIONS):
                parts = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
                if len(node.generators) > 1 or _has_loop(parts):
                    feats.markers.add("nested")
            if not isinstance(node, ast.Call):
                continue
            name = _dotted(node.func, imported)
            top = name.split(".")[0]
            last = name.rpartition(".")[2]
            if name in ("dict", "set", "frozenset"):
                feats.markers.add("dict_set")
            elif top in ("collections", "heapq", "bisect"):
                feats.markers.add(top)
            if name == "sorted" or (isinstance(node.func, ast.Attribute) and last == "sort"):
                if any(k.arg == "key" for k in node.keywords):
                    feats.markers.add("sort_key")
            if isinstance(node.func, ast.Name) and node.func.id == f.name:
                feats.markers.add("recursion")
            if isinstance(node.func, ast.Attribute):
                receiver = node.func.value
                on_module = isinstance(receiver, ast.Name) and receiver.id in modules
                if not on_module:
                    if last in _FILL:
                        feats.markers.add("dict_set")
                    pushes = pushes or last in _PUSH
                    pops = pops or last in _POP
    if pushes and pops:
        feats.markers.add("stack")
    return feats


def _unique(items: List[str]) -> List[str]:
    return list(dict.fromkeys(items))


def gaps(tree: ast.AST, fn: ast.FunctionDef, level: int) -> List[str]:
    """What keeps the solution from fitting `level`, in a few words each ([] = it fits):
    "uses a for loop", "no condition", "24 lines (at most 20)", "no data structure or algorithm",
    "only one data structure or algorithm (a dict or set it fills) in 5 lines"."""
    feats = analyse(tree, fn)
    if level == 1:
        # "one liner OR no if/else and no loop": a single line may hold a comprehension or
        # x if c else y; longer code must be straight-line
        if feats.lines <= 1:
            return []
        found = _unique(feats.loops + feats.branches)
        return [f"uses {', '.join(found)} in {feats.lines} lines"] if found else []
    missing = []
    if not feats.loops:
        missing.append("no loop")
    if not feats.branches:
        missing.append("no condition")
    if level == 2:
        if not missing and feats.lines > MAX_INTERMEDIATE_LINES:
            missing.append(f"{feats.lines} lines (at most {MAX_INTERMEDIATE_LINES})")
        return missing
    markers = feats.marker_words()
    if not markers:
        missing.append("no data structure or algorithm")
    elif len(markers) < MARKERS_NEEDED and feats.lines < LINES_WITH_ONE_MARKER:
        missing.append(f"only one data structure or algorithm ({markers[0]}) in {feats.lines} "
                       "lines")
    return missing


def check_level(tree: ast.AST, fn: ast.FunctionDef, level: int) -> Optional[str]:
    """None if the reference solution fits `level`; else a short problem text for the model
    (it names what was missing or too much, and what to do)."""
    found = gaps(tree, fn, level)
    if not found:
        return None
    if level == 1:
        return (f"too complex for beginner: it {found[0]}. A beginner solution is ONE line (a "
                "comprehension or x if c else y is fine there), or a few straight-line lines with "
                "no if/else, no loop and no comprehension; make the task smaller, with the "
                "target used directly on the input")
    if level == 2:
        if found[0][0].isdigit():
            return (f"too long for intermediate: {found[0]}; keep the solution to "
                    f"{MAX_INTERMEDIATE_LINES} lines or fewer")
        return ("too simple for intermediate: the solution needs a loop (for/while or a "
                "comprehension) and a condition (an if statement, x if c else y, or an if "
                f"inside the comprehension); it has {' and '.join(found)}. Make the task go "
                "through a list and decide something for each item")
    hint = ""
    if found[-1].startswith("only one"):
        hint = (f"; add a second one or make the algorithm longer ({LINES_WITH_ONE_MARKER}+ "
                "lines)")
    return ("too simple for advanced: the solution needs a loop, a condition and a data "
            f"structure or algorithm such as {MARKER_LIST}; it has {'; '.join(found)}{hint}. "
            "Make the task bigger, with the target as one step")


_DESCRIPTIONS = {
    1: ("ONE line (e.g. a single return; a comprehension or x if c else y is allowed in that "
        "line), or a few straight-line lines with NO if/else, NO for/while loop and NO "
        "comprehension; the target is used directly on the input"),
    2: ("the solution MUST have a loop (for/while or a comprehension) AND a condition (an if "
        "statement, x if c else y, or an if inside the comprehension), at most "
        f"{MAX_INTERMEDIATE_LINES} lines; the target is used on each item or on the result"),
    3: ("a real algorithmic task. The solution MUST have a loop AND a condition AND use data "
        f"structures or algorithms: at least {MARKERS_NEEDED} of: {MARKER_LIST} (or one of them "
        f"in a solution of {LINES_WITH_ONE_MARKER}+ lines); the target is one necessary step "
        "of it"),
}
_IDEAS = {
    1: "e.g. return the target applied to the input, combined with simple arithmetic or text",
    2: "e.g. go through a list of records with a loop and keep, count or change only the items "
       "that meet a condition, using the target on each",
    3: "e.g. group records into a dict by a key and return the groups sorted with key=, or "
       "walk the input with a stack, using the target inside the loop",
}


def describe(level: int) -> str:
    """The rule of a level in plain words (prompts and docs)."""
    return _DESCRIPTIONS.get(level, _DESCRIPTIONS[1])


def example_idea(level: int) -> str:
    """One short idea of a task that fits the level (for the prompt)."""
    return _IDEAS.get(level, _IDEAS[1])
