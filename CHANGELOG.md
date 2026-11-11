# Changelog

All notable changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]
### Added
- **A new exercise prompt with examples per level** (`pygym.ai.prompt`, new
  `pygym.ai.examples`; design and measurements in `docs/PROMPT_DESIGN.md`). The prompt shows
  1-2 complete example exercises **of the requested level** (bigger-task ones in bigger mode),
  never about the requested function nor calling it, the first a written-for-the-prompt one
  of the most similar kind; a fresh try takes others. The bank has 12 examples (4 beginner, 3
  intermediate, 3 advanced, 2 bigger-task), plus the hand-written exercises that follow their
  level's rule and have 4-6 tests (14 today); a test runs every example through the real
  validator, so a broken one cannot ship. The prompt now has a role and audience, `##`
  sections, the documentation, use case and earlier exercises between tags as data, the level
  as things to do with a code skeleton, the team's rules (imports only when needed and then
  named and explained in the question and hint; a logical everyday problem; every part of the
  result explained), and a checklist at the end. Measured with qwen2.5:1.5b: about 7 of 18
  valid against 4 of 18 for the old prompt (levels 2-3: 3 of 12 against 2 of 12), on small
  samples; most valid exercises are still weak.
- **Content rules (ethics)** in the system message (everyday topics, names from many cultures,
  no stereotypes, no sensitive topics, no real people/brands/personal data, neutral units,
  never "verified"), and a small check (`pygym.ai.ethics`): a short deny-word list and
  real-looking email addresses in the question or hint are sent back for repair ("topic not
  everyday or neutral"); such use cases are dropped. Not complete, by design.
- **Repair messages with a concrete fix** per problem type (`prompt.FIXES`), plus a reminder of
  the level and the target.
- **`ai_self_review` setting** (default `false`): the model reviews its own reply once before
  the checks. Measured no better and twice as slow with qwen2.5:1.5b.
- `generate_exercise(..., seed=)` makes the use case, examples and sampling seeds repeatable
  (tests, experiments); `experiments/prompt_eval.py` compares prompts with a real model under a
  hard call budget.

### Changed
- Ollama requests set `options.num_ctx = 8192`: Ollama 0.18.1 loads qwen2.5:1.5b with 4096
  tokens and silently cuts longer prompts from the start; the longest request (prompt with a
  repair, plus the answer) needs about 4,100.
- `engine.MAX_TOKENS` 700 → 900: 5 of 103 measured replies were cut off at 700.
- The validator fixes two slips itself: a line break inside a string literal, and a missing
  import of an always-allowed module (`math`, `collections` names such as `defaultdict`...).
- **AI time budget of a minute, as a setting.** The AI may now spend up to 60 s on one
  exercise (was 20 s), with up to 10 tries instead of 4. `ai_timeout` in `config.json`
  (10-300 s) changes it; `pygym --status` shows it, and the progress bar fills against it.
- **Each level has a rule, checked on the reference solution's code** (`pygym.ai.levels`).
  Beginner: one line (a comprehension or `x if c else y` is fine there), or a few
  straight-line lines with no `if`, no `x if c else y`, no `for`/`while`, no
  comprehension, no `match` (`and` / `or` are allowed). Intermediate: at least one loop (a
  comprehension counts) and one condition (an `if` inside a comprehension counts), at most 20
  logical lines. Advanced: a loop and a condition plus data structures or algorithms: 2
  different markers, or 1 marker in 7+ logical lines (a dict or set it fills, `collections`,
  `heapq`, `bisect`, a stack `.append` + `.pop`, nested loops, recursion, sorting with `key=`).
  Only the student's function and the helpers it calls count; imports do not. A generated reply
  that breaks the rule is rejected as repairable and the model is told what was missing ("too
  simple for advanced: ... Make the task bigger, with the target as one step"); the progress
  line says `too simple for the level`. The prompt's level text (`prompt.LEVEL_RULES`) is built
  from the same rules (`levels.describe`, `levels.example_idea`). Saved exercises are only
  reused as a fallback when they fit their level (`cache.fits_level`). Why: a level-3 `bin`
  exercise was `return bin(number).replace('0b', '')`, simpler than the level-1 one.
- **The bigger-task question.** A function too small for an advanced exercise on its own
  (`TargetInfo.simple`: at most one input, a method's own value included, nothing optional, and
  no longer call form in its documentation: `bin`, `abs`, `len`, `str.upper`...; not `sorted`,
  `max`, `round`, `str.join`, `zip`, `numpy.cumsum`) is not moved to advanced without asking when
  the AI writes its exercises. Menu level 3, `pygym bin --level 3`, `:level 3` and the level-up
  to 3 ask "`bin` is too small for an advanced exercise on its own. Practise it inside a bigger
  task (bin + loops, conditions and data structures)? [Y/n]". Yes: advanced exercises are bigger
  tasks in which the function is one necessary step (`generate_exercise(..., bigger=True)`);
  no: intermediate. The answer is remembered for the session (`:bigger yes|no` changes it);
  until it is given the level stays below 3, so neither `:s` nor the prefetch writes an advanced
  exercise. Hand-written exercises in preset mode are used as they are. New:
  `Response.ask_bigger`, the `:bigger yes|no` answer command (`ARG_COMMANDS`),
  `Session(..., bigger=None)`, `Session.question()`, `Session.bigger_prompt()`,
  `Session.bigger_task`, `Session.asking_bigger`, `Session.is_simple`,
  `Session.bigger_applies()`, `session.bigger_answer()`, `session.simple_target()`,
  `menu.asks_bigger()`, `menu.start(..., input_fn=None)`. The line CLI, the editor (PROMPT mode,
  `Editor.prompt_kind`), Jupyter and the menu ask it; tkinter: see docs/FRONTEND_GUIDE.md.
- `python tools/validate_exercises.py --levels`: lists the hand-written exercises whose
  reference does not follow the level rules (report only, exit code 0; 33 of 49 today).
- Tests: `tests/test_ai_levels.py`, `tests/test_bigger_task.py` (160 new tests). The level rules
  are off in other tests unless they use the `real_levels` fixture (conftest), so the older fake
  one-line replies keep testing what they test.

- **Exercises are set in real-world use cases of the function.** The first time a function is
  used, the model lists 6-8 short situations where a programmer would naturally reach for it
  (`numpy.cumsum` → "a bank account balance after each transaction", "rainfall so far this
  month"). They are checked (non-empty, at most 120 characters, no duplicates, at least 3) and
  saved in `PYGYM_HOME/generated/usecases/<target>.json`. Every exercise uses one that was not used
  recently, taking turns. The exercise prompt says the situation must be one where the function
  is the natural tool, and the question must explain the inputs and output and state every rule
  the solution uses (no hidden rules). The use-case call has its own slice of the budget (at most
  8 s and 40 %) and a new progress stage `ideas` ("finding real-world uses"). If it fails, the
  exercise is written without a given situation and the next exercise asks again. New module
  `pygym.ai.usecases` (`SCHEMA`, `parse`, `load`, `fetch`, `pick`).
- **Prefetch: the next exercise is written while the student works.** In AI mode, and in preset
  mode for functions without presets, `Session` starts generating the next exercise in a
  background (daemon) thread as soon as one is shown, at the level `:s` would use. `:s` takes it
  at once. If it is still running, `start(":s")` returns a Job that follows the same work (its
  progress, no second request), and cancelling that Job keeps the prefetch. A failed prefetch
  gives the usual fallback. A level change (`:level`, level-up) throws away a prefetch for the
  old level and starts a new one. There is only one at a time, and a new request waits for a
  thrown-away one, so there is never more than one model request. The prefetched exercise is not
  in `seen` until shown. New: `Session(..., prefetch=None)`, `Session.close()` (called by
  quitting commands, `ui.run()` and `jupyter.start()` for the session it replaces),
  `Session.next_ready`, `Job(follow=other)`, `Job.join()`. `session.skip()` uses the prefetch.
- `config.Settings.prefetch: bool = True` (`"prefetch": false` turns it off); `pygym --status`
  shows a `prefetch` row.
- **Thinking models answer in seconds.** The Ollama provider asks `/api/show` once per model per
  process. If `capabilities` lists `thinking` (qwen3, qwen3.5...), it sends `"think": false`;
  other models get no `think` field. If a server answers HTTP 400 about `think`, the request is
  repeated without it and the model is remembered as non-thinking. A 404 from `/api/show` reports
  "model not installed" right away. Measured: qwen3.5:2b answers in 4.5 s (it used to time out
  after 30 s). The OpenAI-compatible provider is unchanged.
- Tests: `tests/test_ai_usecases.py`, `tests/test_prefetch.py`, and the `think` tests in
  `tests/test_ai_providers.py` (57 new tests). `tests/conftest.py` keeps prefetch off and gives
  every target fixed use cases unless a test asks for the real ones (fixtures `real_usecases`,
  `real_prefetch_setting`), so the fake provider's queued replies go to the exercise under test.
- **A progress bar while the AI writes the next exercise** (`:s`, `:level 2` in AI mode), so
  pygym never looks frozen. A model gives no progress of its own, so the bar fills against the
  20 s budget and the text names the try: `⠙ writing exercise · try 2 of 4 · 7 s`, `try 1
  rejected: crashed on its tests`, `using a hand-written exercise instead`. Editor: on the message
  line, redrawn 10 times a second; keys are ignored meanwhile and `Ctrl-C` cancels (the current
  exercise and the code stay). Line CLI and the menu's first exercise: a transient Rich progress
  bar (the menu's spinner is gone). Jupyter: one progress line updated in place (a text line per
  step outside notebooks). Preset exercises show nothing.
- `pygym.ai.generate_exercise(..., on_progress=None)`: called with
  `{"attempt", "max_attempts", "elapsed", "budget", "stage", "detail"}` at every step
  (`asking`, `checking`, `rejected`, `done`, `fallback`); the rest of the contract is unchanged.
  `engine.short_reason()` turns a rejection into a few words.
- `Session.start(command, code) -> Job`: like `handle()` without blocking; the exercise is written
  in a background thread and only applied by `job.result()` (the session and the student's code
  are untouched until then). `job.done`, `job.progress`, `job.elapsed`, `job.cancel()`,
  `Session.start_next(level)`, `Session.needs_wait(command)`, `Session.busy`,
  `Session(..., on_progress=...)` for the first exercise. `handle()` keeps blocking. Generator
  errors of any kind become the preset fallback.
- **Automatic level-up.** After 3 exercises solved at a level (`LEVEL_UP_AFTER`; a passing `:w` /
  `:wq`, once per exercise, not after the solution was shown; skipping does not reset the count)
  the next exercise is one level higher (at most 3): `Passed! Correct! Level up! Next exercise:
  intermediate ●●○ (:s)`. pygym never lowers the level by itself. Preset and AI mode alike.
  `Session.level`, `Session.solved_at_level`.
- **`:level`** (short **`:lv`**): alone it shows the level and the count (`Level: beginner ●○○ —
  2 of 3 solved to level up. :level 1-3 to change`); `:level 2`, `:level intermediate`,
  `:lv i` / `b` / `a` set the level, reset the count and load a new exercise at once. Listed in
  `COMMANDS`, `:intro` and the start screen. New `Response.kind` `"level"`.
- The editor's status line shows the level and the count (` ●○○ 2/3 `); the line CLI and Jupyter
  print `Level: beginner ●○○ 1/3 solved to level up` under a passed check.
- Commands with an argument: `session.is_command_line()` (used by the line CLI) accepts
  `:level <one word>`; `:level 2 3`, `x = :level` and `:w 3` stay code / unknown as before.
- Tests: `tests/test_progress_level.py` (engine events, jobs, cancel, fallback, level-up,
  `:level` in Session / CLI / editor / Jupyter, command recognition) and
  `tests/test_progress_pty.py` (the real editor redraws the bar about 10 times a second; Ctrl-C).
- **Test results after every check.** The student sees every test: the call
  (`combine(['a'], [1])`), the expected value, what the function returned or raised, ✓/✗, and
  what `print()` wrote during that test (and while the code was loading). Shown on a passing
  check too. New `TestResult` and `GradeResult.tests` / `GradeResult.setup_stdout` (old fields
  unchanged); shared builders `style.results()` (Rich table) and `style.result_lines()`.
- **`:out`**: shows the results of the last check again (`kind="results"`; "Nothing checked yet."
  before the first check; a note when the code changed since). Listed in `:intro` and the start
  screen.
- **The editor's results window**, like Neovim's quickfix window: opens at the bottom after every
  `:w` (at most 40% of the screen, the code keeps at least 3 rows), title bar ` results  3/5
  passed `, one row per test with its `print()` lines (3, then `… n more lines`) under it. It stays
  open while editing and refreshes on the next `:w`; `:out` closes / opens it; `Ctrl-W w` moves
  the focus into it to scroll (`j` `k`, `Space`, `g` `G`), `q` closes it, `Esc` goes back.
- The line CLI and Jupyter print the table (`#`, call, expected, got, ✓/✗) and the `print()`
  output above the verdict line; `%pygym :out` in Jupyter.
- After a timeout the student sees which test was running and what it had printed: the runner
  reports its progress on stderr (`_runner.read_progress()`), finished tests keep their rows.
- Tests: `tests/test_results.py` (runner, grader, renderers), `tests/test_editor_results.py`
  (the window: opening, toggling, refreshing, scrolling, sizes from 20x5 to 120x50, render time),
  more in `test_session.py`, `test_cli.py`, `test_jupyter.py` and a pseudo-terminal `:w`.
- **AI exercises for any installed library** (`numpy.cumsum`, `pandas.concat`,
  `requests.get`, `os.listdir`...), not only 22 standard modules. A shared deny list
  (`_runner.DENIED_MODULES`, used by the menu, the AI engine and the sandbox) refuses GUI
  toolkits, things that start programs or control Python (`subprocess`, `ctypes`, `sys`...),
  `random`/`time`, `pip` and pygym itself, each with a reason. File and network libraries
  need an OS sandbox (refused on Windows). A library that is not installed gets the exact
  `python -m pip install ...` command (pygym never installs anything).
- **The sandbox** (`pygym.sandbox`): every run (student code and model code) happens in a new
  temporary folder that is deleted afterwards, never in the student's folder; a Python audit
  hook blocks writes outside it, starting programs, `ctypes`, opening a browser and network
  access; on Linux with bubblewrap the system is also read-only, the home folder hidden and
  the network cut; on macOS `sandbox-exec` (untested). Secret environment variables are not
  passed on; a 16 MB file-size limit was added. `grader.sandbox_level()`; the menu header and
  `pygym --status` show the level; `PYGYM_SANDBOX=audit-hook` switches the OS layer off.
  Overhead of bubblewrap: about 3-5 ms per run (a plain run takes about 21 ms in total).
- New optional `Exercise` fields `files` (small text files written into the folder before
  every test; `open()` is allowed then) and `http_fixtures` (answers of a local test server
  the code reaches through `BASE_URL`). The AI prompt asks for them when the target needs
  files or the network.
- Results of numpy / pandas / dataclasses / Decimal ... are converted the same way for the
  reference and the student (`.tolist()`, `.to_dict()`, `repr`), and `NaN` equals `NaN`.
  `import numpy as np`, `from numpy import cumsum` and the method form `arr.cumsum()` are
  accepted (numpy ufuncs and the method form get the static check only).
- AI validation rejects a reference that hits the sandbox ("blocked: ..."), sets its own
  `BASE_URL`, or opens a file it did not provide, with a reason the model can act on. A
  reference may import other allowed libraries; the student may then import them too.
- Tests: `tests/test_sandbox.py` (each layer, files, the test server, blocked actions, command
  lines, the audit hook) and `tests/test_libraries.py` (library policy, results conversion,
  numpy grading, the engine end to end with numpy, requests and file exercises); numpy, pandas
  and requests are optional for them.
- **More built-ins with hand-written exercises:** 17 built-ins now have presets (was 3):
  `map filter enumerate len max min range any all sorted sum zip` at levels 1, 2 and 3, and
  `reversed abs divmod round isinstance`. 44 new exercises (49 in total), all listed as
  **pending human review** in `docs/EXERCISE_REVIEW.md`. New `tests/test_dataset.py`.
- **AI mode** (`pygym.ai`): a local model (Ollama, default `qwen2.5:1.5b`; or any
  OpenAI-compatible server) writes a new exercise for a built-in, a method (`str.join`) or a
  function from an allowed standard module (`statistics.median`, `itertools.chain`). The model's
  reference solution runs only in the sandbox; the expected results come from running it, never
  from the model. Up to 4 attempts in 20 s, good exercises are cached in
  `PYGYM_HOME/generated/`, and `PYGYM_NO_AI=1` turns it all off. See `docs/AI_MODE.md`.
- Two modes, `preset` and `ai`: `exercise(name, mode=...)`, `Session(name, level, mode)`, saved
  in `PYGYM_HOME/config.json` (`pygym.config`). Preset mode asks the AI for functions that have no
  preset; AI mode falls back to presets. `Session.notice` says which happened.
- **The `pygym` command and main menu** (`python -m pygym` also works): a Rich menu that asks for
  the library, the function and the level (beginner / intermediate / advanced), with `m` to
  switch modes. Flags: `pygym zip --level 2 --mode ai --ui text`, `--list`, `--status`,
  `--set-mode`, `--set-model`, `--version`.
- Every front-end shows `Session.notice` (the AI warning or a fallback note) when an exercise
  appears and after `:s`; generated exercises get a yellow **AI** badge next to the level.
- `:help` works for dotted targets (`str.join`, `statistics.median`, `itertools.chain`).
- The grader can check module functions and methods (`allowed_imports` per exercise, limited to a
  list of safe modules; a runtime "was it called" check for built-ins and module functions;
  methods of built-in types are checked statically only).
- Tests: AI engine with a fake model (no network), config, menu (incl. a pseudo-terminal run),
  dataset, notice/badge in every front-end; `tests/conftest.py` keeps every test away from the
  real `~/.pygym` and from any real model.
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
- The exercise prompt no longer picks one of 30 fixed themes (`prompt.SCENARIOS`) at random. A
  theme unrelated to the function led to exercises like a "plant watering schedule" for
  `numpy.cumsum` with rules the question never stated. The `FOCUS` list (data shapes such as
  "working with strings") is gone too: it often fought the use case. `build_messages(info,
  level, use_case=..., avoid=..., repair=...)` replaces `scenario=` / `focus=`. A fresh retry
  takes the next use case; a repair keeps it. The few-shot example now states its tie rule.
- The Ollama provider sends `/api/show` before the first chat with a model (once per process).
- `Session(name, difficulty=None)` (and `exercise(name)`) now starts at level 1 instead of a
  random level: levels need a starting point. `session.difficulty` reads `session.level`.
- The menu shows the progress bar instead of a spinner while the first exercise is written;
  Ctrl-C there now cancels and returns to the menu.
- `LEVELS` and `parse_level` moved from `menu` to `session` (still importable from `menu`).
- "Unknown command ... Try:" leaves out `:level`, so the line still fits 80 columns.
- `print()` output is no longer thrown away: the runner keeps it per test (4000 characters each,
  then `… (output cut)`); stray C-level output still goes nowhere, so the JSON result is safe.
- A wrong answer says how many tests passed: `Not quite right yet: 3 of 5 tests passed.` (was
  `Not quite right yet.`); a timeout says in which test it happened.
- The grader compares every test (it used to stop at the first wrong one) and runs the code even
  when the target is missing, so those rows are filled too (the verdict is unchanged).
- A test that raises `SystemExit` no longer stops the other tests.
- "Unknown command" lists the commands after `Try:` (was `Commands:`), so the longer list still
  fits an 80-column editor line.
- The runner starts with `python -E` (was `-I`, which hid libraries installed with
  `pip install --user`); pygym's own folder is still removed from `sys.path` first.
- `os.listdir`-style targets are allowed with an OS sandbox; three tests that expect `os` to be
  refused now pin the basic sandbox level (`PYGYM_SANDBOX=audit-hook`).
- The menu refuses `sys`, `time`, `random`, `subprocess` and the other denied libraries with
  the reason, instead of listing their functions.
- The menu no longer prints `Session.notice` itself; the front-end shows it (it appeared twice in
  the line CLI).
- `:help` is described as "the function's own documentation" (it is no longer only built-ins).
- `tools/validate_exercises.py` also checks required fields, levels, function names, signatures and
  that `expected_outputs` has one entry per test.
- Rich is now a required dependency (`rich>=13.0`); pygym is no longer dependency-free.
- `exercise(ui="auto")` picks: notebook → Jupyter, Linux/macOS terminal → editor,
  otherwise tkinter popup (the Windows default), otherwise the line CLI.
- The line CLI recognises a command on any line by itself, prints an intro with all commands,
  and no longer needs `END` to submit.

### Fixed
- The starter code and the signature never show type hints (`def f(number: int) -> str:` came
  through when the reply did not import `typing`): annotations are stripped from generated
  references whenever they have any, and `Exercise.starter_code` drops them from any signature
  (`models.plain_signature`), also for exercises saved by older versions.
- **P0-1:** an exercise with missing or too few `expected_outputs` let any code pass. `grade()` now
  reports it as broken ("Use :s to skip it."), and the result loop checks that the number of
  outputs matches.
- `:s` crashed (uncaught `ExerciseUnavailable`) for a function without presets when the next
  generation failed; it now keeps the current exercise and says no other one is available.
- AI mode: a test case that also contained its answer (`{"orders": [...], "expected_result": 3.5}`,
  seen in a real reply) was passed to the function as the argument; such keys are now ignored.
- The line CLI crashed with `EOFError` on Ctrl-D / closed input; it now exits cleanly.

### Still planned
- Human review of the 44 new preset exercises (`docs/EXERCISE_REVIEW.md`).
- AI mode quality: with qwen2.5 1.5B/3B most replies are rejected (0 of 5 real requests gave a
  usable exercise on the development laptop). Try bigger models / filling the cache ahead of time.
- Real-machine testing of the tkinter popup (and moving it to `Session.handle`, plus showing
  `Session.notice` and the AI badge inside it) and of the editor on Windows and macOS.

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
