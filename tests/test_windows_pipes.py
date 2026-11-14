"""Things a real Windows 10/11 run of the suite (Python 3.9, 2026) showed.

1. Redirected output on Windows uses the ANSI code page (cp1252; cp950 on Traditional Chinese
   Windows), which cannot show "●" or "✗": the first screen crashed.
2. urllib/requests look for proxy settings in the registry on Windows; the sandbox refuses that and
   used to attach "(pygym blocked: winreg.OpenKey ...)" to the verdict of every HTTP exercise.
"""
import io
import sys

import pytest

from pygym import grader, menu, sandbox


@pytest.mark.parametrize("encoding", ["cp1252", "cp950", "ascii"])
def test_the_first_screen_survives_an_output_encoding_that_cannot_show_it(monkeypatch, tmp_path, encoding):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding=encoding, newline="")      # what a pipe is on Windows
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))
    assert menu.main([]) == 0                                       # no UnicodeEncodeError
    out.flush()
    assert "Bye!" in raw.getvalue().decode(encoding)


def test_a_console_that_is_given_is_left_alone(monkeypatch, tmp_path):
    from rich.console import Console
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    calls = []
    monkeypatch.setattr(menu, "_tolerate_any_encoding", lambda: calls.append(1))
    menu.main(["--version"], console=Console(file=io.StringIO()))
    menu.main([], console=Console(file=io.StringIO()), input_fn=lambda prompt="": "q")
    assert calls == []


def test_the_sandbox_environment_says_no_proxy():
    env = sandbox.child_env()
    assert env["NO_PROXY"] == "*" and env["no_proxy"] == "*"


def test_urllib_inside_the_sandbox_never_goes_looking_for_proxies_elsewhere():
    # With a proxy variable set, urllib.getproxies() answers from the environment and never opens the
    # registry key (the Windows-only fallback that the audit hook refuses).
    code = ("import urllib.request\n"
            "def f():\n    return sorted(urllib.request.getproxies())\n")
    res = grader.run_in_sandbox(code, "f", "urllib.request.urlopen", [[]], 10.0,
                                allowed_imports=["urllib"], level="audit-hook")
    assert res["status"] == "ok" and res["outputs"][0]["value"] == ["no"]
    assert not res.get("blocked")
