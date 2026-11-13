"""Highlighted code must not carry an explicit "default" background in a notebook.

Rich exports HTML in a notebook, and there "default" becomes white (its light terminal theme):
light text on a white box on a dark notebook (seen on Colab). A terminal keeps "default" so the
code sits on the terminal's own background.
"""
from rich.color import ColorType
from rich.console import Console

from pygym import style

SOURCE = "def combine(keys, values):\n    return dict(zip(keys, values))"


def _backgrounds(jupyter):
    console = Console(width=60, force_terminal=True, color_system="truecolor")
    console.is_jupyter = jupyter
    segments = console.render(style.code(SOURCE), console.options)
    return [seg.style.bgcolor for seg in segments if seg.style and seg.style.bgcolor]


def test_terminal_keeps_the_terminals_own_background():
    colors = _backgrounds(jupyter=False)
    assert colors and all(c.type == ColorType.DEFAULT for c in colors)


def test_notebook_gets_the_themes_background_not_default():
    colors = _backgrounds(jupyter=True)
    assert colors and all(c.type != ColorType.DEFAULT for c in colors)


def test_the_code_box_is_laid_out_like_a_plain_syntax_box():
    from rich.panel import Panel
    from rich.syntax import Syntax
    console = Console(width=60, force_terminal=True)

    def widths(renderable):
        lines = console.render_lines(Panel(renderable, expand=False), console.options, pad=False)
        return [sum(len(seg.text) for seg in line) for line in lines]

    plain = Syntax(SOURCE, "python", theme=style.CODE_THEME, background_color="default")
    assert widths(style.code(SOURCE)) == widths(plain)
    assert max(widths(style.code(SOURCE))) < 60          # shrinks to the code: __rich_measure__ works
