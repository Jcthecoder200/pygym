# Changelog

All notable changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]
### Added
- **34 more hand-written exercises** (83 in total): an intermediate and an advanced one for each
  of the 17 built-ins (`reversed` and `abs` get levels 2-3 / 2-3 too). They all follow the level
  rules (`validate_exercises.py --levels` lists none of them), have 5-6 tests, and use made-up data.
  Mutation-tested: every behaviour-changing bug tried on their reference solutions (a flipped
  `<`, a changed constant, `and` -> `or`, a dropped `reverse=True`) fails at least one test.
  Because they follow the level rules they are also valid prompt examples: the example pool for
  AI mode grew from 14 to 48 presets (level 2: 3 -> 20, level 3: 1 -> 18).
- `tools/check_py39.py`: static check that the code and the exercise solutions still run on
  Python 3.9 (stdlib only, runs on any Python).
- `tools/real_machine_check.py` and `tools/notebook_check.ipynb`: self-reporting checks to run on
  Windows, macOS, Python 3.9 and in a browser notebook.
### Fixed
- Windows editor: typing the letter "à" (or pasting text with it) made the editor wait for another
  key and drop it, because `msvcrt.getwch()` returns the same "\xe0" for "à" and for the prefix
  of every arrow key, and `kbhit()` cannot tell them apart (measured on Windows 11). The editor now
  reads raw console events (`ReadConsoleInputW`), which keep the two apart, and falls back to
  `msvcrt` if that is not possible. Ctrl+arrows, Alt chords, Shift+Tab and F-keys stay ignored.
- Highlighted code was a white box with pale text in a dark notebook (Colab, JupyterLab dark):
  Rich paints an explicit "default" background white when it exports HTML. Notebooks now use the
  highlight theme's own background; terminals still use their own.
- Google Colab was treated as "not a notebook" (its shell class is a subclass of
  `ZMQInteractiveShell` with another name), so `exercise()` used the text front-end there.
- macOS `sandbox-exec`: the profile now also allows starting the interpreter file itself. Apple's
  Python 3.9 (Xcode Command Line Tools) runs from outside its prefix, so the self-test failed and
  the sandbox silently fell back to the audit hook. Untested on a Mac.

## [0.2.0] - 2026-11-11
### Added
- **`pygym` command with a Rich main menu** (also `python -m pygym`): library → function →
  beginner / intermediate / advanced, then the exercise opens in the editor. `m` switches
  preset ↔ AI mode, `l` level, `u` window, `?` help, `q` quit. Without the menu:
  `pygym zip --level 2 --mode ai --ui text`, `pygym --list statistics`, `pygym --status`.
- **More built-ins with hand-written exercises**: 17 built-ins (was 3), 49 exercises, levels
  1-3 for `map filter enumerate len max min range any all sorted sum zip`, plus `reversed abs
  divmod round isinstance`. They still need a human review (`docs/EXERCISE_REVIEW.md`).
- **AI mode**: a small local model (Ollama, default `qwen2.5:1.5b`) writes exercises for any
  function of any installed library (`numpy.cumsum`, `str.join`, `statistics.median`...),
  including built-ins without hand-written exercises. Every reply is checked before a student
  sees it: the reference solution runs in the sandbox on every test, must call the function and
  fit the level; expected outputs come from running it, never from the model. Exercises are set
  in real-world use cases of the function, cached, and marked with an `AI` badge and a "may be
  incorrect" warning. Falls back to hand-written exercises when the model fails or is offline.
- **Sandbox**: every check runs in a fresh temporary folder behind bubblewrap (Linux),
  sandbox-exec (macOS, untested) or a Python audit hook (everywhere): no writes outside the
  folder, no network except a local test server (`BASE_URL`), no programs. Exercises can bring
  small fixture files and fake HTTP answers.
- **Results after every check** (`:w`): one row per test with the call, expected value, your
  value or error, and your `print()` output. In the editor a Neovim-quickfix-style window
  (`:out` toggles it, `Ctrl-W w` to scroll); a Rich table in the line CLI and Jupyter.
- **Levels**: rules for what beginner / intermediate / advanced mean, checked on generated
  solutions; level-up after 3 solved exercises (never down); `:level` / `:lv` to change it.
  Functions too small for advanced on their own (`bin`, `len`...) ask whether to practise them
  inside a bigger task.
- **Progress bar while the AI writes** (editor, CLI, menu, Jupyter) and **prefetch**: the next
  AI exercise is written in the background while the student works, so `:s` is usually instant.
- Starter code includes the import a library function needs (`import statistics`).
- Settings in `~/.pygym/config.json`: `mode`, `level`, `model`, `prefetch`, `ai_timeout`
  (default 60 s), `ai_self_review` (off).
- Docs: `docs/AI_MODE.md`, `docs/PROMPT_DESIGN.md`, `docs/EXERCISE_REVIEW.md`, updated
  `docs/FRONTEND_GUIDE.md`; `experiments/prompt_eval.py` and `experiments/readme_demo.py`.

### Changed
- `exercise(name)` without a level starts at level 1 (was a random level).
- A wrong answer says how many tests passed: `Not quite right yet: 3 of 5 tests passed.`
- The grader runs every test (it stopped at the first failure) and keeps `print()` output.
- The sandbox runner starts with `python -E -B` (was `-I`) so user-installed libraries work.
- Thinking models (qwen3.5) get `"think": false`; Ollama calls set `num_ctx` 8192.
- API for front-ends: `Session.start(command, code) -> Job` (background generation with
  progress), `Session.notice`, `Session.level`, `Response.ask_bigger`, `GradeResult.tests`,
  `Exercise.starter_code`; see `docs/FRONTEND_GUIDE.md`.

### Fixed
- **P0-1**: an exercise with missing expected outputs let any code pass; it is now an error.
- A generated question could ask for a different function name than the starter code.
- `:s` crashed when the next generation failed for a function without presets.
- The line CLI crashed on Ctrl-D; switching to AI mode printed the warning twice; "No exercise"
  now says why the AI could not write one.
- Type hints no longer appear in the starter code.

### Known issues (must be solved before 1.0)
- **AI exercise quality with the default 1.5B model is low.** Levels 2-3 rarely pass the checks
  (about 3 of 12 in our test); many exercises that pass are still weak: rules the question never
  states, illogical stories (a median used to count buses), nonsense expected outputs
  (`bin(-34)[2:]` → `'b100010'`). The checks prove the code runs, not that the question makes
  sense. A larger model helps a lot (qwen3.5 9.7B: 4 of 4 sensible) but is too heavy for most
  student laptops. Decision pending.
- **44 hand-written exercises are not reviewed yet**, and 28 of 49 do not follow the new level
  rules (`python tools/validate_exercises.py --levels`).
- **The tkinter popup (the Windows default) is still the 0.0.0 one**: no results window, no
  `:out`, `:level`, progress bar, AI badge or bigger-task question. `docs/FRONTEND_GUIDE.md`
  has how-tos.
- **Untested**: Windows editor and Windows sandbox (audit hook only; file/network libraries are
  refused there), macOS sandbox-exec, Python 3.9, Jupyter progress in a browser notebook.
  (The test suite passes on Linux with Python 3.10, 3.11, 3.12 and 3.14.)
- Without bubblewrap, generated code can still read the student's files (it cannot write or
  send them).
- Some generated use cases do not fit their function (about half for `zip`, most for `bin`).
- The level is kept only for one session, so a student who always ends with `:wq` never
  levels up.
- The ethics word check is small; it misses things like gendered roles ("help him").
- Ctrl-C during generation stops waiting, but the request already sent keeps the model busy
  until it finishes.
- AI mode needs Ollama installed separately; pygym only shows "AI offline" and the docs.

## [0.1.0] - 2026-11-10
### Added
- `Session.handle(command, code)` returning a `Response`: one place for all command rules, so every
  front-end (CLI, editor, Jupyter, tkinter) behaves the same.
- vim-style commands: `:w` `:wq` `:q` `:q!` `:h` `:help` `:s` `:solution` `:intro`.
  `:q` refuses to quit when the code changed since the last `:w` or when the last check failed
  (then offers the solution); `:wq` on a failed check offers the solution and stays open.
- Neovim-style terminal editor (`ui="editor"`): NORMAL / INSERT modes, `:` command line, status bar,
  start screen with every command, pager for `:help` / `:solution` / `:intro`, syntax highlighting.
  Default in Linux/macOS terminals; on Windows 10+ consoles available with `ui="editor"`.
- Jupyter front-end: `exercise("zip")` in a notebook registers the `%%pygym :w` cell magic
  (and `%pygym :h` etc.).
- Shared Rich look (`src/pygym/style.py`) for the line CLI and Jupyter.
- `docs/FRONTEND_GUIDE.md` for front-end developers; `experiments/` with a tkinter sketch,
  a Rich preview, a raw-key demo and a scripted demo for recordings.
- Tests for the session rules, the line CLI, the editor (incl. a pseudo-terminal run and a
  Windows layer with a fake `msvcrt`), Jupyter and the styles (186 tests in total).

### Changed
- Rich is now a required dependency (`rich>=13.0`); pygym is no longer dependency-free.
- `exercise(ui="auto")` picks: notebook → Jupyter, Linux/macOS terminal → editor,
  otherwise tkinter popup (the Windows default), otherwise the line CLI.
- The line CLI recognises a command on any line by itself, prints an intro with all commands,
  and no longer needs `END` to submit.

### Fixed
- The line CLI crashed with `EOFError` on Ctrl-D / closed input; it now exits cleanly.

## [0.0.0] - 2026-11-09
### Added
- Project kernel: `exercise("name")` entry point and `Session` (submit / hint / solution / skip).
- Sandboxed grader: separate process, timeout, memory limit, restricted imports,
  runtime check that the target built-in was actually called, normalized output comparison.
- Text UI (terminal / Jupyter) and tkinter popup UI.
- Local progress file.
- Pilot dataset: 5 exercises across `zip`, `sum`, `sorted`.
- Exercise validation tool and 11 grader tests.
- GitHub Actions workflow to test, build and publish to PyPI on release.
