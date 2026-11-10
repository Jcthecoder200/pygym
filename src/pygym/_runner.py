"""Runs in a SEPARATE process. Reads a JSON job on stdin, writes a JSON result to stdout.

Job:    {code, function_name, target, inputs, allowed_imports?: [str]}
        target is the full name: "zip", "str.join" or "statistics.median".
Result: {status: "ok", outputs: [...], target_calls: int}  |  {status: "error", message: str}
        target_calls is -1 when the call can't be counted (methods of built-in types, see below).

This file must not import pygym (it is started with `python -I`), so the lists the rest of
pygym shares (SAFE_MODULES, METHOD_OWNER_NAMES) are defined here.

The sandbox is NOT a security boundary: it stops mistakes and casual cheating, not attacks.
"""
import builtins
import importlib
import io
import json
import sys

# Modules any exercise may import.
ALLOWED_IMPORTS = {"math", "itertools", "functools", "collections", "operator", "string"}
# Modules an exercise may additionally unlock through its "allowed_imports" field, and the only
# modules AI mode may write exercises about. Pure computation only: nothing that touches files,
# processes, the network or the interpreter, and nothing random or time-based.
SAFE_MODULES = frozenset({
    "math", "cmath", "statistics", "itertools", "functools", "collections", "operator", "string",
    "re", "textwrap", "heapq", "bisect", "fractions", "decimal", "datetime", "calendar", "json",
    "difflib", "unicodedata", "copy", "array", "keyword",
})
# "str.join" style targets: methods of these built-in types.
METHOD_OWNER_NAMES = ("str", "list", "dict", "set", "tuple", "int", "float", "bytes")
REMOVED_BUILTINS = ["open", "exec", "eval", "compile", "input", "breakpoint",
                    "exit", "quit", "help", "memoryview"]
MEMORY_LIMIT = 512 * 1024 * 1024


def _limits():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT, MEMORY_LIMIT))
    except Exception:
        pass  # Windows / unsupported: the parent's timeout still protects us


def norm(x):
    """Turn any result into a JSON-safe, order-stable structure."""
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if isinstance(x, dict):
        return {str(k): norm(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [norm(i) for i in x]
    if isinstance(x, (set, frozenset)):
        return sorted((norm(i) for i in x), key=repr)
    try:
        return [norm(i) for i in iter(x)]   # zip / map / filter / generators ...
    except TypeError:
        return repr(x)


def _counting(fn, calls):
    def recorder(*a, **k):
        calls["n"] += 1
        return fn(*a, **k)
    return recorder


def _patch_module_target(target, allowed, calls):
    """Replace module.name by a counting wrapper. Returns True if calls can be counted.

    Works for `import statistics; statistics.median(...)` and `from statistics import median`
    because both read the attribute from the (shared) module object after this patch.
    For a class (itertools.chain, collections.Counter) the wrapper is a function, so its public
    attributes are copied over (wrapped too: chain.from_iterable(...) counts as using chain).
    Known limit: isinstance(x, collections.Counter) fails while the class is wrapped.
    """
    module_name, _, name = target.rpartition(".")
    if module_name.split(".")[0] not in allowed:
        return False
    try:
        module = importlib.import_module(module_name)
        original = getattr(module, name)
    except Exception:  # noqa: BLE001
        return False
    if not callable(original):
        return False
    recorder = _counting(original, calls)
    if isinstance(original, type):
        for attr in dir(original):
            if attr.startswith("_"):
                continue
            try:
                value = getattr(original, attr)
                setattr(recorder, attr, _counting(value, calls) if callable(value) else value)
            except Exception:  # noqa: BLE001
                pass
    recorder.__name__ = getattr(original, "__name__", name)
    recorder.__doc__ = getattr(original, "__doc__", None)
    try:
        setattr(module, name, recorder)
    except Exception:  # noqa: BLE001
        return False
    return True


def main():
    job = json.loads(sys.stdin.read())
    real_out = sys.stdout
    _limits()

    allowed = set(ALLOWED_IMPORTS)
    # The intersection is a second lock: a bad exercise file can't unlock "os".
    allowed |= set(job.get("allowed_imports") or ()) & SAFE_MODULES

    safe = dict(vars(builtins))
    for name in REMOVED_BUILTINS:
        safe.pop(name, None)
    real_import = builtins.__import__

    def safe_import(name, *a, **k):
        if name.split(".")[0] not in allowed:
            raise ImportError(f"import of '{name}' is not allowed in pygym")
        return real_import(name, *a, **k)

    safe["__import__"] = safe_import

    # Record real calls to the target (stops "dead code" cheating), by kind:
    #   built-in ("zip"):                 replace the name in the safe built-ins
    #   module function ("statistics.median"): replace the module attribute
    #   method of a built-in type ("str.join"): can't be patched (C types are read-only),
    #                                     so only the parent's static check applies (-1 here)
    calls = {"n": 0}
    target = job["target"]
    owner = target.rpartition(".")[0]
    if not owner:
        checked = target in safe
        if checked:
            safe[target] = _counting(safe[target], calls)
    elif owner in METHOD_OWNER_NAMES:
        checked = False
    else:
        checked = _patch_module_target(target, allowed | SAFE_MODULES, calls)

    sys.stdout = io.StringIO()            # swallow the user's print()s
    try:
        ns = {"__builtins__": safe}
        exec(job["code"], ns)
        fn = ns.get(job["function_name"])
        if not callable(fn):
            raise NameError(f"Your code must define a function named '{job['function_name']}'.")
        outputs = []
        for args in job["inputs"]:
            try:
                outputs.append({"value": norm(fn(*args))})
            except Exception as e:  # noqa: BLE001
                outputs.append({"error": f"{type(e).__name__}: {e}"})
        result = {"status": "ok", "outputs": outputs,
                  "target_calls": calls["n"] if checked else -1}
    except BaseException as e:  # noqa: BLE001
        result = {"status": "error", "message": f"{type(e).__name__}: {e}"}
    finally:
        sys.stdout = real_out
    real_out.write(json.dumps(result))


if __name__ == "__main__":
    main()
