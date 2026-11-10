# Front-end guide (terminal editor, Jupyter, tkinter popup and any future UI)

Last updated: 2026-11-11. Applies to the code on `main` after the "terminal editor + Jupyter
magic" change, the Rich look (colours, panels, syntax highlighting), and AI mode with the
`pygym` main menu (see [The `pygym` command, modes and AI exercises](#the-pygym-command-modes-and-ai-exercises)).

## TL;DR

- All rules now live in **`Session.handle(command, code) -> Response`** (`src/pygym/session.py`).
- A front-end does three things only: collect the code, send a command, show the `Response`.
- The text CLI (`run_text`), the terminal editor (`editor.py`) and the Jupyter magic
  (`jupyter.py`) already work this way. `run_tk` has **not** been changed yet and still calls
  `session.submit()` directly. It keeps working, but does not follow the new rules.
- A tested starting point for the new popup is in **`experiments/tk_handle_sketch.py`**.
- **Rich** (`rich>=13.0`) is pygym's one required dependency. The line CLI, the Jupyter magic and
  the terminal editor all draw with it; the shared colours and panels are in
  **`src/pygym/style.py`** (see [Look and colours](#look-and-colours)).
- **New (2026-11-11):** every front-end must show **`Session.notice`** when it is not empty, and
  mark generated exercises (`exercise.source == "ai"`) with an **AI** badge. See below.

## The `pygym` command, modes and AI exercises

**The command.** `pip install -e .` creates a `pygym` console command (`python -m pygym` works
without reinstalling). The code is in `src/pygym/menu.py`; `src/pygym/__main__.py` only calls it.

```
pygym                                   the Rich main menu: library -> function -> level
pygym zip --level 2 --mode ai --ui text one exercise straight away, then exit
pygym statistics.median -l beginner     a function from another library
pygym --list [LIBRARY] | --status | --set-mode ai | --set-model qwen2.5:3b | --version
```

In the menu every prompt takes `m` (switch preset/AI, saved), `l` level, `u` window
(`auto editor text tk`), `?` help, `q` quit. At the level prompt `b` means *beginner*
(use `back` or `0` to go back). After an exercise ends you are back in the menu. The menu only
chooses *which* exercise; it builds a `Session(target, level, mode)` and calls `ui.run()`.

**Modes** (`Session(name, difficulty, mode)`; `mode=None` = the saved setting in
`PYGYM_HOME/config.json`):

| Mode | What `Session` does |
|---|---|
| `"preset"` | a hand-written exercise. A function with **no** preset asks the AI instead. |
| `"ai"` | always asks the AI (`pygym.ai.generate_exercise`). If that fails, uses a preset. |

The front-end does not need to know the mode. It needs two things:

1. **`Session.notice`** (a string, often `""`). It is the AI warning
   (`"AI-generated exercise: it may be incorrect. Use it carefully."`) or a fallback note
   (`"AI mode unavailable (Ollama is not running at ...); using the preset exercises."`).
   Show it **when the exercise appears and again after every `:s`** (the new exercise may be
   generated, or the AI may have failed this time). It is updated before `handle(":s")`
   returns.
2. **`exercise.source`**: `"ai"` for a generated exercise. Show an **AI** badge next to the level
   (`style.is_generated(ex)`, `style.AI_BADGE`).

How each front-end does it:

| Front-end | Notice | AI badge |
|---|---|---|
| line CLI | yellow line `! ...` under the exercise panel, at start and after `:s` (`style.exercise_view(session)`) | in the panel's title line |
| Jupyter | same as the CLI, at `exercise()` and after `%pygym :s` | same |
| editor | on the message line at start and after `:s` (yellow, `! `), and at the top of the start screen (wrapped, never cut) | in the question line: `zip  ●○○  level 1   AI  ─ ...` |
| tkinter | `ui.run(..., "tk")` prints it in the terminal before the window opens (`run_tk` is unchanged) | not yet (teammate's code) |
| menu | does **not** print it (the front-end does; this avoids showing it twice) | — |

**Dotted targets.** Generated exercises can be about `str.join` or `statistics.median`.
`exercise.target_function` is then the full name. `:help` shows that function's documentation
(with its signature); titles say e.g. `Documentation: statistics.median`.

## Which UI runs

`exercise(name, difficulty=None, ui="auto")` calls `ui.run(session, ui)`:

| `ui=` | Front-end | Where it lives |
|---|---|---|
| `"jupyter"` | `%%pygym` cell magic | `src/pygym/jupyter.py` |
| `"editor"` | full-screen terminal editor | `src/pygym/editor.py` |
| `"tk"` | tkinter popup | `run_tk` in `src/pygym/ui.py` |
| `"text"` | line-by-line CLI | `run_text` in `src/pygym/ui.py` |

`"auto"` (the default) tries them in this order and takes the first that works:

1. Inside a Jupyter notebook (`jupyter.in_notebook()`) → `"jupyter"`.
2. In a real Linux/macOS terminal (`editor.editor_available()`: stdin and stdout are TTYs,
   `termios` exists, `TERM` is not `dumb`) → `"editor"`. **Not on Windows** (team decision): there
   the tkinter popup stays the default and the editor runs only with `ui="editor"`.
3. A tkinter window can open (`tk_available()`) → `"tk"`. This is the Windows default.
4. Otherwise → `"text"`. Windows gets this when tkinter is missing.

If you force `ui="editor"` where it can't run, pygym says so and uses the line CLI instead.
Any other `ui` value also gives the line CLI.

## What changed and why

| Before | Now | Why |
|---|---|---|
| Each UI decided its own wording and flow (`"PASSED: "` vs `"Passed! "`, CLI quit on pass, popup didn't) | One `Session.handle()` decides; UIs only display | Both UIs behave the same; a new UI is just widgets |
| CLI: type code, then `END`; commands only as the first line | vim-style commands, recognised on **any** line | Familiar to vim users; you can ask for a hint mid-function |
| CLI crashed with `EOFError` on Ctrl-D | Clean "Bye!" | Bug fix |
| No way to read the built-in's docs | `:help` shows e.g. `zip.__doc__` | Learn without leaving the exercise |

Files touched: `src/pygym/models.py` (new `Response`), `src/pygym/session.py` (`COMMANDS`,
`handle()`, `saved_code`, `is_dirty()`, `help()`), `src/pygym/ui.py` (`run_text` only).
New tests: `tests/test_session.py`, `tests/test_cli.py`.

## The commands (the rules every UI must follow)

| Command | Suggested button | What Session does | Response |
|---|---|---|---|
| `:w` | Check | grades the code, remembers it as "saved" | `kind="pass"` or `"fail"`; window stays open |
| `:wq` | Check & close | grades; pass → close; fail → offer the solution | pass: `quit=True`. fail: `ask_reveal=True`, **does not quit** |
| `:q` | Close / window X | quits only if the code has not changed since the last `:w` **and** the last check did not fail (nothing tried yet = may quit) | `quit=True`; or `kind="refused"` (changed code); or `kind="refused"` + `ask_reveal=True` (last check failed) |
| `:q!` | Close without checking | always quits | `quit=True` |
| `:h` | Hint | — | `kind="hint"` |
| `:help` | Docs | the target function's docstring (`zip`, `str.join`, `statistics.median`) | `kind="help"` (long text: show in a scrollable area) |
| `:s` | Skip | loads another exercise, forgets the saved code; may update `Session.notice` | `kind="exercise"`, `clear_code=True`; or `kind="refused"` (no `clear_code`) when no other exercise exists right now (a function without presets whose generation failed) |
| `:solution` | Solution | — | `kind="solution"` |
| `:intro` | Commands / ? | the list of all commands (`session.commands_text()`) | `kind="commands"` |

"Changed since the last `:w`" ignores trailing spaces and blank lines.
The list with descriptions is `pygym.session.COMMANDS`; use it for tooltips/labels so the
wording matches the CLI.

## The `Response` contract (`src/pygym/models.py`)

```python
@dataclass
class Response:
    text: str                 # what to show the student
    kind: str                 # pass | fail | hint | help | solution | commands | exercise | refused | unknown | quit
    quit: bool = False        # close the window now
    ask_reveal: bool = False  # ask "Reveal the solution?" (yes -> show session.solution())
    clear_code: bool = False  # new exercise: redraw the question and reset the code box
    result: GradeResult | None = None   # set for :w / :wq
```

The UI decides *how* things look (colours, popups, icons). It must not decide *whether*
something is allowed: always ask `handle()`.

## Terminal editor (Linux/macOS)

`src/pygym/editor.py`. A small modal editor inspired by Neovim. The terminal handling is
standard library (`termios`, `tty`, `select`, `signal`, `shutil`); the colours and the Python
syntax highlighting come from Rich.

### Modes

- **Start screen**: like Neovim's, the empty `~` area below the code shows what NORMAL and INSERT
  do and every command (short descriptions from `session.SHORT`). It disappears the first time the
  code changes; `:intro` shows the list again. On a small terminal only the rows that fit are shown,
  ending with ":intro for the full list".
- **NORMAL** (the editor starts here): keys are not text. `:` opens the command line.
- **INSERT** (press `i`): every printable key is text, also `:`. `Esc` goes back to NORMAL.
- **Command line** (press `:` in NORMAL): type a command, then `Enter`. After any command the
  editor is in NORMAL mode again.
- **Pager**: long texts (`:help`, `:solution`, long messages) open full screen.
- **Prompt**: after a failed `:wq` (or a refused `:q` after a failed check) the bottom line asks
  `Reveal the solution? [y/N]`.

### Keys

| Mode | Key | What it does |
|---|---|---|
| NORMAL | arrows, `h` `j` `k` `l` | move the cursor |
| NORMAL | `i` / `a` | INSERT before / after the cursor |
| NORMAL | `o` | new line below (same indentation), then INSERT |
| NORMAL | `x` | delete the character under the cursor |
| NORMAL | `dd` | delete the line (one empty line is always left) |
| NORMAL | `:` | open the command line |
| INSERT | letters, digits, `:` ... | typed as text |
| INSERT | `Enter` | new line, keeps the indentation; +4 spaces after a line ending in `:` |
| INSERT | `Backspace` | delete the character before the cursor; at the start of a line, join it with the line above |
| INSERT | `Tab` | 4 spaces (never a tab character) |
| INSERT | `Esc` | back to NORMAL |
| both | arrows, `Home`, `End`, `Delete` | move / delete |
| command line | `Enter` / `Esc` / `Backspace` | run / cancel / edit (Backspace on an empty line cancels) |
| pager | arrows, `j` `k`, `Space`, `PageDown` | scroll; `q`, `Esc` or `Enter` closes |
| prompt | `y` | show the solution in the pager; any other key: no |
| any | `Ctrl-C` | does **not** quit; shows `Type :q to quit, :q! to quit without checking` |

Pasted text is inserted as it is (no extra auto-indent), also in NORMAL mode, if the terminal
supports "bracketed paste" (most modern terminals do).

### Screen layout

```
zip  ●○○  level 1 ─ Write a function 'combine' that takes a list of keys and a    <- question (up
list of values, pairs them up, and returns a dictionary. ...                         to 3 rows)
  1 def combine(keys, values):                                                    <- line numbers,
  2     return dict(zip(keys, values))                                               highlighted code
~                                                                                 <- empty space
 INSERT                          zip-01                                     2,35  <- status line
-- INSERT --                                                                      <- message / :cmd
```

Colours (Neovim-like): the built-in's name bold magenta, the difficulty dots yellow; the code is
highlighted with the `monokai` theme on the terminal's own background; line numbers grey, the
cursor line's number bold yellow; `~` blue. The status line has a mode block (` NORMAL ` blue,
` INSERT ` green, ` COMMAND ` yellow), a dark grey bar with the exercise id and a light grey
`row,col` block. Messages are coloured by `Response.kind` (see the palette below). The pager
title is blue for `:help`, green for the solution (which is highlighted too), cyan otherwise.

`run_editor()` reads the terminal's colour depth once (`rich.console.Console().color_system`:
truecolor, 256 or 16 colours) and Rich reduces every colour to it. With the `NO_COLOR`
environment variable set there are no colours; the status line then uses reverse video.

The whole screen is drawn again after every key. It uses the alternate screen (like `vim` and
`less`), so the terminal looks as before when the editor closes. The last message (for example
`Passed! ...` or `Bye!`) is printed after it closes.

### How it talks to Session

- At start the buffer is the signature plus an indented empty line, and the editor sets
  `session.saved_code` to that text. So `:q` before typing anything is allowed.
- `:w`, `:wq`, `:q` ... are sent as `session.handle(":" + typed, "\n".join(lines))`. Any
  other `:xyz` gets Session's "Unknown command" answer.
- The editor only shows the `Response` and obeys `quit`, `ask_reveal` and `clear_code`
  (`:s` loads the new question and resets the buffer and `saved_code`).
- One-line answers go to the bottom line (`Hint: ` before hints; `✓ `, `✗ ` or `! ` before
  pass, fail and refused). `help`, `solution` and anything longer than the screen width
  (counting that icon) open the pager.

### Testing it

The logic needs no terminal: `Editor(session).feed("i")`, `parse_keys(b"\x1b[A")`,
`render(editor, 80, 24)` (optionally `color_system="256"`, `"standard"` or `None`). See
`tests/test_editor.py`, which also checks the colour codes (mode block, current line number,
message colours, `NO_COLOR`). `tests/test_editor_pty.py` runs the real
editor in a pseudo-terminal, `tests/test_menu_pty.py` the `pygym` menu (builtins → map →
beginner → editor → `:q!` → menu → `q`). `tests/test_notice.py` checks the notice and the AI
badge in every front-end.

### Known limits

- No undo, no horizontal scrolling (long lines are cut at the screen edge).
- No visual mode, search, or counts like `3dd`. Unknown keys are ignored.
- Wide characters (e.g. Chinese) can make the cursor column look wrong.
- Windows: available with `exercise("zip", ui="editor")` (not the default, see "Which UI runs").

### Windows (`ui="editor"`)

Windows has no `termios`, so the editor has a second input layer there (`_run_windows` in
`editor.py`):

| | Linux / macOS | Windows |
|---|---|---|
| Reading keys | `termios` raw mode + `os.read` | `msvcrt.getwch()` one key per call |
| Arrow keys | `ESC [ A` ... (parsed by `parse_keys`) | `"\xe0"` or `"\x00"`, then `H` `P` `K` `M` (`translate_windows_keys`) |
| Esc | lone `\x1b`: wait 50 ms for more | plain `\x1b`, no waiting |
| Ctrl-C | the byte `\x03` | `KeyboardInterrupt` → `CTRL_C` |
| Pasting | bracketed paste (`ESC[200~`) | a burst of ≥ 4 waiting keys with an Enter counts as a paste |
| Colours, cursor, alternate screen | ANSI codes | the same ANSI codes, after switching on `ENABLE_VIRTUAL_TERMINAL_PROCESSING` (Windows 10+) |
| Resize | `SIGWINCH` | the size is compared every 20 ms |

Everything above the input layer (the `Editor` state machine, the start screen, `render()`) is the
same code. `editor_available()` is True on Windows only in a real console where virtual terminal
processing can be switched on; otherwise `ui="editor"` falls back to the line CLI with a note.

Tested only with a fake `msvcrt` on Linux (`tests/test_editor_windows.py`). **Please test on real
Windows** and report what breaks:

- [ ] `python -c "from pygym import exercise; exercise('zip', ui='editor')"` in cmd, PowerShell,
      Windows Terminal and the VS Code terminal
- [ ] start screen shows; colours look right; the status bar is one line
- [ ] arrows, Home/End, Delete, Backspace, Tab, Enter auto-indent
- [ ] `i` / Esc / `:w` / `:q` (refused) / `y` to reveal / `:help` pager / `:intro` / `:wq`
- [ ] Ctrl-C shows "Type :q to quit" and does not crash
- [ ] pasting a 3-line function in INSERT mode is not indented twice
- [ ] resizing the window redraws; after quitting, the console looks normal again
- [ ] `exercise('zip')` without `ui=` still opens the tkinter popup

## Jupyter (`%%pygym` cell magic)

`src/pygym/jupyter.py`. In a notebook, `exercise("zip")` prints the question and registers a
cell magic `%%pygym` and a line magic `%pygym`. The student writes code in a cell whose first
line is the command:

```python
# cell 1
from pygym import exercise
exercise("zip")
```

```python
# cell 2: check the code (the cell body is the code)
%%pygym :w
def combine(keys, values):
    return dict(zip(keys, values))
```

```python
# cell 3: commands without code
%pygym :h
```

```python
# cell 4: quit (allowed because the last check passed)
%pygym :q
```

How it works:

- The command is the text after `%%pygym` / `%pygym`. An empty command means `:w`.
- The code is the cell body. If the body is empty (or only spaces), the magic passes
  `code=None` and Session uses the code from the last check. That is why `%pygym :q` after a
  passing `:w` quits, and `%pygym :w` re-checks the last code.
- Everything is printed with Rich, using the same panels as the line CLI (`src/pygym/style.py`):
  in a notebook Rich shows them as HTML, in a plain IPython shell as text. `exercise()` prints
  an intro panel with the `%%pygym` instructions and an example cell, then the exercise panel.
- Hints and docs are panels, `Solution:` is followed by the highlighted code, one-line answers
  get an icon (`✓ Passed! ...`, `✗ Failed: ...`, `! ...`). If `ask_reveal` is set it asks
  `Reveal the solution? [y/N]` with `input()`. After `:s` it prints the new question. On quit it
  prints `Session ended.`; later magics say `No active exercise. Run exercise("zip") first.`
- The cell code is only graded, not run in the notebook.
- Errors are printed, never raised into the notebook.

Tests: `tests/test_jupyter.py` uses a real (non-notebook) IPython shell.

## Look and colours

Rich is a required dependency (`dependencies = ["rich>=13.0"]` in `pyproject.toml`); there is no
plain-text fallback. All colours are in `PALETTE` in **`src/pygym/style.py`**, by meaning:

| Key | Rich style | Used for | Tk suggestion |
|---|---|---|---|
| `brand` / `command` | cyan / bold cyan | intro border, command names | `#00a6b2` |
| `exercise` / `target` | magenta / bold magenta | exercise border, the built-in's name | `#b0379b` |
| `dots` | yellow | difficulty dots `●●○` | `#c9a400` |
| `muted` | dim | explanations, level, ids, `Skipped`, `Bye!` | `gray50` |
| `pass` | bold green | `✓ Passed! ...` | `#1a8f2e` |
| `fail` | bold red | `✗ Failed: ...` | `#c62828` |
| `refused` | yellow | `! Unchecked changes ...` | `#b58900` |
| `unknown` | red | `Unknown command ...` | `#c62828` |
| `hint` / `hint_line` | yellow (panel border) / cyan (editor line) | hints | `#00a6b2` |
| `help` | blue | documentation panel / pager title | `#1e63c6` |
| `solution` | green | solution pager title | `#1a8f2e` |
| `ai_badge` | bold black on yellow | ` AI ` next to the level of a generated exercise | `#c9a400` background |
| `notice` | yellow | `! ` + `Session.notice` | `#b58900` |
| `gutter` / `gutter_current` | grey50 / bold yellow | editor line numbers | — |
| `mode_normal` / `mode_insert` / `mode_command` | bold black on blue / green / yellow | editor mode block | — |
| `status_bar` / `status_pos` | white on grey23 / black on grey58 | editor status line | — |

Code is highlighted with Pygments' `monokai` theme (`style.CODE_THEME`) on the terminal's own
background. The Tk column is only a suggestion for a light window background.

The builders (each returns a Rich renderable; `style.to_plain(r)` gives its text):

| Builder | What it draws |
|---|---|
| `style.intro(how_to=None)` | cyan panel "pygym" / `v<version>`: one line, the how-to text (Jupyter passes its own), the `COMMANDS` table |
| `style.exercise_header(ex)` | magenta panel titled with the exercise id: `zip  ●○○  level 1` (+ ` AI ` badge for a generated one), the question, the signature |
| `style.exercise_view(session)` | `exercise_header` + the `Session.notice` line below it (if any): use this one |
| `style.notice(text)` | `! ` + text in the `notice` colour |
| `style.response(resp, session)` | a `Response` by `kind`: icon + coloured line, hint / documentation panel, or `Solution:` + code |
| `style.solution(code)`, `style.code(code)` | `Solution:` + highlighted code / highlighted code only |
| `style.line(text, kind)` | one line with the icon (`style.ICONS`) and colour of `kind` |

A tkinter front-end can't use Rich renderables, but it can mirror them: `PALETTE` and `ICONS`
for the status label (`fg=` by `kind`), `style.dots()` for the difficulty, and the same titles
("hint", "Documentation: zip", "Solution").

## How to wire tkinter (see `experiments/tk_handle_sketch.py`)

```python
def run(cmd):
    r = session.handle(cmd, box.get("1.0", "end-1c"))   # "end-1c": drop Tk's extra newline
    if r.kind in ("help", "solution"):
        popup(title, r.text); return                     # don't overwrite the student's code
    status.config(text=r.text, fg=COLORS.get(r.kind, "black"))
    if r.ask_reveal and messagebox.askyesno("pygym", "Reveal the solution?"):
        popup("Solution", session.solution())
    if r.clear_code:
        show(session.exercise)
    if r.quit:
        root.destroy()
```

Things that are easy to get wrong:

0. **Notice and badge.** Show `session.notice` (if not empty) in the status label when the
   exercise appears and after Skip, and add "AI" to the title when `ex.source == "ai"`.
1. **Pre-filled starter text.** The box starts with `def combine(keys, values):\n    `. Session
   doesn't know about it, so `:q` would be refused before the student typed anything. In
   `show()`, set `session.saved_code = starter_text` right after inserting it.
2. **Don't call `session.submit()` for checking any more.** It grades but does not update
   `saved_code`, so `:q` would keep refusing. Use `handle(":w", code)`.
3. **Window X button.** Route it through `:q` with `root.protocol("WM_DELETE_WINDOW", ...)`. If
   it's refused (changed code, or the last check failed), ask "Close anyway?" and close on yes
   (that's `:q!`), so a stuck student is never trapped in the window.
4. **Show solution / docs in a separate window**, not in the code box. Replacing the box text
   marks the code as changed, and `:q` is then refused.
5. **Keep the logic out of the UI.** If you need a new rule (e.g. "confirm before skip"), add it
   to `Session.handle()` and a test in `tests/test_session.py`, so the CLI gets it too.
6. **Build vs mainloop.** Splitting `build_tk()` (returns widgets) from `run_tk()` (calls
   `mainloop()`) lets you test the popup with `button.invoke()` and no human.

## What has been verified

- `pytest`: all tests pass (Python 3.14, Rich 15.0). Python 3.9 was not run.
- The sketch was driven by a script on a virtual display (`xvfb-run`): starter text counts as
  saved, a wrong `:w` shows red text, a wrong `:wq` asks to reveal, Hint/Docs/Solution open
  without touching the code box, Skip resets the box, a correct `:wq` closes the window, the
  window X closes at once when nothing changed and asks "Close anyway?" when the code changed.
- The terminal editor was run in a Linux pseudo-terminal (typing, paste, pager, resize, `:wq`,
  `:q!`). The `%%pygym` magic was run in an IPython shell.
- **Not** verified: real Windows/macOS desktops, macOS Terminal/iTerm, VS Code's terminal, a real
  Jupyter notebook in the browser, or how it looks. That's task P0-2.

## Open decisions (not settled by the team yet)

- **How a student fixes a wrong line in the CLI.** For now, after `:w` the CLI starts a fresh
  attempt (retype the function). This doesn't affect tkinter: the box is already an editor.
- Whether Skip should warn about unchecked code (now it just skips).
- Jupyter: notebooks now get the `%%pygym` magic. With `ui="tk"` in a notebook, tkinter still
  blocks the kernel while the window is open.

## How to check your work

```bash
pip install -e ".[dev]"
python -m pytest -q                                 # all tests, incl. test_session / test_cli
PYTHONPATH=src python experiments/tk_handle_sketch.py   # opens the sketch window
python -c "from pygym import exercise; exercise('zip', ui='tk')"   # the real popup
python -c "from pygym import exercise; exercise('zip', ui='text')" # the CLI, for comparison
python -c "from pygym import exercise; exercise('zip', ui='editor')" # the terminal editor
```
