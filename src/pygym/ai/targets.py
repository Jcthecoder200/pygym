"""Turn what the student typed ("zip", "str.join", "statistics.median") into a TargetInfo.

Three kinds of target:
  builtin  "zip", "divmod"                     getattr(builtins, name)
  method   "str.join", "list.sort"             a method of one of METHOD_OWNERS
  module   "statistics.median", "math.gcd"     a function or class in one of SAFE_MODULES

Only these are allowed. Modules that touch files, processes, the network or the interpreter
(os, sys, subprocess, pathlib, ...) are not, and neither are random/time (their results change
between runs, so expected outputs could not be computed once).
"""
from __future__ import annotations

import builtins
import difflib
import importlib
import inspect
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional, Sequence

from .._runner import METHOD_OWNER_NAMES, REMOVED_BUILTINS, SAFE_MODULES

__all__ = ["SAFE_MODULES", "METHOD_OWNERS", "EXCLUDED_BUILTINS", "TargetInfo", "UnknownTarget",
           "resolve", "libraries", "functions", "suggest"]

METHOD_OWNERS: Dict[str, type] = {name: getattr(builtins, name) for name in METHOD_OWNER_NAMES}

# Built-ins that make bad exercises: removed in the sandbox, print things, change between runs,
# or are about the interpreter itself. Exception classes are excluded separately.
EXCLUDED_BUILTINS: FrozenSet[str] = frozenset(REMOVED_BUILTINS) | frozenset({
    "print", "id", "hash", "globals", "locals", "vars", "dir", "setattr", "delattr",
    "__import__", "super", "object", "type", "property", "staticmethod", "classmethod",
    "aiter", "anext", "copyright", "credits", "license",
})
DOC_LIMIT = 600


@dataclass(frozen=True)
class TargetInfo:
    name: str        # canonical: "zip", "str.join", "statistics.median"
    short: str       # "zip", "join", "median"
    kind: str        # "builtin" | "method" | "module"
    owner: str       # "" | "str" | "statistics"
    signature: str   # "median(data)" or "" when unknown
    doc: str         # cleaned docstring, <= 600 chars, may be ""

    @property
    def allowed_imports(self) -> List[str]:
        return [self.owner] if self.kind == "module" else []

    @property
    def call_form(self) -> str:
        """How the reference solution should call the target (for the prompt)."""
        if self.kind == "builtin":
            return f"`{self.name}(...)`"
        if self.kind == "method":
            return (f"the `.{self.short}()` method of a {self.owner} value, "
                    f"e.g. `x.{self.short}(...)`")
        return f"`{self.name}(...)` after `import {self.owner}`"


class UnknownTarget(ValueError):
    """The target does not exist or is not allowed. `suggestions` lists close names."""

    def __init__(self, message: str, suggestions: Sequence[str] = ()):
        super().__init__(message)
        self.suggestions = list(suggestions)


def _is_exception_class(obj) -> bool:
    return isinstance(obj, type) and issubclass(obj, BaseException)


def _good_callable(name: str, obj) -> bool:
    return (not name.startswith("_") and callable(obj) and not _is_exception_class(obj)
            and not inspect.ismodule(obj))


def _clean(text: str) -> str:
    text = (text or "").strip()
    if text.endswith("()"):
        text = text[:-2].strip()
    if text.startswith("builtins."):
        text = text[len("builtins."):]
    return text


def _squeeze(text: Optional[str]) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= DOC_LIMIT else text[:DOC_LIMIT - 3].rstrip() + "..."


def _signature(obj, short: str) -> str:
    try:
        return f"{short}{inspect.signature(obj)}"
    except (TypeError, ValueError):
        pass
    first = (inspect.getdoc(obj) or "").strip().splitlines()
    if first and "(" in first[0] and len(first[0]) <= 200:
        return first[0].strip()
    return ""


@lru_cache(maxsize=None)
def _module(name: str):
    return importlib.import_module(name)


def libraries() -> List[str]:
    """Where targets come from: "builtins", the method owners, then the safe modules."""
    return ["builtins", *METHOD_OWNERS, *sorted(SAFE_MODULES)]


@lru_cache(maxsize=None)
def _functions(library: str) -> tuple:
    if library == "builtins":
        return tuple(sorted(n for n in dir(builtins)
                            if n not in EXCLUDED_BUILTINS and _good_callable(n, getattr(builtins, n))))
    if library in METHOD_OWNERS:
        owner = METHOD_OWNERS[library]
        return tuple(sorted(f"{library}.{n}" for n in dir(owner)
                            if _good_callable(n, getattr(owner, n))))
    if library in SAFE_MODULES:
        try:
            mod = _module(library)
        except ImportError:
            return ()
        names = getattr(mod, "__all__", None) or dir(mod)
        return tuple(sorted(f"{library}.{n}" for n in set(names)
                            if _good_callable(n, getattr(mod, n, None))))
    return ()


def functions(library: str) -> List[str]:
    """Canonical target names in a library, sorted ([] for an unknown library)."""
    return list(_functions((library or "").strip()))


def _all_names() -> List[str]:
    return [name for lib in libraries() for name in _functions(lib)]


def suggest(text: str, library: Optional[str] = None, n: int = 5) -> List[str]:
    """Close matches for a misspelled target, e.g. "zipp" -> ["zip"]."""
    text = _clean(text)
    if not text:
        return []
    pool = functions(library) if library else _all_names()
    if library and library != "builtins" and "." not in text:
        text = f"{library}.{text}"
    found = difflib.get_close_matches(text, pool, n=n, cutoff=0.6)
    if len(found) < n:  # also match on the short name: "median" -> "statistics.median"
        short = text.rpartition(".")[2]
        by_short = {}
        for full in pool:
            by_short.setdefault(full.rpartition(".")[2], []).append(full)
        for s in difflib.get_close_matches(short, list(by_short), n=n, cutoff=0.8):
            for full in by_short[s]:
                if full not in found:
                    found.append(full)
    return found[:n]


def target_object(info: TargetInfo):
    """The function / method / class a resolved target names (for its full documentation)."""
    if info.kind == "builtin":
        return getattr(builtins, info.name)
    if info.kind == "method":
        return getattr(METHOD_OWNERS[info.owner], info.short)
    return getattr(_module(info.owner), info.short)


def resolve(target: str) -> TargetInfo:
    """Look the target up. Raises UnknownTarget if it doesn't exist or isn't allowed."""
    name = _clean(target)
    if not name:
        raise UnknownTarget("no function name given")
    owner, _, short = name.rpartition(".")
    if not owner:
        obj = getattr(builtins, name, None)
        if obj is None or not _good_callable(name, obj):
            raise UnknownTarget(f"'{name}' is not a built-in function", suggest(name))
        if name in EXCLUDED_BUILTINS:
            raise UnknownTarget(f"'{name}' can't be used for exercises", suggest(name))
        return TargetInfo(name, name, "builtin", "", _signature(obj, name),
                          _squeeze(inspect.getdoc(obj)))
    if owner in METHOD_OWNERS:
        obj = getattr(METHOD_OWNERS[owner], short, None)
        if obj is None or not _good_callable(short, obj):
            raise UnknownTarget(f"'{owner}' has no method '{short}'", suggest(name))
        return TargetInfo(name, short, "method", owner, _signature(obj, short),
                          _squeeze(inspect.getdoc(obj)))
    if owner in SAFE_MODULES:
        try:
            obj = getattr(_module(owner), short, None)
        except ImportError:
            obj = None
        if obj is None or not _good_callable(short, obj):
            raise UnknownTarget(f"'{owner}' has no function '{short}'", suggest(name))
        return TargetInfo(name, short, "module", owner, _signature(obj, short),
                          _squeeze(inspect.getdoc(obj)))
    top = owner.split(".")[0]
    if hasattr(builtins, owner):
        raise UnknownTarget(f"methods of '{owner}' are not supported", suggest(name))
    raise UnknownTarget(f"'{top}' is not allowed in pygym")
