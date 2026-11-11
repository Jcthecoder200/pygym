# Front-end guide (terminal editor, Jupyter, tkinter popup and any future UI)

Last updated: 2026-11-11 (progress bar while the AI writes, levels and `:level`). Applies to the
code on `main` after the "terminal editor + Jupyter
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
- **New: test results.** A check now tells the student *every* test: the call, the expected value,
  what their function returned (or raised) and what `print()` wrote. They are in
  **`GradeResult.tests`** (`Response.result.tests`) and **`GradeResult.setup_stdout`**; the new
  command **`:out`** shows the last check's results again. The editor has a results window
  (like Neovim's quickfix window), the CLI and Jupyter print a table. See
  [Test results](#test-results-graderesulttests-and-out); a tkinter how-to is included.
- **New: a progress bar while the AI writes the next exercise.** `session.start(command, code)`
  returns a `Job` at once (the exercise is written in a background thread); show `job.progress`
  until `job.done`, then use `job.result()` (the same `Response` as `handle()`). `handle()` still
  blocks, for simple front-ends. See [Waiting for the AI](#waiting-for-the-ai-sessionstart-and-job).
- **New: levels.** After 3 solved exercises at a level the next one is a level higher
  (`Session.level`, `Session.solved_at_level`, `session.LEVEL_UP_AFTER`); `:level` (short `:lv`)
  shows or changes it. See [Levels](#levels-level-and-the-automatic-level-up).

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
Targets can come from any installed library (`numpy.cumsum`, `requests.get`); pre-fill
`exercise.starter_code` (it already has `import numpy` above the `def` line; the student may
change it to `import numpy as np`).

**New `Exercise` fields** (both optional, default empty; old cache files still load):

| Field | Type | Meaning for a front-end |
|---|---|---|
| `files` | `Dict[str, str]` | small text files (relative path → text) the code finds in its working folder. The question text names them; a front-end may show them (e.g. under the question), but does not need to. |
| `http_fixtures` | `Dict[str, dict]` | answers of a local test server (`"/users/1": {"status": 200, "json": {...}}`). The code reaches it through the global name `BASE_URL`. Nothing to show. |

Grading is unchanged for a front-end: `session.handle(":w", code)`. The grader runs the code
in a new temporary folder (never the student's folder), see `docs/AI_MODE.md`, "The sandbox".

**Sandbox level.** `grader.sandbox_level()` returns `"bwrap"`, `"sandbox-exec"` or
`"audit-hook"`; `sandbox.label()` gives the text to show (`"bubblewrap"`,
`"macOS sandbox-exec"`, `"basic (Python audit hook only)"`). The menu header shows it in a
`Sandbox` row and `pygym --status` in a `sandbox` row. The first call runs a quick self-test
(about 30 ms, once per process). Front-ends don't need to show it.

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
| `:s` | Skip | loads another exercise (at `Session.level`), forgets the saved code; may update `Session.notice`. In AI mode this can take up to 20 s: use `start(":s")` to show a progress bar | `kind="exercise"`, `clear_code=True`; or `kind="refused"` (no `clear_code`) when no other exercise exists right now (a function without presets whose generation failed) |
| `:solution` | Solution | — (solving this exercise afterwards no longer counts towards the next level; so does `session.solution()`) | `kind="solution"` |
| `:level` / `:lv` | Level | alone: the level and the count (`Level: beginner ●○○ — 2 of 3 solved to level up. :level 1-3 to change`). `:level 2`, `:level intermediate`, `:lv i` (also `b`, `a`): sets the level, resets the count, loads a new exercise at once (progress bar in AI mode) | alone / same level: `kind="level"`; a new level: `kind="exercise"`, `clear_code=True`; not a level: `kind="refused"` (`'x' is not a level. Use :level 1, 2 or 3 (or b, i, a).`) |
| `:bigger yes` / `:bigger no` | (the answer to the [Y/n] question, not a button) | the answer to the bigger-task question (see [Bigger tasks](#bigger-tasks-the-ask_bigger-question)); remembered for the session. Not in `COMMANDS` / `:intro`: front-ends send it | `kind="exercise"`, `clear_code=True` when it loads one (yes after `:level 3`; no from beginner); else `kind="level"`; `kind="refused"` for another word, or for a function that is not too small |
| `:intro` | Commands / ? | the list of all commands (`session.commands_text()`) | `kind="commands"` |
| `:out` | Results | the results of the last check again (the editor: opens / closes its results window) | `kind="results"`, `result=` the last `GradeResult` (text `Last check: ...`, or a note that the code changed since); before any check `result=None` and the text `Nothing checked yet. :w to check your code.` |

"Changed since the last `:w`" ignores trailing spaces and blank lines.
The list with descriptions is `pygym.session.COMMANDS`; use it for tooltips/labels so the
wording matches the CLI.

**Which lines are commands** (line CLI, and anything else that mixes code and commands): only a
line that is a command *as a whole*, after trimming spaces. Use `session.is_command_line(line)`:
`:` + letters/`!` (`:wq`, `:xyz` → unknown command, still not code), or a command that takes an
argument (`session.ARG_COMMANDS`: `:level`, `:lv`, `:bigger`) followed by exactly one word
(`:level 2`, `:bigger yes`).
`:level 2 3`, `x = :level`, `:w 3` and a dict continuation line `    : 1}` are code. The editor
sends whatever was typed on its `:` line; the Jupyter magic sends the text after `%pygym`.

## The `Response` contract (`src/pygym/models.py`)

```python
@dataclass
class Response:
    text: str                 # what to show the student
    kind: str                 # pass | fail | hint | help | solution | commands | results | level | exercise | refused | unknown | quit
    quit: bool = False        # close the window now
    ask_reveal: bool = False  # ask "Reveal the solution?" (yes -> show session.solution())
    clear_code: bool = False  # new exercise: redraw the question and reset the code box
    ask_bigger: bool = False  # ask session.bigger_prompt() [Y/n]; send the answer as a command
    result: GradeResult | None = None   # set for :w / :wq, and for :out after a check
```

Order in which a front-end obeys the flags: show the text, `ask_reveal`, `clear_code`, `quit`,
then `ask_bigger` (its answer is the next command, whose own Response is obeyed the same way).

The UI decides *how* things look (colours, popups, icons). It must not decide *whether*
something is allowed: always ask `handle()`.

## Test results (`GradeResult.tests` and `:out`)

The owner's rule: show everything. Every test's input, expected value, the student's value and
their `print()` output are visible, on a passing check too (it shows what the code does).
Hard-coding answers is the student's own problem.

```python
@dataclass
class TestResult:                 # src/pygym/models.py
    index: int                    # the test's number as the student sees it: 1, 2, 3 ...
    args: list                    # the arguments; the call is function_name(*args)
    expected: Any                 # the expected return value
    got: Any = None               # what the function returned (when error == "")
    error: str = ""               # "ZeroDivisionError: division by zero", or why it did not run
    passed: bool = False
    stdout: str = ""              # what print() wrote during this test
    # call_text(function_name, limit=80) -> "combine(['a'], [1])" ("…" when cut)
    # ran (property): False for a test that never started (error == models.NOT_RUN)

@dataclass
class GradeResult:
    passed: bool                  # passed, reason, message: unchanged
    reason: str
    message: str = ""
    tests: List[TestResult] = []  # one row per test, in order
    setup_stdout: str = ""        # what print() wrote while the code was loaded (top-level code)
    # passed_count (property), summary() -> "3/5 passed" ("" without rows)
```

When are there rows?

| Check | `tests` | `message` |
|---|---|---|
| passed | every row, all `passed` | `Correct!` |
| wrong answers / exceptions | every row (the code keeps running after a test raised) | `Not quite right yet: 3 of 5 tests passed.` (+ ` (pygym blocked: ...)` when the sandbox stopped something) |
| target not used | every row (the answers may be right: the verdict is "use the target") | unchanged |
| timeout | the finished tests, the running one (`error == models.TIMED_OUT`, with what it printed so far), the rest `error == models.NOT_RUN` | `Your code ran too long (possible infinite loop) in test 3.` (`... while loading (top-level code).`, or no detail when unknown) |
| syntax error, crash while loading, missing function, broken exercise | `[]` (never expected values for an exercise without them) | the error |

`print()` output is cut after 4000 characters per test (`_runner.STDOUT_LIMIT`) and then ends with
`… (output cut)`. `expected` and `got` are the JSON form the grader compares (tuples are lists,
sets are sorted lists). Printed text can contain anything: show it through `style.visible()`
(control characters such as `\x1b` become visible escapes).

How each front-end shows it:

| Front-end | After `:w` / `:wq` | `:out` |
|---|---|---|
| line CLI | a table (`#`, call, expected, got, ✓/✗), then each test's `print()` output, then the verdict line | prints it again, with `Last check: ...` |
| Jupyter | the same table under the cell | `%pygym :out` |
| editor | the results window opens (also on a pass) and stays open while you edit | opens / closes the window |
| tkinter | not yet: see the how-to below | — |

Shared builders in `style.py`: `style.results(result, function_name, width=None)` (the Rich table
and the print blocks; `style.response()` already puts it above the verdict for `pass`, `fail` and
`results` responses), `style.result_lines(result, function_name, width, print_lines=3)` (one
`rich.text.Text` per screen row, used by the editor), `style.has_results(result)`,
`style.results_title(result)` (`"3/5 passed"` or `"no tests ran"`), `style.row_icon(t)`,
`style.outcome_parts(t)`, `style.visible(text)`, `style.printed_lines(text, n)`.

### tkinter how-to (`run_tk` was not changed)

After `r = session.handle(":w", code)`, `r.result` is the `GradeResult`. A read-only `Text`
widget under the code box is enough (a `ttk.Treeview` with the columns `#`, call, expected, got
works too):

```python
from pygym import style

out = scrolledtext.ScrolledText(root, height=8, font=("Courier", 10), state="disabled")
out.tag_config("pass", foreground="#1a8f2e")      # PALETTE "pass"
out.tag_config("fail", foreground="#c62828")      # PALETTE "fail"
out.tag_config("muted", foreground="gray50")      # PALETTE "printed" / "muted"

def show_results(result):
    out.config(state="normal")
    out.delete("1.0", "end")
    rows = style.result_lines(result, session.exercise.function_name, width=100, print_lines=20)
    for row in rows:                       # rows are rich Text objects; .plain is the text
        tag = {"✓": "pass", "✗": "fail"}.get(row.plain[:1], "muted")
        out.insert("end", row.plain + "\n", tag)
    out.config(state="disabled")
```

Call `show_results(r.result)` after **every** check (a passing one too), keep the widget visible
while the student edits, clear it after Skip, and add a "Results" button that calls
`handle(":out")` (its `result` is `None` before the first check: show `r.text` then).

## Waiting for the AI (`Session.start()` and `Job`)

In AI mode (and in preset mode for a function without presets) `:s` and `:level 2` make a local
model write the next exercise: up to 4 tries within a 20 s budget, then a fallback. `handle()`
blocks all that time. A front-end that wants to stay responsive and show progress uses:

```python
job = session.start(command, code)   # returns at once; same arguments as handle()
job.done          # True once finished, failed or cancelled (never blocks)
job.progress      # the latest event (dict, below)
job.elapsed       # seconds since the job started (for a bar that moves smoothly)
job.cancel()      # stop waiting: the current exercise stays (result() -> kind "refused", "Cancelled. ...")
job.wait(0.1)     # block up to 0.1 s; True when done
resp = job.result()   # call once job.done: applies the new exercise, returns the Response
```

With prefetch on (the default), `:s` is usually instant: see "Prefetch" below.

- Only a command that loads an **AI-written** exercise runs in the background. Every other
  command, and every preset exercise, is done at once (`job.done` is `True` straight away), so
  `if not job.done: show the bar` shows nothing for presets. `session.needs_wait(command)` tells
  the same thing before starting.
- **Thread safety.** The worker thread never changes the session: the current exercise, the
  saved code and the results stay as they are until **you** call `job.result()` (on your UI
  thread). Keep the student's code box untouched until then (the editor ignores keys). While a
  job is pending, `handle()` / `start()` answer `kind="refused"` (`session.BUSY`), and
  `session.busy` is `True`.
- Errors in the generator never escape: they become the preset fallback (with
  `Session.notice` = `AI mode unavailable (...); using the preset exercises.`), or `kind="refused"`
  when there is no exercise at all.
- `session.start_next(level=None)` is the same as `start(":s")` (or `start(":level N")`).
- The first exercise is generated by the constructor: `Session(name, level, mode,
  on_progress=callback)` reports to `callback`; the menu runs it in a `Job` too
  (`Job(lambda report: Session(..., on_progress=report))`).

### Prefetch: the next exercise is written while the student works

When the AI writes the exercises (AI mode, or preset mode for a function without presets),
`Session` starts writing the **next** one in a background thread as soon as an exercise is set:
in the constructor and after every `:s` / `:level`. It uses the level `:s` would use. A front-end
does not have to do anything to get it. What changes for you:

- **`start(":s")` is usually done at once** (`job.done` is `True`): the prefetched exercise is
  applied by `job.result()` like any other. Your existing `if not job.done: show the bar` code
  shows nothing then.
- **If it is still being written**, `start(":s")` returns a Job that **follows that same work**:
  `job.progress` and `job.elapsed` are the prefetch's (the bar starts part-filled and the time is
  the real time so far). No second model request is sent. `job.cancel()` only stops waiting: the
  prefetch keeps running and the next `:s` follows it again. `handle(":s")` blocks on it.
- `session.next_ready` is `True` when `:s` will be instant. `session.needs_wait(":s")` is `False`
  then (Jupyter uses this to skip the progress line).
- A level change (`:level 2`, or the level-up in a passing `:w`) throws away a prefetch written
  for the old level and starts a new one. Only one prefetch runs at a time, and a new one waits
  until a thrown-away request has finished (one model request at a time).
- The prefetched exercise is not in `session.seen` until it is shown. The worker never changes
  the session (same rule as every Job). `:w` checks are not blocked by it: they run in the
  sandbox, not the model, and nothing serialises the two.
- **Ending a session:** `session.close()` cancels the prefetch (and a pending job). A command
  that quits (`:q`, `:q!`, a passing `:wq`) calls it itself, and `ui.run()` calls it when the
  window returns (also on Ctrl-C or end of input). `jupyter.start()` closes the session it
  replaces. A front-end that does not go through `ui.run()` must call `session.close()` itself
  when the window closes. The threads are daemons, so an unfinished model request never keeps
  the program alive; its answer is thrown away.
- **Setting:** `config.Settings.prefetch` (bool, default `True`; `"prefetch": false` in
  `config.json` turns it off). `pygym --status` shows a `prefetch` row; there is no menu key.
  `Session(..., prefetch=False)` overrides the setting (`None` = the setting).
- `session.skip()` (used by the tkinter popup) takes the prefetched exercise too.

**The progress event** (from `pygym.ai.generate_exercise(..., on_progress=callback)`, the
engine's only new parameter; `Job.progress` before the first event is `session.STARTING`):

```python
{"attempt": 2, "max_attempts": 4, "elapsed": 7.1, "budget": 20.0,
 "stage": "ideas" | "asking" | "checking" | "rejected" | "done" | "fallback", "detail": "..."}
```

| stage | when | text (`style.progress_text(event, job.elapsed)`) |
|---|---|---|
| `ideas` | (`attempt` 0) only the first time a function is used: the model lists its real-world use cases, at most 8 s of the budget | `finding real-world uses · 1 s` |
| `asking` | try n is sent to the model | `writing exercise · try 2 of 4 · 7 s` |
| `checking` | its reply is validated (the sandbox runs it) | `checking exercise · try 2 of 4 · 9 s` |
| `rejected` | the reply was not usable; `detail` says why in a few words (`engine.short_reason`) | `try 1 rejected: crashed on its tests · 4 s` |
| `done` | the exercise is ready | `exercise ready` |
| `fallback` | no new exercise; `detail` says what happens instead | `using a hand-written exercise instead` / `using an earlier saved exercise` |

A model reports no progress of its own, so the bar is honest about what is known: it fills
against the time budget (`style.progress_fraction(event, elapsed)` = elapsed / budget, at most
0.99 until done) and the text names the try. Shared builders: `style.progress_line(event,
elapsed, width)` (one `Text` row: spinner, text, bar, `Ctrl-C cancels`), `style.spinner_frame()`,
`style.wait_with_bar(job, console)` (a transient Rich `Progress`; Ctrl-C cancels; returns `False`
when cancelled).

| Front-end | While the AI writes |
|---|---|
| editor | the message line: `⠙ writing exercise · try 2 of 4 · 7 s  ━━━━━━━━━──────────  Ctrl-C cancels`, redrawn 10 times a second; keys are ignored, `Ctrl-C` cancels and keeps the current exercise |
| line CLI | a transient Rich progress bar (spinner, text, bar); `Ctrl-C` cancels |
| menu (first exercise) | the same bar (it was a spinner); `Ctrl-C` cancels and goes back to the menu |
| Jupyter | in a notebook one output line updated in place (no ipywidgets needed), cleared at the end; in the IPython terminal a line `… writing exercise · try 1 of 4` each time the step changes. Interrupting the kernel cancels |
| tkinter | not yet (`run_tk` unchanged): see below |

### tkinter how-to: the progress bar

```python
from tkinter import ttk
from pygym import style

bar = ttk.Progressbar(root, length=300, maximum=100)
label = tk.Label(root, anchor="w")

def run(cmd):
    job = session.start(cmd, box.get("1.0", "end-1c"))
    if job.done:
        return show_response(job.result())       # presets and every other command
    box.config(state="disabled")                 # don't let the code change underneath
    bar.pack(); label.pack()
    root.bind("<Escape>", lambda e: job.cancel())   # or a Cancel button
    poll(job)

def poll(job):
    if not job.done:
        label.config(text=style.progress_text(job.progress, job.elapsed))
        bar["value"] = 100 * style.progress_fraction(job.progress, job.elapsed)
        root.after(100, poll, job)               # ~10 times a second, on the Tk thread
        return
    bar.pack_forget(); label.pack_forget()
    box.config(state="normal")
    show_response(job.result())                  # result() on the Tk thread, never in a thread
```

## Levels (`:level` and the automatic level-up)

- `Session.level` (1-3) is the level of the **next** exercise; `Session(name, difficulty)` starts
  there (`difficulty=None` starts at 1). `session.difficulty` is the old name and returns the same.
- `Session.solved_at_level` counts exercises solved at this level in this session: a passing
  `:w` / `:wq`, **once** per exercise, and **not** when the solution was shown before
  (`:solution`, or `session.solution()` after the reveal prompt). Skipping never resets it.
- After `session.LEVEL_UP_AFTER` (3) solved, the level goes up by one (at most 3) and the count
  starts again. The passing check says so: `Passed! Correct! Level up! Next exercise:
  intermediate ●●○ (:s)` (with `:wq`: the same without ` (:s)`). The next exercise (`:s`) is at the
  new level, in preset and AI mode (preset: the loader's usual fallback when there is no preset at
  that level). pygym **never** lowers the level by itself; only `:level` does.
- The level is kept for the session only (the menu's saved default does not change).
- Show it: `style.level_badge(session)` → `"●○○ 2/3"` (`"●●●"` at level 3),
  `style.level_line(session)` → `"Level: beginner ●○○ 1/3 solved to level up"`.

| Front-end | Where the level shows |
|---|---|
| editor | a block in the status line, before `row,col`: ` NORMAL    zip-01    ●○○ 2/3  2,5 ` (left out on a very narrow screen) |
| line CLI / Jupyter | a line under every passed check (`style.response()` adds it); `:level` / `%pygym :level` |
| exercise panels | unchanged: `zip  ●●○  level 2` is the exercise's own level |
| tkinter | suggestion: `style.level_badge(session)` in the title or status label, refreshed after each check |

### Bigger tasks (the `ask_bigger` question)

A function too small for an advanced exercise on its own (`bin`, `abs`, `str.upper`;
`session.simple_target(name)`, `Session.is_simple`) whose exercises the AI writes
(`Session.bigger_applies()`) is not moved to advanced without asking. Three ways in:

| When | Response | Level meanwhile |
|---|---|---|
| `:level 3` (`:lv a`, `start_next(3)`) | `kind="level"`, `ask_bigger=True`, text `` `bin` is too small for an advanced exercise on its own. `` | unchanged, nothing is generated |
| the level-up from 2 to 3 (a passing `:w`) | `kind="pass"`, `ask_bigger=True`, text `Passed! ... `` `bin` alone is too small for advanced. `` | stays 2 (count back to 0) |
| `Session(name, 3)` | `session.question()` returns `Response(kind="level", ask_bigger=True)` right after start (None otherwise) | starts at 2 |

Then the front-end asks `session.bigger_prompt()`:
`Practise it inside a bigger task (bin + loops, conditions and data structures)? [Y/n] `
(`session.BIGGER_PROMPT`; the editor shows `Practise bin inside a bigger task? [Y/n] ` when the
full one does not fit). It turns the answer into the next command with
`session.bigger_answer(text)`: Enter / `y` / `yes` → `":bigger yes"`, `n` / `no` →
`":bigger no"`, anything else and `None` (EOF, Ctrl-C, Esc) → `None`: send nothing, show
`session.NOT_ANSWERED`, the level stays as it is and `:level 3` asks again. The answer goes
through `handle()` / `start()` like any command, on its own (the per-line rule is unchanged), so
`needs_wait(":bigger yes")` says whether it writes an exercise (show the progress bar then).

- yes: after `:level 3` or at start, an advanced exercise is written at once
  (`ai.generate_exercise(..., bigger=True)`: the prompt makes the function one step of a bigger
  task); after a level-up only the level changes (`Level up! advanced ●●● (bin inside bigger
  tasks). :s for the next exercise.`).
- no: `Staying at intermediate ●●○: ...` (from beginner, an intermediate exercise is loaded).
- The answer is `Session.bigger_task` (None / True / False), kept for the session: `:s` and later
  level-ups never ask again; `:level 3` after "no" answers `Staying at intermediate ... (:bigger
  yes to change)`. `Session(..., bigger=True|False)` passes an answer the front-end got before
  the session existed (the menu asks before writing the first exercise).
- `Session.asking_bigger` is True while a question waits. The prefetch writes at `Session.level`,
  which stays below 3 until "yes", so nothing advanced is written before the answer.

| Front-end | How it asks |
|---|---|
| line CLI | `input_fn(session.bigger_prompt())` after the Response; at start after the exercise |
| editor | PROMPT mode with `prompt_kind="bigger"`: `y` / Enter = yes, `n` = no, other keys (Esc) = not answered, Ctrl-C = not answered |
| Jupyter | `input(session.bigger_prompt())`, also in `jupyter.start()` |
| menu / `pygym bin --level 3` | `menu.start()` asks before the session exists (`menu.asks_bigger(choice)`), then `Session(..., bigger=answer)`; not answered → back to the menu |
| tkinter | not wired; see the how-to below |

#### tkinter how-to: the bigger-task question

```python
def obey(r):
    ...                                    # the usual: text, ask_reveal, clear_code, quit
    if r.ask_bigger:
        answer = messagebox.askyesnocancel("pygym", r.text + "\n\n" + session.bigger_prompt())
        if answer is not None:             # Cancel = not answered: send nothing
            run(":bigger yes" if answer else ":bigger no")   # may write an exercise: start()

# after show(session.exercise) at start:
q = session.question()
if q is not None:
    obey(q)
```

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
- **Results window** (like Neovim's quickfix window): opens at the bottom after every `:w` / `:wq`,
  pass or fail, and stays open while you keep editing (to compare while fixing); the next `:w`
  refreshes it, `:out` closes / opens it, `:s` closes it. `Ctrl-W w` moves the focus into it to
  scroll (see Keys).

### Keys

| Mode | Key | What it does |
|---|---|---|
| NORMAL | arrows, `h` `j` `k` `l` | move the cursor |
| NORMAL | `i` / `a` | INSERT before / after the cursor |
| NORMAL | `o` | new line below (same indentation), then INSERT |
| NORMAL | `x` | delete the character under the cursor |
| NORMAL | `dd` | delete the line (one empty line is always left) |
| NORMAL | `:` | open the command line |
| NORMAL | `Ctrl-W w` (or `Ctrl-W j`, `Ctrl-W Ctrl-W`) | move the focus into the results window |
| results window | `j` `k` arrows, `Space` / `PageDown`, `b` / `PageUp`, `g` / `G` | scroll |
| results window | `Esc`, `Ctrl-W w` (or `Ctrl-W k`) | back to the code |
| results window | `q` | close the window (like `:cclose`); `:` and `i` `a` `o` work as in the code |
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
| while the AI writes (`:s`, `:level 2` in AI mode) | any key | ignored (the code must not change under the new exercise) |
| while the AI writes | `Ctrl-C` | cancels: the current exercise and your code stay (`Cancelled. You are still on this exercise.`) |

Pasted text is inserted as it is (no extra auto-indent), also in NORMAL mode, if the terminal
supports "bracketed paste" (most modern terminals do).

### Screen layout

```
zip  ●○○  level 1 ─ Write a function 'combine' that takes a list of keys and a    <- question (up
list of values, pairs them up, and returns a dictionary. ...                         to 3 rows)
  1 def combine(keys, values):                                                    <- line numbers,
  2     return dict(zip(keys, values))                                               highlighted code
~                                                                                 <- empty space
 INSERT                       zip-01                       ●○○ 0/3  2,35  <- status line (level 0/3)
-- INSERT --                                                                      <- message / :cmd
```

While the AI writes the next exercise (`:s` in AI mode) the message line shows its progress:

```
 NORMAL                     ai-zip-1-00000001                      ●○○ 0/3  2,5
⠙ writing exercise · try 2 of 4 · 7 s  ━━━━━━━━━───────────────  Ctrl-C cancels
```

`Editor(session, w, h, background=True)` (what `run_editor` uses) starts such commands with
`session.start()`; the run loop calls `editor.tick()` every 0.1 s (`TICK`), which redraws the line
and applies the job's Response once it is done. With `background=False` (the default, used by the
tests) commands block as before.

After a `:w` the results window sits between the code and the status line (80x24, failing check):

```
  5     return dict(zip(keys, values))
~
 results  2/3 passed                                                 :out close   <- title bar
✓ 1  combine(['a', 'b', 'c'], [1, 2, 3])  →  {'a': 1, 'b': 2, 'c': 3}
    │ checking ['a', 'b', 'c']                                                    <- print() output
✓ 2  combine(['x', 'y'], [1, 2, 3, 4])  →  {'x': 1, 'y': 2}
    │ checking ['x', 'y']
✗ 3  combine([], [])  expected {}  got None
    │ checking []
 NORMAL                               zip-01                               5,35
✗ Failed: Not quite right yet: 2 of 3 tests passed.
```

- Height: what the content needs, at most 40% of the screen (`RESULTS_MAX_SHARE`), and the code
  always keeps at least 3 rows (`MIN_CODE_ROWS`); on a tiny screen the window is not drawn.
- A row that does not fit puts `expected ... got ...` on a second, indented row; anything still too
  long ends with `…`. `print()` output shows 3 lines per test (`RESULTS_PRINT_LINES`), then
  `… n more lines` (the CLI shows 20).
- The title bar shows `1-8 of 24` when it can scroll; ` 3/5 passed ` is white on red after a
  failed check, black on green after a passed one; the bar's ` results ` label turns cyan while
  the window has the focus.
- The content rows are built once per check and width (`Editor.results_lines()`), so drawing the
  screen stays fast (`tests/test_editor_results.py` measures it).

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
- The editor only shows the `Response` and obeys `quit`, `ask_reveal`, `ask_bigger` and
  `clear_code` (`:s` loads the new question and resets the buffer and `saved_code`). Both
  questions use PROMPT mode; `Editor.prompt_kind` (`"reveal"` / `"bigger"`) says which, and
  `Editor.prompt_text(width)` what the bottom line shows.
- One-line answers go to the bottom line (`Hint: ` before hints; `✓ `, `✗ ` or `! ` before
  pass, fail and refused). `help`, `solution` and anything longer than the screen width
  (counting that icon) open the pager.
- A `Response` with a `result` (`:w`, `:wq`) opens the results window with it. `kind="results"`
  (`:out`) toggles the window; its text goes to the message line (cut, never the pager).

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
- A check prints the test table (call, expected, got, ✓/✗) and each test's `print()` output
  above the verdict line, also when it passes; `%pygym :out` prints the last one again.
- Everything is printed with Rich, using the same panels as the line CLI (`src/pygym/style.py`):
  in a notebook Rich shows them as HTML, in a plain IPython shell as text. `exercise()` prints
  an intro panel with the `%%pygym` instructions and an example cell, then the exercise panel.
- Hints and docs are panels, `Solution:` is followed by the highlighted code, one-line answers
  get an icon (`✓ Passed! ...`, `✗ Failed: ...`, `! ...`). If `ask_reveal` is set it asks
  `Reveal the solution? [y/N]` with `input()`; if `ask_bigger` is set it asks
  `session.bigger_prompt()` with `input()` and runs the answer as the next command (also right
  after `exercise()` for a level-3 start). After `:s` it prints the new question. On quit it
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
| `printed` | dim | `print()` output under a test (`│ hello`) | `gray50` |
| `error_text` | red | `raised ZeroDivisionError: ...`, a wrong `got` value | `#c62828` |
| `results_bar` / `results_bar_focus` | black on grey58 / bold black on cyan | editor results window title bar | — |
| `results_pass_block` / `results_fail_block` | bold black on green / bold white on red | ` 3/5 passed ` in that bar | — |
| `level` / `status_level` | yellow / yellow on grey23 | `:level` answers and the level line / the `●○○ 2/3` block in the editor's status line | `#c9a400` |
| `progress_spinner` / `progress_bar` / `progress_rest` | cyan / cyan / grey35 | the spinner and the bar while the AI writes | `ttk.Progressbar` |

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
| `style.results(result, function_name)` | the test table and the `print()` output (see [Test results](#test-results-graderesulttests-and-out)) |
| `style.result_lines(result, function_name, width)` | the same, as one `Text` per screen row (the editor's results window) |

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
