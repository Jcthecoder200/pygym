"""Shared test setup: never touch the real ~/.pygym and never reach a real LLM.

Tests that exercise the AI engine unset PYGYM_NO_AI themselves and inject a fake provider.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path / "pygym-home"))
    monkeypatch.setenv("PYGYM_NO_AI", "1")
