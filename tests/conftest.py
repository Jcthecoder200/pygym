"""Shared test setup: never touch the real ~/.pygym and never reach a real LLM.

Tests that exercise the AI engine unset PYGYM_NO_AI themselves and inject a fake provider.

Two background behaviours are quiet unless a test asks for them, so the fake provider's queued
replies all go to the exercise the test is about:
  - prefetch (Session writes the next exercise in a thread): off; pass Session(prefetch=True) or
    use the `real_prefetch_setting` fixture.
  - the use-case call (one model call per target before its first exercise): every target gets
    STUB_USE_CASES; the `real_usecases` fixture brings the real lookup back.

The level rules (ai/levels.py: a level-3 reference needs loops, conditions and a data structure)
are off too, so the many small fake replies of the older tests (one-liners at level 2) keep
testing what they test; the `real_levels` fixture turns them on (every level test uses it).
"""
import pytest

from pygym import session as session_module
from pygym.ai import levels, providers, usecases

STUB_USE_CASES = ["a shop's daily sales", "scores of a class test", "temperatures of a week"]
_REAL_LOAD = usecases.load
_REAL_CHECK_LEVEL = levels.check_level
_REAL_PREFETCH_SETTING = session_module._prefetch_setting


@pytest.fixture(autouse=True)
def _rich_is_not_a_legacy_windows_console(monkeypatch):
    """Under pytest's output capture Rich (on Windows) decides it is a legacy console and draws square
    boxes and "-" bars instead of ╭ and ━. The tests describe a modern terminal."""
    import rich.console
    monkeypatch.setattr(rich.console, "detect_legacy_windows", lambda: False, raising=False)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path / "pygym-home"))
    monkeypatch.setenv("PYGYM_NO_AI", "1")


@pytest.fixture(autouse=True)
def _quiet_background(monkeypatch):
    monkeypatch.setattr(session_module, "_prefetch_setting", lambda: False)
    monkeypatch.setattr(usecases, "load", lambda target: list(STUB_USE_CASES))
    monkeypatch.setattr(providers, "_thinking", {})
    monkeypatch.setattr(levels, "check_level", lambda tree, fn, level: None)


@pytest.fixture
def real_levels(monkeypatch):
    """The real level rules (levels.check_level) in the validator."""
    monkeypatch.setattr(levels, "check_level", _REAL_CHECK_LEVEL)


@pytest.fixture
def real_usecases(monkeypatch):
    """The real use-case lookup: saved file, else one model call."""
    monkeypatch.setattr(usecases, "load", _REAL_LOAD)


@pytest.fixture
def real_prefetch_setting(monkeypatch):
    """Session(prefetch=None) reads config.Settings.prefetch again."""
    monkeypatch.setattr(session_module, "_prefetch_setting", _REAL_PREFETCH_SETTING)
