"""Rebuild the README images that show pygym's real output (besides readme_demo.py's GIF).

    python experiments/readme_shots.py

Writes:
  docs/media/status.svg          `pygym --set-provider openai ...` then `pygym --status`, recorded
                                 with Rich. Needs Ollama on localhost:11434 (its /v1 address).
  experiments/out/pygym_flow.html  a notebook (the error tip, exercise("zip"), a wrong and a right
                                 %%pygym :w) executed in a real kernel and exported to HTML.
                                 Needs: pip install nbconvert nbclient ipykernel

The PNGs docs/media/error_tip.png and docs/media/jupyter_flow.png are screenshots of that HTML
at 1240 px wide (cell 1 for the tip; cells 2-4 with the intro panel hidden for the flow).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "experiments" / "out"
HOME = tempfile.mkdtemp(prefix="pygym-shots-")
os.environ["PYGYM_HOME"] = HOME
os.environ.setdefault("PYGYM_API_KEY", "sk-demo-not-real")   # a dummy: the local server ignores it
os.environ.setdefault("COLORTERM", "truecolor")

CELLS = [
    'from pygym import exercise\n\nnames = ["Ana", "Ben", "Chen"]\n'
    'scores = 87          # oops: should be a list of scores\nreport = dict(zip(names, scores))',
    'exercise("zip")',
    "%%pygym :w\ndef combine(keys, values):\n    return list(zip(keys, values))",
    "%%pygym :w\ndef combine(keys, values):\n    return dict(zip(keys, values))",
]


def status_svg() -> None:
    from rich.console import Console
    from rich.text import Text

    from pygym import menu

    con = Console(record=True, width=92, color_system="truecolor", force_terminal=True,
                  file=open(os.devnull, "w"))
    for argv in (["--set-mode", "ai", "--set-provider", "openai", "--set-base-url",
                  "http://localhost:11434/v1", "--set-model", "qwen2.5:1.5b"], ["--status"]):
        con.print(Text("$ ", style="bold green") + Text("pygym " + " ".join(argv), style="bold"))
        menu.main(argv, console=con)
        con.print()
    title = "pygym  (your own model: --set-provider / --set-base-url, then --status)"
    (ROOT / "docs" / "media" / "status.svg").write_text(con.export_svg(title=title), encoding="utf-8")


def notebook_html() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    nb = {"cells": [{"cell_type": "code", "metadata": {}, "source": c, "outputs": [],
                     "execution_count": None} for c in CELLS],
          "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3",
                                      "language": "python"}},
          "nbformat": 4, "nbformat_minor": 5}
    path = OUT / "pygym_flow.ipynb"
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    env = dict(os.environ, PYGYM_NO_AI="1")
    run = [sys.executable, "-m", "jupyter", "nbconvert"]
    subprocess.run(run + ["--to", "notebook", "--execute", "--allow-errors", "--inplace", str(path)],
                   check=True, env=env)
    subprocess.run(run + ["--to", "html", str(path)], check=True, env=env)


if __name__ == "__main__":
    status_svg()
    notebook_html()
    print(f"wrote docs/media/status.svg and {OUT / 'pygym_flow.html'}")
