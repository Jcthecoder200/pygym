"""Runs in a SEPARATE process. Reads a JSON job on stdin, writes a JSON result to stdout.

Job:    {code, function_name, target, inputs}
Result: {status: "ok", outputs: [...], target_calls: int}  |  {status: "error", message: str}
"""
import builtins
import io
import json
import sys

ALLOWED_IMPORTS = {"math", "itertools", "functools", "collections", "operator", "string"}
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


def main():
    job = json.loads(sys.stdin.read())
    real_out = sys.stdout
    _limits()

    safe = dict(vars(builtins))
    for name in REMOVED_BUILTINS:
        safe.pop(name, None)
    real_import = builtins.__import__

    def safe_import(name, *a, **k):
        if name.split(".")[0] not in ALLOWED_IMPORTS:
            raise ImportError(f"import of '{name}' is not allowed in pygym")
        return real_import(name, *a, **k)

    safe["__import__"] = safe_import

    # Record real calls to the target built-in (stops "dead code" cheating).
    calls = {"n": 0}
    target = job["target"]
    checked = target in safe
    if checked:
        original = safe[target]

        def recorder(*a, **k):
            calls["n"] += 1
            return original(*a, **k)

        safe[target] = recorder

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
