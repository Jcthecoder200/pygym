"""Build the README demo GIF from REAL pygym frames (no screen recorder needed).

    python experiments/readme_demo.py            # writes docs/media/demo.gif and docs/media/menu.svg

The menu, the editor and the results window are pygym's own renderers, driven by scripted
keys; the AI part asks the real local model (Ollama) for a statistics.median exercise and
records the progress bar while it works. Needs: rsvg-convert and ImageMagick (`magick`).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = tempfile.mkdtemp(prefix="pygym-readme-")
os.environ["PYGYM_HOME"] = HOME
os.environ.setdefault("COLORTERM", "truecolor")

from rich.console import Console          # noqa: E402
from rich.text import Text                # noqa: E402

from pygym import config, editor, menu    # noqa: E402
from pygym.session import Session         # noqa: E402

W, H = 88, 26
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "media"
WORK = Path(tempfile.mkdtemp(prefix="pygym-frames-"))
frames = []   # (png path, delay in 1/100 s)


def svg_of(rows, cursor=None, title="pygym") -> str:
    con = Console(record=True, width=W, color_system="truecolor", force_terminal=True,
                  file=open(os.devnull, "w"))
    for n, row in enumerate(rows, 1):
        row = row.copy()
        if cursor and n == cursor[0]:
            col = cursor[1] - 1
            if col >= len(row.plain):
                row.append(" ")
            row.stylize("reverse", col, col + 1)
        con.print(row, end="\n", overflow="crop", no_wrap=True)
    return con.export_svg(title=title)


def add(svg: str, delay: int) -> None:
    n = len(frames)
    s, p = WORK / f"f{n:04d}.svg", WORK / f"f{n:04d}.png"
    s.write_text(svg, encoding="utf-8")
    subprocess.run(["rsvg-convert", "-w", "1100", "-o", str(p), str(s)], check=True)
    frames.append((p, delay))


def snap(ed, delay=12):
    rows, cursor = editor._editor_frame(ed, W, H)
    add(svg_of(rows, cursor), delay)


def keys(ed, seq, every=3, delay=8):
    for i, k in enumerate(seq):
        ed.feed(k)
        if i % every == every - 1:
            snap(ed, delay)
    snap(ed, delay)


def typed(text):
    return [("ENTER" if c == "\n" else c) for c in text]


def menu_frame() -> str:
    answers = iter(["builtins", "zip", "1", "q"])
    con = Console(record=True, width=W, color_system="truecolor", force_terminal=True,
                  file=open(os.devnull, "w"))

    def ask(prompt):
        answer = next(answers)
        con.print(Text(prompt + answer))
        return answer
    menu.run_menu(console=con, input_fn=ask, start_fn=lambda choice, c: 0)
    return con.export_svg(title="pygym  (main menu)")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    config.save(config.Settings(mode="preset", prefetch=False))

    menu_svg = menu_frame()
    (OUT / "menu.svg").write_text(menu_svg, encoding="utf-8")

    # 1. a preset exercise: wrong answer with a print, :w, results window, fix, :w
    ses = Session("zip", 1, mode="preset", prefetch=False)
    ed = editor.Editor(ses, W, H)
    ed.load_exercise()
    snap(ed, 250)
    keys(ed, ["i"] + typed('print("pairs", keys)\nreturn dict(zip(keys, keys))'), every=4)
    keys(ed, ["ESC", ":", "w"], every=1, delay=30)
    ed.feed("ENTER")
    snap(ed, 400)                                     # the results window (failed)
    keys(ed, ["d", "d", "o"], every=1, delay=40)
    keys(ed, typed("return dict(zip(keys, values))"), every=5)
    keys(ed, ["ESC", ":", "w"], every=1, delay=30)
    ed.feed("ENTER")
    snap(ed, 350)                                     # passed
    keys(ed, [":", "l", "v"], every=1, delay=30)
    ed.feed("ENTER")
    snap(ed, 250)

    # 2. AI mode: a real model writes the next exercise; record the progress bar
    config.save(config.Settings(mode="ai", prefetch=False))
    ai_ses = Session("statistics.median", 1, mode="ai", prefetch=False)
    ed = editor.Editor(ai_ses, W, H, background=True)
    ed.load_exercise()
    ed._show_notice()
    snap(ed, 300)
    keys(ed, [":", "s"], every=1, delay=30)
    ed.feed("ENTER")
    started = time.monotonic()
    while ed.job is not None and time.monotonic() - started < 90:
        ed.tick()
        snap(ed, 50)                                  # one frame per half second of real time
        time.sleep(0.5)
    ed.tick()
    snap(ed, 500)

    gif = OUT / "demo.gif"
    cmd = ["magick"]
    for png, delay in frames:
        cmd += ["-delay", str(delay), str(png)]
    cmd += ["-loop", "0", "-layers", "Optimize", "-colors", "128", str(gif)]
    subprocess.run(cmd, check=True)
    print(f"{len(frames)} frames -> {gif} ({gif.stat().st_size // 1024} KB); {OUT / 'menu.svg'}")
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
