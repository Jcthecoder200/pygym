"""Throwaway preview: what the pygym command-line screens could look like with Rich.

NOT part of the package (pygym stays dependency-free for now). Run from the repo root:

    uv run --with rich python experiments/rich_preview.py          # print to terminal
    uv run --with rich python experiments/rich_preview.py --svg    # also save experiments/out/*.svg

It renders static screens from a real exercise in src/pygym/data/exercises.json.
Nothing is graded; the results shown are mock-ups of each state.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from pygym import loader

# Command table as decided by the learner (2026-11-10).
COMMANDS = [
    (":w", "check your code"),
    (":wq", "check, then quit if it passes (asks to reveal the solution if it fails)"),
    (":q", "quit (refuses if you changed code since the last :w)"),
    (":q!", "quit and throw your code away"),
    (":h", "hint"),
    (":help", "the built-in's own documentation"),
    (":s", "skip to another exercise"),
    (":solution", "show the solution"),
]


def intro() -> Panel:
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="bold cyan", no_wrap=True)
    t.add_column()
    for cmd, desc in COMMANDS:
        t.add_row(cmd, desc)
    body = Group(
        Text("Learn a Python built-in without leaving your code.\n"),
        Text("Type your function line by line. A line that is exactly a command runs it;", style="dim"),
        Text("every other line becomes part of your code.\n", style="dim"),
        t,
    )
    return Panel(body, title="[bold]pygym[/]", subtitle="v0.0.0", border_style="cyan", padding=(1, 2))


def exercise_screen(ex) -> Panel:
    header = Text.assemble((f"{ex.target_function}", "bold magenta"), "  ",
                           ("●" * ex.difficulty + "○" * (3 - ex.difficulty), "yellow"),
                           (f"  level {ex.difficulty}", "dim"))
    sig = Syntax(ex.function_signature, "python", theme="monokai", background_color="default")
    return Panel(Group(header, Text(""), Text(ex.question_text), Text(""),
                       Text("Your function should look like:", style="dim"), sig),
                 title=f"[dim]{ex.id}[/]", border_style="magenta", padding=(1, 2))


def typed_code(code: str) -> Panel:
    return Panel(Syntax(code, "python", theme="monokai", line_numbers=True),
                 title="your code (unsaved)", title_align="left", border_style="dim")


def result_fail() -> Group:
    return Group(
        Panel(Text("✗ Not quite right yet. This exercise is about 'zip'. Try using it.", style="bold red"),
              border_style="red"),
        Text.assemble(("Reveal the solution? ", "bold"), ("[y/N] ", "dim")),
    )


def result_pass() -> Panel:
    return Panel(Text("✓ Passed! Progress saved.", style="bold green"), border_style="green")


def help_screen(name: str) -> Panel:
    import builtins
    doc = getattr(builtins, name).__doc__ or "(no documentation)"
    return Panel(Text(doc), title=f":help {name}", border_style="blue", padding=(1, 2))


def hint(ex) -> Panel:
    return Panel(Text(ex.hint or "No hint for this one."), title="hint", border_style="yellow")


def main() -> None:
    svg = "--svg" in sys.argv
    ex = next(e for e in loader.load_all() if e.id == "zip-01")
    bad = f"{ex.function_signature}\n    result = {{}}\n    for i in range(len(keys)):\n        result[keys[i]] = values[i]\n    return result"
    screens = {
        "1_intro": intro(),
        "2_exercise": exercise_screen(ex),
        "3_typing": typed_code(bad),
        "4_fail_wq": result_fail(),
        "5_hint": hint(ex),
        "6_help": help_screen(ex.target_function),
        "7_pass": result_pass(),
    }
    out = Path(__file__).parent / "out"
    for name, renderable in screens.items():
        console = Console(record=svg, width=90)
        console.print(Rule(f"[dim]{name}[/]"))
        console.print(renderable)
        if svg:
            out.mkdir(exist_ok=True)
            console.save_svg(str(out / f"{name}.svg"), title=f"pygym preview: {name}")
    if svg:
        print(f"SVGs written to {out}")


if __name__ == "__main__":
    main()
