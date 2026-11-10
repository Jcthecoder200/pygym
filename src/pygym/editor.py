"""A small modal terminal editor (inspired by Neovim) on top of Session.handle(). Linux/macOS.

Like every pygym front-end it has no rules of its own: it keeps a buffer of lines and a cursor,
sends ":" commands to session.handle(command, code) and shows the Response.

The parts, from pure logic to the terminal:
  Buffer       lines + cursor + edit operations
  Editor       the state machine (modes NORMAL / INSERT / COMMAND / PAGER / PROMPT); feed(key)
  render()     turns an Editor into one screen frame (a string of text + ANSI codes); each row
               is a rich.text.Text coloured from style.PALETTE, the code highlighted by Rich
  parse_keys() turns raw bytes from the keyboard into key names ("a", "ESC", "UP", ...)
  run_editor() the only part that touches the terminal (raw mode, alternate screen, read loop)
"""
from __future__ import annotations

import io
import os
import re
import shutil
import signal
import sys
import textwrap
from typing import Dict, List, Optional, Tuple

from rich.color import ColorSystem
from rich.console import Console
from rich.style import Style
from rich.text import Text

from . import style
from .session import COMMANDS, SHORT
from .style import PALETTE

NORMAL, INSERT, COMMAND, PAGER, PROMPT = "NORMAL", "INSERT", "COMMAND", "PAGER", "PROMPT"

INDENT = "    "  # Tab and auto-indent always use 4 spaces, never a tab character
START_HINT = "i to edit · Esc then :w to check · :intro for all commands"
CTRL_C_MESSAGE = "Type :q to quit, :q! to quit without checking"
REVEAL_PROMPT = "Reveal the solution? [y/N] "
ESC_TIMEOUT = 0.05  # seconds to wait after a lone ESC byte (Neovim's ttimeoutlen)
# The question can be longer than one screen line. It is wrapped onto at most this many rows
# (the last row ends with "…" if it still does not fit). Set to 1 for a single truncated line.
HEADER_MAX_ROWS = 3

MOVES = {"UP": (-1, 0), "DOWN": (1, 0), "LEFT": (0, -1), "RIGHT": (0, 1)}
VI_MOVES = {"k": (-1, 0), "j": (1, 0), "h": (0, -1), "l": (0, 1)}


def _leading_ws(line: str) -> str:
    return line[:len(line) - len(line.lstrip())]


def _printable(key: str) -> bool:
    return len(key) == 1 and key.isprintable()


# ----- Buffer ------------------------------------------------------------------------------

class Buffer:
    """The code being edited: a list of lines and a cursor (row, col), both 0-based.

    The column may be anywhere from 0 to len(line) (one past the last character), in every mode.
    """

    def __init__(self, text: str = "") -> None:
        self.lines: List[str] = text.split("\n")
        self.row = 0
        self.col = 0

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def line(self) -> str:
        return self.lines[self.row]

    def _clamp(self) -> None:
        self.row = max(0, min(self.row, len(self.lines) - 1))
        self.col = max(0, min(self.col, len(self.line)))

    def move(self, drow: int, dcol: int) -> None:
        self.row += drow
        self.col += dcol
        self._clamp()

    def home(self) -> None:
        self.col = 0

    def end(self) -> None:
        self.col = len(self.line)

    def insert(self, text: str) -> None:
        line = self.line
        self.lines[self.row] = line[:self.col] + text + line[self.col:]
        self.col += len(text)

    def newline(self, auto_indent: bool = True) -> None:
        """Split the line at the cursor. With auto_indent the new line gets the current line's
        leading whitespace, plus 4 spaces if the text before the cursor ends with ':'."""
        line = self.line
        before, after = line[:self.col], line[self.col:]
        indent = ""
        if auto_indent:
            indent = _leading_ws(line)
            if before.rstrip().endswith(":"):
                indent += INDENT
            if not before.strip():
                before = ""  # don't leave a line that is only indentation behind
            after = after.lstrip(" ")
        self.lines[self.row] = before
        self.lines.insert(self.row + 1, indent + after)
        self.row += 1
        self.col = len(indent)

    def backspace(self) -> None:
        if self.col > 0:
            line = self.line
            self.lines[self.row] = line[:self.col - 1] + line[self.col:]
            self.col -= 1
        elif self.row > 0:  # at column 0: join with the previous line
            prev = self.lines[self.row - 1]
            self.lines[self.row - 1] = prev + self.line
            del self.lines[self.row]
            self.row -= 1
            self.col = len(prev)

    def delete(self) -> None:
        """Delete the character under the cursor; at the end of a line, join the next line."""
        line = self.line
        if self.col < len(line):
            self.lines[self.row] = line[:self.col] + line[self.col + 1:]
        elif self.row < len(self.lines) - 1:
            self.lines[self.row] = line + self.lines.pop(self.row + 1)

    def delete_char(self) -> None:
        """NORMAL-mode x: delete the character under the cursor (nothing at the end of a line)."""
        line = self.line
        if self.col < len(line):
            self.lines[self.row] = line[:self.col] + line[self.col + 1:]
        self._clamp()

    def delete_line(self) -> None:
        """NORMAL-mode dd. The buffer never becomes zero lines: one empty line is kept."""
        del self.lines[self.row]
        if not self.lines:
            self.lines = [""]
        self.row = min(self.row, len(self.lines) - 1)
        self.col = len(_leading_ws(self.line))

    def open_below(self) -> None:
        """NORMAL-mode o: a new line below with the current line's indentation."""
        indent = _leading_ws(self.line)
        self.lines.insert(self.row + 1, indent)
        self.row += 1
        self.col = len(indent)


# ----- layout helpers shared by Editor and render() -----------------------------------------

def _screen(width: int, height: int) -> Tuple[int, int]:
    return max(width, 20), max(height, 5)


def _header_parts(ex) -> List[Tuple[str, str]]:
    """The question line as (text, style) pieces: zip  ●○○  level 1 ─ Write a function ..."""
    question = " ".join(ex.question_text.split())
    badge = [("  ", ""), (style.AI_BADGE, PALETTE["ai_badge"])] if style.is_generated(ex) else []
    return [(ex.target_function, PALETTE["target"]), ("  ", ""),
            (style.dots(ex.difficulty), PALETTE["dots"]),
            (f"  level {ex.difficulty}", PALETTE["muted"]), *badge, (" ─ ", PALETTE["muted"]),
            (question, "")]


def _wrap_header(ex, width: int, height: int) -> Tuple[List[str], bool]:
    """The wrapped question rows, and whether the last one was cut (it then ends with "…")."""
    text = "".join(part for part, _ in _header_parts(ex))
    rows = textwrap.wrap(text, width=width) or [""]
    limit = max(1, min(HEADER_MAX_ROWS, height - 4))
    if len(rows) > limit:
        rows = rows[:limit]
        rows[-1] = rows[-1][:width - 1] + "…"
        return rows, True
    return rows, False


def _header_rows(ex, width: int, height: int) -> List[str]:
    return _wrap_header(ex, width, height)[0]


def _header_texts(ex, width: int, height: int) -> List[Text]:
    """_header_rows() with the colours of the question line kept on every wrapped row."""
    full = Text.assemble(*_header_parts(ex))
    rows, truncated = _wrap_header(ex, width, height)
    out: List[Text] = []
    pos = 0
    for n, row in enumerate(rows, 1):
        cut = truncated and n == len(rows)
        body = row[:-1] if cut else row
        start = full.plain.find(body, pos)  # each row is a piece of the full line, in order
        if start < 0:
            out.append(Text(row))
            continue
        piece = full[start:start + len(body)]
        if cut:
            piece.append("…", style=PALETTE["muted"])
        out.append(piece)
        pos = start + len(body)
    return out


def _visible_top(top: int, row: int, rows: int) -> int:
    """The first line to show so that `row` is on screen, moving `top` as little as possible."""
    top = min(top, row)
    return max(top, row - rows + 1, 0)


def _pager_lines(text: str, width: int) -> List[str]:
    out: List[str] = []
    for line in text.expandtabs(4).split("\n"):
        out.extend([line[i:i + width] for i in range(0, len(line), width)] or [""])
    return out


# ----- Editor (the state machine) -----------------------------------------------------------

class Editor:
    """All editor behaviour, driven by feed(key). No terminal needed (tests use it directly)."""

    def __init__(self, session, width: int = 80, height: int = 24) -> None:
        self.session = session
        self.width, self.height = width, height
        self.mode = NORMAL
        self.message = ""     # the bottom line in NORMAL mode (and over -- INSERT -- until a key)
        self.message_kind = ""  # Response.kind of the message ("" = the editor's own text): its colour
        self.cmdline = ""     # what was typed after ':'
        self.done = False     # True once a Response said quit
        self.farewell = ""    # the text of that last Response (printed after leaving the screen)
        self.pending = ""     # first key of a two-key NORMAL command ("d" of "dd")
        self.pasting = False  # inside a bracketed paste: text is inserted as-is
        self.top = 0          # first buffer line on screen
        self.pager_title = ""
        self.pager_kind = ""  # Response.kind shown in the pager ("help", "solution", ...): its colours
        self.pager_text = ""
        self.pager_top = 0
        self.after_pager = NORMAL  # the mode to return to when the pager closes
        self.buffer = Buffer()
        self.intro_visible = True  # the start screen in the empty area, until the code first changes
        self.load_exercise()
        self.message, self.message_kind = START_HINT, ""
        self._show_notice()

    # -- helpers --

    def _show_notice(self) -> None:
        """Session.notice (AI warning / fallback note) goes on the message line, if there is one."""
        if getattr(self.session, "notice", ""):
            self.message, self.message_kind = self.session.notice, "notice"

    def load_exercise(self) -> None:
        """Reset the buffer to the starter text of the current exercise."""
        ex = self.session.exercise
        self.buffer = Buffer(ex.function_signature + "\n" + INDENT)
        self.buffer.row, self.buffer.col = 1, len(INDENT)
        self.starter_text = self.buffer.text
        self.top = 0
        # The pre-filled starter text counts as "saved", so :q right away is allowed.
        self.session.saved_code = self.buffer.text

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self._follow_cursor()

    def body_rows(self, width: Optional[int] = None, height: Optional[int] = None) -> int:
        w, h = _screen(width or self.width, height or self.height)
        return h - len(_header_rows(self.session.exercise, w, h)) - 2

    def pager_page(self) -> Tuple[List[str], int]:
        w, h = _screen(self.width, self.height)
        return _pager_lines(self.pager_text, w), h - 2

    def _follow_cursor(self) -> None:
        self.top = _visible_top(self.top, self.buffer.row, self.body_rows())
        if self.mode == PAGER:
            lines, page = self.pager_page()
            self.pager_top = max(0, min(self.pager_top, len(lines) - page))

    def open_pager(self, title: str, text: str, after: str = NORMAL, kind: str = "") -> None:
        self.mode = PAGER
        self.pager_title, self.pager_text, self.pager_top = title, text, 0
        self.pager_kind = kind
        self.after_pager = after
        self.message = ""  # the footer shows how to scroll and leave

    def _enter_insert(self) -> None:
        self.mode = INSERT
        self.message = ""

    # -- input --

    def feed(self, key: str) -> None:
        if key == "PASTE_START":
            self.pasting = True
        elif key == "PASTE_END":
            self.pasting = False
        elif key == "CTRL_C":
            # Like Neovim: Ctrl-C never quits, it only explains how to.
            self.pending = ""
            if self.mode in (COMMAND, PROMPT):
                self.mode, self.cmdline = NORMAL, ""
            self.message, self.message_kind = CTRL_C_MESSAGE, ""
        elif self.mode == NORMAL:
            self._normal(key)
        elif self.mode == INSERT:
            self._insert(key)
        elif self.mode == COMMAND:
            self._command(key)
        elif self.mode == PAGER:
            self._pager(key)
        elif self.mode == PROMPT:
            self._prompt(key)
        if self.intro_visible and self.buffer.text != self.starter_text:
            self.intro_visible = False  # the first edit hides the start screen (:intro brings it back)
        self._follow_cursor()

    def _normal(self, key: str) -> None:
        b = self.buffer
        if self.pasting:  # pasted text is text, not commands
            if key == "ENTER":
                b.newline(auto_indent=False)
            elif key == "TAB":
                b.insert(INDENT)
            elif _printable(key):
                b.insert(key)
            return
        if self.pending == "d":
            self.pending = ""
            if key == "d":
                b.delete_line()
                return
            if key == "ESC":
                return
        if key in MOVES or key in VI_MOVES:
            b.move(*(MOVES.get(key) or VI_MOVES[key]))
        elif key == "HOME":
            b.home()
        elif key == "END":
            b.end()
        elif key in ("x", "DELETE"):
            b.delete_char()
        elif key == "i":
            self._enter_insert()
        elif key == "a":
            b.col = min(b.col + 1, len(b.line))
            self._enter_insert()
        elif key == "o":
            b.open_below()
            self._enter_insert()
        elif key == "d":
            self.pending = "d"
        elif key == ":":
            self.mode, self.cmdline = COMMAND, ""
        # anything else is ignored

    def _insert(self, key: str) -> None:
        b = self.buffer
        self.message = ""  # any key brings back -- INSERT --
        if key == "ESC":
            self.mode = NORMAL
        elif key == "ENTER":
            b.newline(auto_indent=not self.pasting)
        elif key == "BACKSPACE":
            b.backspace()
        elif key == "DELETE":
            b.delete()
        elif key == "TAB":
            b.insert(INDENT)
        elif key in MOVES:
            b.move(*MOVES[key])
        elif key == "HOME":
            b.home()
        elif key == "END":
            b.end()
        elif _printable(key):
            b.insert(key)

    def _command(self, key: str) -> None:
        if self.pasting:
            if _printable(key):
                self.cmdline += key
            return
        if key == "ESC":
            self.mode, self.cmdline = NORMAL, ""
        elif key == "BACKSPACE":
            if self.cmdline:
                self.cmdline = self.cmdline[:-1]
            else:
                self.mode = NORMAL  # Backspace on an empty command line cancels it
        elif key == "ENTER":
            self._run_command()
        elif _printable(key):
            self.cmdline += key

    def _run_command(self) -> None:
        typed, self.cmdline = self.cmdline, ""
        self.mode = NORMAL
        if not typed.strip():
            return  # a bare ':' + Enter does nothing, like Neovim
        resp = self.session.handle(":" + typed, self.buffer.text)
        self.show_response(resp)
        if resp.clear_code:
            self.load_exercise()
            self._show_notice()  # a new exercise may be generated (or a fallback): say so
        if resp.quit:
            self.done = True
            self.farewell = ("Hint: " if resp.kind == "hint" else "") + resp.text
        elif resp.ask_reveal:
            if self.mode == PAGER:
                self.after_pager = PROMPT
            else:
                self.mode = PROMPT

    def show_response(self, resp) -> None:
        text = ("Hint: " if resp.kind == "hint" else "") + resp.text
        shown = style.ICONS.get(resp.kind, "") + text  # the message line also shows the icon
        if resp.kind in ("help", "solution") or "\n" in text or len(shown) > self.width:
            titles = {"help": f"Documentation: {self.session.exercise.target_function}",
                      "solution": "Solution", "commands": "pygym commands"}
            self.open_pager(titles.get(resp.kind, "pygym"), text, kind=resp.kind)
        else:
            self.message, self.message_kind = text, resp.kind

    def _pager(self, key: str) -> None:
        if self.pasting:
            return
        lines, page = self.pager_page()
        if key in ("q", "ESC", "ENTER"):
            self.mode, self.after_pager = self.after_pager, NORMAL
            return
        if key in ("DOWN", "j"):
            self.pager_top += 1
        elif key in ("UP", "k"):
            self.pager_top -= 1
        elif key in (" ", "PAGEDOWN"):
            self.pager_top += page
        elif key in ("PAGEUP", "b"):
            self.pager_top -= page
        elif key in ("HOME", "g"):
            self.pager_top = 0
        elif key in ("END", "G"):
            self.pager_top = len(lines)
        self.pager_top = max(0, min(self.pager_top, len(lines) - page))

    def _prompt(self, key: str) -> None:
        if self.pasting:
            return
        self.mode = NORMAL
        if key in ("y", "Y"):
            self.open_pager("Solution", self.session.solution(), kind="solution")


# ----- render --------------------------------------------------------------------------------
#
# Every row is built as a rich.text.Text; one Console per (colour depth, width, NO_COLOR) turns the
# rows into ANSI codes. Text.cell_len is the visible width of a row.

# The editor's message line: like style.LINE_STYLES, but a one-line hint is cyan.
_MESSAGE_STYLES = dict(style.LINE_STYLES, hint=PALETTE["hint_line"])
_MODE_STYLES = {NORMAL: "mode_normal", INSERT: "mode_insert", COMMAND: "mode_command"}
_PAGER_COLORS = {"help": PALETTE["help"], "solution": PALETTE["solution"]}
# With NO_COLOR the status line falls back to reverse video, so it still stands out.
_NO_COLOR_STYLES = {"mode_normal": "bold reverse", "mode_insert": "bold reverse",
                    "mode_command": "bold reverse", "status_bar": "reverse",
                    "status_pos": "reverse"}
_COLOR_SYSTEMS = {"standard": ColorSystem.STANDARD, "256": ColorSystem.EIGHT_BIT,
                  "truecolor": ColorSystem.TRUECOLOR, "windows": ColorSystem.WINDOWS}
_consoles: Dict[Tuple[Optional[str], int, bool], Console] = {}
_ansi_cache: Dict[tuple, str] = {}
_system_styles: Dict[tuple, Style] = {}


def _no_color() -> bool:
    return os.environ.get("NO_COLOR", "") != ""


def _style(name: str, no_color: bool) -> str:
    return (no_color and _NO_COLOR_STYLES.get(name)) or PALETTE[name]


def _console(color_system: Optional[str], width: int, no_color: bool) -> Console:
    key = (color_system, width, no_color)
    console = _consoles.get(key)
    if console is None:
        if len(_consoles) > 16:  # many resizes: start over
            _consoles.clear()
        console = Console(file=io.StringIO(), force_terminal=True, color_system=color_system,
                          width=width, no_color=no_color, force_jupyter=False, highlight=False,
                          markup=False, emoji=False, legacy_windows=False, soft_wrap=True)
        _consoles[key] = console
    return console


def _style_for(seg_style: Style, system: ColorSystem, no_color: bool) -> Style:
    """The style with its colours already reduced to `system` (or removed for NO_COLOR).

    Needed because a Style remembers its ANSI codes from the first time it is rendered: without
    this, a style first drawn in truecolor would keep its truecolor codes in a 256-colour frame.
    """
    key = (seg_style, system, no_color)
    result = _system_styles.get(key)
    if result is None:
        if no_color:
            result = seg_style.without_color
        else:
            color, bgcolor = seg_style.color, seg_style.bgcolor
            result = seg_style + Style.from_color(color and color.downgrade(system),
                                                  bgcolor and bgcolor.downgrade(system))
        if len(_system_styles) >= 4096:
            _system_styles.clear()
        _system_styles[key] = result
    return result


def _ansi(row: Text, console: Console, system: Optional[ColorSystem], no_color: bool) -> str:
    """One row as text + ANSI codes, remembered: most rows are the same in the next frame.

    This is what Console.print does, without its wrapping and measuring (a row never wraps),
    which made a full 160x50 frame several times slower."""
    key = (row.plain, row.style, tuple(row.spans), system, no_color)
    cached = _ansi_cache.get(key)
    if cached is not None:
        return cached
    if row.style:  # Text.render() only applies spans: make the row's own style a span first
        row = Text("").join([row])
    parts = []
    for segment in row.render(console):
        text, seg_style = segment.text, segment.style
        if seg_style and system is not None:
            parts.append(_style_for(seg_style, system, no_color).render(text, color_system=system))
        else:
            parts.append(text)
    if len(_ansi_cache) >= 4096:
        _ansi_cache.clear()
    _ansi_cache[key] = ansi = "".join(parts)
    return ansi


def _clip(text: str, width: int) -> str:
    return text[:max(0, width)]


def _status_line(label: str, middle: str, right: str, width: int, no_color: bool = False) -> Text:
    """The mode block, a bar with the exercise id in the middle, and row,col on the right."""
    left, right = f" {label} ", f" {right} "
    inner = width - len(left) - len(right)
    mid = middle.center(inner) if inner >= len(middle) + 2 else _clip(middle, inner).ljust(max(inner, 0))
    line = Text.assemble((left, _style(_MODE_STYLES.get(label, "mode_normal"), no_color)),
                         (mid, _style("status_bar", no_color)),
                         (right, _style("status_pos", no_color)))
    line.truncate(width)
    return line


def _message(ed: Editor, width: int) -> Text:
    line = Text(style.ICONS.get(ed.message_kind, "") + ed.message,
                style=_MESSAGE_STYLES.get(ed.message_kind, ""))
    line.truncate(max(0, width))
    return line


def render(editor: Editor, width: int, height: int, *,
           color_system: Optional[str] = "truecolor") -> str:
    """Return one full frame for a width x height screen (rows are positioned absolutely).

    color_system: "truecolor", "256", "standard" (16 colours) or None (no colours at all).
    """
    width, height = _screen(width, height)
    no_color = _no_color()
    if editor.mode == PAGER:
        rows, cursor = _pager_frame(editor, width, height)
    else:
        rows, cursor = _editor_frame(editor, width, height, no_color)
    console = _console(color_system, width, no_color)
    system = _COLOR_SYSTEMS.get(console.color_system or "")  # None: no colour codes at all
    out = ["\x1b[?25l"]  # hide the cursor while drawing
    for n, row in enumerate(rows, 1):
        out.append(f"\x1b[{n};1H{_ansi(row, console, system, no_color)}")
        if row.cell_len < width:
            out.append("\x1b[K")  # clear the rest of the row
    out.append(f"\x1b[{cursor[0]};{cursor[1]}H\x1b[?25h")
    return "".join(out)


def _editor_frame(ed: Editor, width: int, height: int, no_color: bool = False):
    b = ed.buffer
    rows: List[Text] = _header_texts(ed.session.exercise, width, height)
    header = len(rows)
    body = height - header - 2
    top = _visible_top(ed.top, b.row, body)
    numw = max(3, len(str(len(b.lines))))
    gutter = numw + 1
    code = style.highlight_lines(b.text)
    for idx in range(top, top + body):
        if idx < len(b.lines):
            number_style = PALETTE["gutter_current"] if idx == b.row else PALETTE["gutter"]
            row = Text.assemble((f"{idx + 1:>{numw}}", number_style), " ")
            line = code[idx]
            line.truncate(max(0, width - gutter))
            row.append_text(line)
            rows.append(row)
        else:
            rows.append(Text("~", style=PALETTE["filler"]))
    if ed.intro_visible:
        _overlay_intro(rows, header, width, getattr(ed.session, "notice", ""))

    label = {INSERT: "INSERT", COMMAND: "COMMAND"}.get(ed.mode, NORMAL)
    status_row = _status_line(label, ed.session.exercise.id, f"{b.row + 1},{b.col + 1}", width,
                              no_color)

    cursor = (header + (b.row - top) + 1, min(width, gutter + b.col + 1))
    last = width - 1  # never write the bottom-right cell (some terminals scroll)
    if ed.mode == COMMAND:
        line = ":" + ed.cmdline
        line = line[-last:] if len(line) > last else line
        bottom = Text(line)
        cursor = (height, len(line) + 1)
    elif ed.mode == PROMPT:
        if ed.message:  # keep the message visible above the question, like Neovim
            status_row = _message(ed, width)
        line = _clip(REVEAL_PROMPT, last)
        bottom = Text(line, style=PALETTE["prompt"])
        cursor = (height, len(line) + 1)
    elif ed.mode == INSERT and not ed.message:
        bottom = Text("-- INSERT --", style=PALETTE["insert_label"])
    else:
        bottom = _message(ed, last)
    rows.append(status_row)
    rows.append(bottom)
    return rows, cursor


def _intro_lines(width: int) -> List[Text]:
    """The start screen: what the two modes do and every command (from session.COMMANDS)."""
    muted, cmd = PALETTE["muted"], PALETTE["command"]
    lines = [Text.assemble(("NORMAL  ", "bold blue"), ("arrows / h j k l move · i, a, o start typing", muted)),
             Text.assemble(("INSERT  ", "bold green"), ("type your code · Esc goes back to NORMAL", muted)),
             Text("")]
    names = [name for name, _ in COMMANDS]
    namew = max(len(n) for n in names) + 2

    def entry(name: str, descw: int = 0) -> Text:
        return Text.assemble((name.ljust(namew), cmd), (SHORT.get(name, "").ljust(descw), muted))

    half = (len(names) + 1) // 2
    left, right = names[:half], names[half:]
    leftw = max(len(SHORT.get(n, "")) for n in left) + 4  # description width + gap
    two_col = namew + leftw + namew + max(len(SHORT.get(n, "")) for n in right)
    if width >= two_col + 4:  # two columns, like Neovim's start screen
        for i, name in enumerate(left):
            row = entry(name, leftw)
            if i < len(right):
                row.append_text(entry(right[i]))
            row.rstrip()
            lines.append(row)
    else:
        lines.extend(entry(name) for name in names)
    return lines


def _notice_lines(notice: str, width: int) -> List[Text]:
    """Session.notice wrapped for the start screen, then one empty line."""
    if not notice:
        return []
    wrapped = textwrap.wrap(style.NOTICE_ICON + notice, width=max(20, min(width - 6, 76)))
    return [Text(row, style=PALETTE["notice"]) for row in wrapped] + [Text("")]


def _overlay_intro(rows: List[Text], header: int, width: int, notice: str = "") -> None:
    """Draw the start screen over the "~" rows below the code (only the rows that fit).
    Session.notice, if any, comes first so it is never the part that gets cut."""
    filler = [i for i in range(header, len(rows)) if rows[i].plain == "~"]
    if len(filler) < 3:
        return
    lines = _notice_lines(notice, width) + _intro_lines(width)
    room = len(filler) - 1  # keep one plain "~" row between the code and the text
    if len(lines) > room:
        lines = lines[:room - 1] + [Text(":intro for the full list", style=PALETTE["muted"])]
    block = max(line.cell_len for line in lines)
    indent = max(2, (width - block) // 2)
    for i, line in zip(filler[1:], lines):
        row = Text("~", style=PALETTE["filler"])
        row.append(" " * (indent - 1))
        row.append_text(line)
        row.truncate(width)
        rows[i] = row


def _pager_texts(text: str, width: int, highlight: bool) -> List[Text]:
    """Like _pager_lines(), as Text rows (with Python syntax colours when `highlight`)."""
    text = text.expandtabs(4)
    lines = style.highlight_lines(text) if highlight else [Text(ln) for ln in text.split("\n")]
    out: List[Text] = []
    for line in lines:
        n = len(line.plain)
        out.extend(line.divide(range(width, n, width)) if n > width else [line])
    return out


def _pager_frame(ed: Editor, width: int, height: int):
    lines = _pager_texts(ed.pager_text, width, highlight=ed.pager_kind == "solution")
    page = height - 2
    top = max(0, min(ed.pager_top, len(lines) - page))
    title = _clip(f" {ed.pager_title} ".ljust(width), width)
    color = _PAGER_COLORS.get(ed.pager_kind, PALETTE["pager"])
    rows: List[Text] = [Text(title, style=f"bold reverse {color}")]
    for idx in range(top, top + page):
        rows.append(lines[idx] if idx < len(lines) else Text(""))
    if ed.message:
        footer = ed.message
    elif len(lines) > page:
        end = min(top + page, len(lines))
        footer = f"-- lines {top + 1}-{end} of {len(lines)} -- ↑↓ Space: scroll · q, Esc or Enter: back"
    else:
        footer = "-- q, Esc or Enter: back --"
    footer = _clip(footer, width - 1)
    rows.append(Text(footer, style=PALETTE["muted"]))
    return rows, (height, len(footer) + 1)


# ----- parse_keys ----------------------------------------------------------------------------

_CSI_FINAL = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT", "H": "HOME", "F": "END"}
_CSI_TILDE = {"1": "HOME", "7": "HOME", "4": "END", "8": "END", "3": "DELETE",
              "5": "PAGEUP", "6": "PAGEDOWN", "200": "PASTE_START", "201": "PASTE_END"}
_CONTROL = {"\r": "ENTER", "\n": "ENTER", "\t": "TAB", "\x7f": "BACKSPACE", "\x08": "BACKSPACE",
            "\x03": "CTRL_C"}


def parse_keys(data: bytes) -> List[str]:
    """Turn raw bytes from the terminal into key names.

    Printable characters stay themselves ("a", ":", "é"); special keys get names ("ESC", "ENTER",
    "UP", "DELETE", ...). Many keys can arrive at once (pasting); a trailing lone ESC is "ESC".
    Unknown escape sequences are dropped.
    """
    text = data.decode("utf-8", "replace")
    keys: List[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":
            nxt = text[i + 1] if i + 1 < n else ""
            if nxt == "[":
                j = i + 2
                while j < n and "\x20" <= text[j] <= "\x3f":  # parameter / intermediate bytes
                    j += 1
                if j < n and "\x40" <= text[j] <= "\x7e":  # final byte: a complete sequence
                    params, final = text[i + 2:j], text[j]
                    name = (_CSI_TILDE.get(params.split(";")[0]) if final == "~"
                            else _CSI_FINAL.get(final))
                    if name:
                        keys.append(name)
                    i = j + 1
                    continue
            elif nxt == "O" and i + 2 < n and text[i + 2] in _CSI_FINAL:
                keys.append(_CSI_FINAL[text[i + 2]])
                i += 3
                continue
            keys.append("ESC")
            i += 1
        elif ch in _CONTROL:
            keys.append(_CONTROL[ch])
            i += 2 if text[i:i + 2] == "\r\n" else 1
        elif ch < " ":
            keys.append("CTRL_" + chr(ord(ch) + 64))  # e.g. Ctrl-D -> "CTRL_D" (ignored)
            i += 1
        else:
            keys.append(ch)
            i += 1
    return keys


_INCOMPLETE_CSI = re.compile(rb"\x1b(\[[\x20-\x3f]*|O)?\Z")


def _needs_more(data: bytes) -> bool:
    """True if data ends in the middle of an escape sequence or a UTF-8 character."""
    if _INCOMPLETE_CSI.search(data):
        return True
    for back in range(1, min(4, len(data)) + 1):
        byte = data[-back]
        if byte & 0xC0 == 0x80:  # continuation byte: keep looking for the lead byte
            continue
        if byte >= 0xF0:
            return back < 4
        if byte >= 0xE0:
            return back < 3
        if byte >= 0xC0:
            return back < 2
        return False
    return False


# ----- the terminal ----------------------------------------------------------------------------

def is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def editor_available() -> bool:
    """True when the full-screen editor can run here: a real terminal (Linux/macOS), or a Windows
    console that understands ANSI escape codes (Windows 10 and newer)."""
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except (AttributeError, ValueError):
        return False
    if is_windows():
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            return False
        old = _win_vt_mode(True)  # can virtual terminal processing be switched on?
        if old is None:
            return False
        _win_vt_mode(False, old)
        return True
    try:
        import termios  # noqa: F401
        import tty  # noqa: F401
    except ImportError:
        return False
    return os.environ.get("TERM", "") != "dumb"


# ----- Windows console input ---------------------------------------------------------------------
#
# msvcrt.getwch() returns one key per call. Special keys come as two calls: "\x00" or "\xe0", then a
# code letter (Python docs, msvcrt). Esc is a plain "\x1b" (no escape sequences, so no 50 ms wait).
# Ctrl-C can't be read with getwch: Python raises KeyboardInterrupt instead, which run_editor turns
# into "CTRL_C". Windows gives no "paste started" signal, so a burst of keys that are already waiting
# (more than a person can type between two reads) is treated as a paste.

_WIN_SPECIAL = {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT", "G": "HOME", "O": "END",
                "S": "DELETE", "I": "PAGEUP", "Q": "PAGEDOWN"}
_WIN_CONTROL = {"\r": "ENTER", "\n": "ENTER", "\t": "TAB", "\x08": "BACKSPACE", "\x7f": "BACKSPACE",
                "\x1b": "ESC", "\x03": "CTRL_C"}
PASTE_BURST = 4  # this many keys waiting at once (with text and an Enter) = a paste


def translate_windows_keys(chars: List[str]) -> List[str]:
    """Turn characters from msvcrt.getwch() into the editor's key names."""
    keys: List[str] = []
    it = iter(chars)
    for ch in it:
        if ch in ("\x00", "\xe0"):
            code = next(it, "")
            name = _WIN_SPECIAL.get(code)
            if name:
                keys.append(name)  # other function keys (F1...) are ignored
        elif ch in _WIN_CONTROL:
            keys.append(_WIN_CONTROL[ch])
        elif ch < " ":
            keys.append("CTRL_" + chr(ord(ch) + 64))
        else:
            keys.append(ch)
    printable = sum(1 for k in keys if len(k) == 1)
    if len(keys) >= PASTE_BURST and printable and "ENTER" in keys:
        return ["PASTE_START", *keys, "PASTE_END"]
    return keys


def read_windows_keys(msvcrt) -> List[str]:
    """Block for one key, then also take every key already waiting (a paste arrives all at once)."""
    chars = [msvcrt.getwch()]
    while msvcrt.kbhit():
        chars.append(msvcrt.getwch())
    if chars[-1] in ("\x00", "\xe0"):  # the code letter of a special key is always right behind
        chars.append(msvcrt.getwch())
    return translate_windows_keys(chars)


def _win_vt_mode(enable: bool, restore: Optional[int] = None) -> Optional[int]:
    """Switch ANSI escape-code handling on for the Windows console; return the old mode (None = no).

    With restore given, put that mode back instead.
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return None
        if restore is not None:
            kernel32.SetConsoleMode(handle, restore)
            return restore
        # ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x4) | DISABLE_NEWLINE_AUTO_RETURN (0x8)
        if enable and not kernel32.SetConsoleMode(handle, mode.value | 0x0004 | 0x0008):
            return None
        return mode.value
    except Exception:
        return None


def _write(text: str) -> None:
    out = sys.stdout
    out.flush()
    buf = getattr(out, "buffer", None)
    if buf is None:
        out.write(text)
        out.flush()
        return
    buf.write(text.encode(out.encoding or "utf-8", "replace"))
    buf.flush()


def _draw(editor: Editor, color_system: Optional[str] = "256") -> None:
    size = shutil.get_terminal_size()
    editor.resize(size.columns, size.lines)
    _write(render(editor, size.columns, size.lines, color_system=color_system))


def run_editor(session) -> None:
    """Run the editor in the current terminal until a Response says quit."""
    size = shutil.get_terminal_size()
    editor = Editor(session, size.columns, size.lines)
    if is_windows():
        _run_windows(editor)
    else:
        _run_posix(editor)
    if editor.farewell:
        print(editor.farewell)


def _run_windows(editor: Editor) -> None:
    import msvcrt
    import time

    old_mode = _win_vt_mode(True)
    if old_mode is None:
        raise RuntimeError("This Windows console does not support the editor (needs Windows 10+).")
    color_system = Console().color_system or "256"
    last = shutil.get_terminal_size()
    try:
        _write("\x1b[?1049h\x1b[2J")
        _draw(editor, color_system)
        while not editor.done:
            try:
                if not msvcrt.kbhit():
                    time.sleep(0.02)
                    size = shutil.get_terminal_size()
                    if size != last:  # no resize signal on Windows: compare sizes instead
                        last = size
                        _write("\x1b[2J")
                        _draw(editor, color_system)
                    continue
                keys = read_windows_keys(msvcrt)
            except KeyboardInterrupt:
                keys = ["CTRL_C"]
            for key in keys:
                editor.feed(key)
                if editor.done:
                    break
            _draw(editor, color_system)
    finally:
        _write("\x1b[0m\x1b[?25h\x1b[?1049l")
        _win_vt_mode(False, old_mode)


def _run_posix(editor: Editor) -> None:
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    color_system = Console().color_system or "256"  # the terminal's colour depth, read once
    resized: List[int] = []
    old_winch = None
    winch_set = False
    try:
        tty.setraw(fd)
        if hasattr(signal, "SIGWINCH"):
            try:
                old_winch = signal.signal(signal.SIGWINCH, lambda *_: resized.append(1))
                winch_set = True
            except ValueError:  # not the main thread: the size is re-read on every redraw anyway
                pass
        # alternate screen, bracketed paste on, clear
        _write("\x1b[?1049h\x1b[?2004h\x1b[2J")
        _draw(editor, color_system)
        while not editor.done:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                if resized:
                    del resized[:]
                    _write("\x1b[2J")
                    _draw(editor, color_system)
                continue
            data = os.read(fd, 4096)
            if not data:
                break  # the terminal went away
            while _needs_more(data):  # e.g. a lone ESC: wait briefly for the rest of a sequence
                ready, _, _ = select.select([fd], [], [], ESC_TIMEOUT)
                more = os.read(fd, 4096) if ready else b""
                if not more:
                    break
                data += more
            for key in parse_keys(data):
                editor.feed(key)
                if editor.done:
                    break
            if resized:
                del resized[:]
                _write("\x1b[2J")
            _draw(editor, color_system)
    finally:
        if winch_set:
            signal.signal(signal.SIGWINCH, old_winch if old_winch is not None else signal.SIG_DFL)
        # bracketed paste off, normal colours, cursor on, leave the alternate screen
        _write("\x1b[?2004l\x1b[0m\x1b[?25h\x1b[?1049l")
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
