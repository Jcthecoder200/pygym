"""Turn what the student typed ("zip", "str.join", "statistics.median") into a TargetInfo.

Three kinds of target:
  builtin  "zip", "divmod"                     getattr(builtins, name)
  method   "str.join", "list.sort"             a method of one of METHOD_OWNERS
  module   "statistics.median", "numpy.cumsum" a function or class of an installed library

Any installed library that imports inside the sandbox works, except (pygym.sandbox.refusal):
  * the deny list in _runner.DENIED_MODULES: GUI toolkits, things that start programs or
    control Python itself (subprocess, ctypes, sys, ...), random/time (results change between
    runs), pip and pygym itself; and os functions that start or stop programs;
  * libraries about files or the network (os, pathlib, shutil, requests, urllib, ...) when this
    computer has no operating-system sandbox (only the audit hook, e.g. on Windows).
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import difflib
import importlib
import importlib.util
import inspect
import io
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, FrozenSet, List, Optional, Sequence, Tuple

from .. import sandbox
from .._runner import (FILE_MODULES, METHOD_OWNER_NAMES, NETWORK_MODULES, REMOVED_BUILTINS,
                       SAFE_MODULES)

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
# Usual short names, accepted in reference solutions ("np.cumsum" without an import gets one).
ALIASES = {"numpy": "np", "pandas": "pd", "polars": "pl", "networkx": "nx", "sympy": "sp",
           "scipy": "sp", "seaborn": "sns", "statsmodels": "sm"}
# Target names that suggest the function reads or writes files (pandas.read_csv, numpy.loadtxt).
_FILE_WORDS = ("read", "load", "open", "file", "save", "write", "path", "dir", "glob", "walk",
               "exists", "stat", "csv")


@dataclass(frozen=True)
class TargetInfo:
    name: str        # canonical: "zip", "str.join", "statistics.median"
    short: str       # "zip", "join", "median"
    kind: str        # "builtin" | "method" | "module"
    owner: str       # "" | "str" | "statistics"
    signature: str   # "median(data)" or "" when unknown
    doc: str         # cleaned docstring, <= 600 chars, may be ""

    @property
    def package(self) -> str:
        """The top-level package to import: "numpy" for "numpy.linalg.norm", "" if none."""
        return self.owner.split(".")[0] if self.kind == "module" else ""

    @property
    def allowed_imports(self) -> List[str]:
        return [self.package] if self.kind == "module" else []

    @property
    def needs_http(self) -> bool:
        """A network library: the exercise needs http_fixtures and BASE_URL."""
        return self.package in NETWORK_MODULES

    @property
    def needs_files(self) -> bool:
        """A file library or a function that reads/writes files: the exercise may need files."""
        if self.kind != "module":
            return False
        return (self.package in FILE_MODULES or self.package in ("csv", "fileinput", "io")
                or any(w in self.short.lower() for w in _FILE_WORDS))

    @property
    def simple(self) -> bool:
        """Too small for an advanced exercise on its own (bin, abs, len, str.upper): its
        signature has at most one input (a method's own value counts) and nothing optional
        (no default, no *args, no [optional] part), and the documentation shows no other call
        form with more arguments ("range(start, stop[, step])"). Unknown signature: False.
        A simple target is practised at advanced level inside a bigger task (see Session)."""
        counted = _count_inputs(self.signature)
        if counted is None:
            return False
        required, optional = counted
        if optional or required > 1:
            return False
        forms = re.findall(rf"(?<![\w.]){re.escape(self.short)}\(([^()]*)\)", self.doc or "")
        return not any(_is_longer_form(f) for f in forms)

    @property
    def call_form(self) -> str:
        """How the reference solution should call the target (for the prompt)."""
        if self.kind == "builtin":
            return f"`{self.name}(...)`"
        if self.kind == "method":
            return (f"the `.{self.short}()` method of a {self.owner} value, "
                    f"e.g. `x.{self.short}(...)`")
        alias = ALIASES.get(self.package)
        if alias and self.owner == self.package:
            return (f"`{self.name}(...)` after `import {self.package}` "
                    f"(or `{alias}.{self.short}(...)` after `import {self.package} as {alias}`)")
        return f"`{self.name}(...)` after `import {self.package}`"


def _count_inputs(signature: str) -> Optional[Tuple[int, bool]]:
    """"bin(number, /)" -> (1, False); "sorted(iterable, /, *, key=None, reverse=False)" ->
    (1, True); "max(iterable, *[, default=obj, key=func]) -> value" -> (1, True). The pair is
    (required inputs, anything optional). None when the signature is unknown."""
    start = (signature or "").find("(")
    if start < 0:
        return None
    depth, end = 0, -1
    for i in range(start, len(signature)):
        if signature[i] in "([":
            depth += 1
        elif signature[i] in ")]":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    params = signature[start + 1:end]
    try:
        args = ast.parse(f"def _({params}): pass").body[0].args
    except SyntaxError:
        # a documentation form: "max(iterable, *[, default=obj, key=func])"
        parts = [p.strip() for p in params.replace("[", ",[").split(",") if p.strip()]
        parts = [p for p in parts if p not in ("/", "*")]
        required = sum(1 for p in parts if not p.startswith(("*", "[")) and "=" not in p)
        optional = "[" in params or any(p.startswith("*") or "=" in p for p in parts)
        return required, optional
    positional = args.posonlyargs + args.args
    required = len(positional) - len(args.defaults)
    required += sum(1 for d in args.kw_defaults if d is None)
    optional = bool(args.defaults or any(d is not None for d in args.kw_defaults)
                    or args.vararg or args.kwarg)
    return required, optional


def _is_longer_form(params: str) -> bool:
    """"start, stop[, step]" (another way to call it, with more inputs) -> True; an example
    call such as "[1, 3, 5]" or "2796202" -> False."""
    if "," not in params and "[" not in params:
        return False
    names = [p.strip().lstrip("*").split("=")[0].strip() for p in re.split(r"[,\[\]]", params)]
    names = [n for n in names if n and n != "/"]
    return bool(names) and all(n.isidentifier() for n in names)


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
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return importlib.import_module(name)


def _installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except Exception:  # noqa: BLE001  (find_spec imports the parent package and may raise)
        return False


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
    # Any other library that can be a target, except file/network libraries (only listed for
    # suggestions; they still resolve when an OS sandbox is available).
    usable = library in SAFE_MODULES or (
        library.split(".")[0] not in sandbox.SYSTEM_MODULES
        and not sandbox.refusal(library, "audit-hook")[0]
        and all(p.isidentifier() for p in library.split(".")) and _installed(library))
    if usable:
        try:
            mod = _module(library)
        except Exception:  # noqa: BLE001
            return ()
        if not inspect.ismodule(mod):
            return ()
        names = getattr(mod, "__all__", None) or dir(mod)
        return tuple(sorted(f"{library}.{n}" for n in set(names)
                            if isinstance(n, str) and _good_callable(n, getattr(mod, n, None))))
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
    if hasattr(builtins, owner):
        raise UnknownTarget(f"methods of '{owner}' are not supported", suggest(name))
    top = owner.split(".")[0]
    what, why = sandbox.refusal(name)
    if what:
        raise UnknownTarget(f"'{what}' is not allowed in pygym ({why})")
    if not all(p.isidentifier() for p in owner.split(".")):
        raise UnknownTarget(f"'{owner}' is not a library name")
    if not _installed(top):
        raise UnknownTarget(sandbox.install_hint(top) or f"there is no library called '{top}'")
    try:
        mod = _module(owner)
    except Exception:  # noqa: BLE001
        mod = None
    if mod is None or not inspect.ismodule(mod):
        parent, _, cls = owner.rpartition(".")
        try:
            is_class = bool(parent) and isinstance(getattr(_module(parent), cls, None), type)
        except Exception:  # noqa: BLE001
            is_class = False
        if is_class:
            raise UnknownTarget(f"methods of '{owner}' are not supported; pick a function of "
                                f"'{parent}' instead")
        raise UnknownTarget(f"the library '{owner}' could not be imported")
    obj = getattr(mod, short, None)
    if obj is None or not _good_callable(short, obj):
        raise UnknownTarget(f"'{owner}' has no function '{short}'", suggest(name, library=owner))
    ok, problem = sandbox.importable(top)
    if not ok:
        raise UnknownTarget(f"'{top}' does not import inside the pygym sandbox ({problem})")
    return TargetInfo(name, short, "module", owner, _signature(obj, short),
                      _squeeze(inspect.getdoc(obj)))
