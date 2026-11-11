"""ai.targets: what the student typed -> TargetInfo (no network, no model)."""
import pytest

from pygym.ai import targets
from pygym.ai.targets import UnknownTarget, resolve


@pytest.mark.parametrize("text, name, short, kind, owner, imports", [
    ("zip", "zip", "zip", "builtin", "", []),
    ("  zip()  ", "zip", "zip", "builtin", "", []),
    ("builtins.len", "len", "len", "builtin", "", []),
    ("str.join", "str.join", "join", "method", "str", []),
    ("list.sort", "list.sort", "sort", "method", "list", []),
    ("statistics.median", "statistics.median", "median", "module", "statistics", ["statistics"]),
    ("itertools.chain", "itertools.chain", "chain", "module", "itertools", ["itertools"]),
])
def test_resolve_kinds(text, name, short, kind, owner, imports):
    info = resolve(text)
    assert (info.name, info.short, info.kind, info.owner) == (name, short, kind, owner)
    assert info.allowed_imports == imports
    assert info.doc and len(info.doc) <= targets.DOC_LIMIT


def test_signatures():
    assert resolve("statistics.median").signature == "median(data)"
    assert resolve("zip").signature.startswith("zip(")
    # no inspect.signature for int(): falls back to the first docstring line
    assert resolve("int").signature.startswith("int(")


def test_call_form_names_the_target():
    assert "zip(" in resolve("zip").call_form
    assert ".join()" in resolve("str.join").call_form
    assert "import statistics" in resolve("statistics.median").call_form


@pytest.mark.parametrize("text, words", [
    ("os.listdir", "not allowed"),
    ("subprocess.run", "not allowed"),
    ("random.choice", "not allowed"),
    ("eval", "can't be used"),
    ("print", "can't be used"),
    ("ValueError", "not a built-in"),
    ("str.nope", "no method"),
    ("statistics.nope", "no function"),
    ("frozenset.union", "not supported"),
    ("", "no function"),
])
def test_unknown_or_unsafe(text, words, monkeypatch):
    # os is only allowed with an OS sandbox; without one (Windows) it is refused.
    monkeypatch.setenv("PYGYM_SANDBOX", "audit-hook")
    with pytest.raises(UnknownTarget) as e:
        resolve(text)
    assert words in str(e.value)
    assert isinstance(e.value, ValueError)


def test_suggestions():
    with pytest.raises(UnknownTarget) as e:
        resolve("zipp")
    assert "zip" in e.value.suggestions
    with pytest.raises(UnknownTarget) as e:
        resolve("statistics.medain")
    assert "statistics.median" in e.value.suggestions
    assert "statistics.median" in targets.suggest("median")
    assert targets.suggest("sumn", library="builtins")[0] == "sum"


def test_libraries_and_functions():
    libs = targets.libraries()
    assert libs[0] == "builtins" and "str" in libs and "statistics" in libs
    assert "os" not in libs and "random" not in libs
    builtins_list = targets.functions("builtins")
    assert "zip" in builtins_list and "sorted" in builtins_list
    for bad in ("print", "open", "eval", "ValueError", "__import__"):
        assert bad not in builtins_list
    assert "str.join" in targets.functions("str")
    assert "statistics.median" in targets.functions("statistics")
    assert targets.functions("os") == [] and targets.functions("nope") == []


def test_every_listed_function_resolves():
    for lib in ("builtins", "str", "statistics", "itertools"):
        for name in targets.functions(lib):
            assert resolve(name).name == name
