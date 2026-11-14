# Handoff: real-machine testing and hardening of pygym (state on 2026-11-14)

Written for whoever continues this work (a new session with no memory of the old one). Read this
file first. It says what the project is, what was changed and why, what was verified on real
machines (with the evidence), what is still unverified, and what to do next.

## 1. The project in five lines

- `pygym` (package in `src/pygym`, version still `0.2.0`): students practise one Python built-in at a
  time. Users are mostly Hong Kong university students (Windows laptops, Google Colab; some Mac).
- Front-ends: **text**, **tkinter popup** (the default on Windows), **terminal editor** (vim-like,
  `ui="editor"`; default on Linux/macOS terminals), **Jupyter/Colab** (`exercise("zip")`, answer in a
  cell that starts with `%%pygym :w`).
- Sandbox levels for running student code: `bwrap` (Linux), `sandbox-exec` (macOS, never run on a
  Mac), `audit-hook` (Windows, and the fallback). Windows has *only* the audit hook.
- Exercises: 83 hand-written presets in `src/pygym/data/exercises.json` (49 original + 34 added),
  plus AI mode, which asks a model for new ones. `src/pygym/ai/examples.py` feeds the model few-shot
  examples from a hand-written `BANK` and from presets that follow the level rules (4-6 tests).
- Docs worth reading: `docs/AI_MODE.md`, `docs/FRONTEND_GUIDE.md`, `docs/EXERCISE_REVIEW.md`,
  `CHANGELOG.md` (the `[Unreleased]` section lists everything below).

## 2. Working conditions (they shaped everything)

- The assistant's container: Linux, Python 3.12, **no network, no pytest, no rich**. Workaround used
  (it is gone with the container, rebuild it if needed): pip vendors Rich, so copy
  `/usr/lib/python3/dist-packages/pip/_vendor/rich` to a folder on `PYTHONPATH` as `rich`; a ~150-line
  pytest stand-in ran the suite (fixtures, parametrize, monkeypatch, capsys). Method that made it
  trustworthy: run the ORIGINAL zip and the PATCHED tree under the same stand-in and diff results.
- Nothing could be run on Windows, macOS, Python 3.9 or in a browser by the assistant. The user (Jason
  Choy) ran things and pasted output or screenshots. Real machines used so far: **Windows 11 (build
  26200; Python reports "Windows-10-10.0.26200")** with Python 3.13 (Microsoft Store) and 3.9.13
  (python.org, in `.venv39`), Windows Terminal; **Google Colab** (Python 3.13). No Mac, no old
  console host (conhost), no Developer Mode.
- Deliverable each round: `/mnt/user-data/outputs/pygym-JC.zip` (call `present_files`; the user did not
  see the zip once because it was not attached).
- The user wanted the new presets written without AI mode and checked mechanically.

## 3. What changed (all in the zip; originals untouched unless listed)

Source (`src/pygym`):
- `data/exercises.json`: +34 presets (ids `<target>-04/-05`, plus `reversed-03/-04`, `abs-03/-04`), the
  first 49 byte-identical. Every new one follows its level rules, has 5-6 tests, fake data only, is
  mutation-tested (flipped `<`, constants, `and`/`or`, `reverse=True`: all behaviour-changing mutants
  fail a test; survivors are equivalent mutants). Example pool for AI mode: eligible presets 14 -> 48
  (level 2: 3 -> 20, level 3: 1 -> 18). **No human has reviewed them** (see `docs/EXERCISE_REVIEW.md`).
- `jupyter.py`: `in_notebook()` follows the class hierarchy (Colab's shell is `google.colab._shell.Shell`,
  a subclass of `ZMQInteractiveShell`); `ANSWER_HERE` line under every exercise ("answer goes in a NEW
  cell").
- `style.py`: `code()` returns `_Code`, which picks the background per console (a notebook gets the
  theme's dark background; Rich painted an explicit "default" background white in HTML).
  `_highlighter()` builds a plain `Syntax`.
- `editor.py`: raw Windows console input (`windows_events_to_keys`, `_ConsoleReader` over
  `ReadConsoleInputW`, `_MsvcrtReader` fallback, `_windows_reader()`); cursor column uses `cell_len`
  (double-width 中 no longer shifts the cursor).
- `sandbox.py`: `macos_profile(..., executables=())` also allows exec of the interpreter file (Apple's
  CLT Python 3.9 lives outside its prefix; never run on a Mac); `child_env()` sets `NO_PROXY=*`.
- `menu.py`: `_tolerate_any_encoding()` (stdout/stderr `errors="replace"`) when the CLI makes its console.
- `ui.py`: `run_tk(session, _driver=None)` test hook.

Tools (`tools/`, new): `check_py39.py` (static 3.9 scan, runs anywhere), `real_machine_check.py`
(self-reporting kit: `--keys`, `--keys2`, `--keys3`, `--tk`; macOS probes run on a Mac only),
`notebook_check.ipynb` (cell 0 says whether the session has the patched pygym).

Tests: new `test_notebook_detect`, `test_notebook_prompt`, `test_code_background`,
`test_editor_windows_raw`, `test_editor_wide_chars`, `test_tk_driver`, `test_windows_pipes`; edited
`conftest.py`, `test_editor.py`, `test_editor_windows.py`, `test_libraries.py`, `test_sandbox.py`
(platform-aware, see section 5).

## 4. Verified on real machines (evidence)

| Area | Result |
|---|---|
| Windows 11, Py 3.13 and 3.9.13: `real_machine_check.py` | all PASS: audit hook blocks outside writes, `os.system`, non-loopback connect; fixture server answers; libraries refused; endless loop killed; non-ASCII survives |
| Windows memory limit | **not enforced** (700 MB allocation succeeded) |
| Windows `--keys`/`--keys2` | `getwch()` gives `\xe0` for the letter à AND before arrow keys, and `kbhit()` is False in both cases: it cannot tell them apart. `ReadConsoleInputW`: arrows = char `\x00` + virtual key; à, é, 中 (typed, Alt-code, pasted) = char with vk 0 |
| Windows `--keys3` (the editor's own input layer) | LEFT, HOME, DELETE, à, é, 中, typed text OK; Ctrl+Left, Shift+Tab, F1 ignored as intended |
| Windows tkinter (`--tk`, Py 3.9) | PASS: wrong->red Failed, right (with 中文 à é in a comment)->green Passed, Hint, Show solution, Skip; window 671x378 px at 96 dpi |
| Colab | patched build: `in_notebook()` True, front-end `jupyter`, `%%pygym :w` works, ■ interrupt clean, code box dark (was white) |
| Colab, old build | reproduced the bug: in_notebook False, text front-end, no `%%pygym` |
| Full pytest on Windows, Py 3.9.13 | 1607 passed, 31 skipped, **9 failed** (all analysed in section 5) |

## 5. The 9 Windows failures: causes and what was done (needs one re-run to confirm)

| Failing test | Cause | Fix in this zip |
|---|---|---|
| `test_editor::test_auto_routing[editor]` | test ignored that Windows defaults to tk | patches `is_windows`; new Windows routing test |
| `test_libraries::test_not_installed_library_gets_a_pip_hint` | hint quotes a Python path that has a space (`C:\Users\Jason Choy\...`); test expected it unquoted | test expects the quoted form |
| `test_menu::test_python_dash_m_pygym_first_screen_and_quit` | **real bug**: redirected output uses cp1252/cp950, cannot show `●`/`✗`: UnicodeEncodeError (reproduced on Linux) | `menu._tolerate_any_encoding()` + `test_windows_pipes.py` |
| `test_progress_level::test_cli_shows_a_progress_bar...` and `test_style::test_hint_is_a_yellow_panel` | Rich on Windows under pytest capture thinks it is a legacy console: `-` bars, square boxes (reproduced by simulation) | autouse fixture in `conftest.py` |
| `test_sandbox::test_base_url_fixture_server_answers[audit-hook]` | **real bug**: urllib reads proxy settings from the registry; the hook refuses `winreg.OpenKey`; every HTTP exercise got a spurious `(pygym blocked: ...)` note | `NO_PROXY=*` in `sandbox.child_env()` + test |
| `test_sandbox::test_bwrap_command_line` | POSIX paths; bwrap is Linux-only | skipped on win32 |
| `test_sandbox::test_guard_follows_symlinks_out_of_the_folder` | creating a symlink needs Developer Mode/admin | skips when symlink creation fails |
| `test_sandbox::test_timeout_and_memory_limits_still_apply` | Windows has no memory limit | split: timeout test everywhere, memory test skipped on win32 (reason points here) |

On Linux (stand-in): 1647 passed, 2 failed, 10 skipped; no regressions against the original except
the renamed test above. The 2 failures: `test_network_is_blocked` (needs DNS; the container has none)
and one `test_prefetch` timing test.

## 6. Open items, in the order I would do them

1. **Re-run the suite on Windows 3.9 (and 3.13)** after unzipping: `py -3.9 -m venv .venv39`,
   `.\.venv39\Scripts\python -m pip install -e ".[dev]"`, `.\.venv39\Scripts\python -m pytest -q`.
   Expect only prefetch flakes and skips. Also run `pytest tests/test_jupyter.py` anywhere IPython is
   installed (it was skipped in the assistant's container; the new `ANSWER_HERE` line should not break
   it, its assertions are `in` checks).
2. **Windows memory limit** (real gap: a student's `'x' * 10**10` is stopped only by the 5 s timeout).
   Plan: Job Object via `ctypes` (`CreateJobObjectW`, `SetInformationJobObject` with
   `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`, `JOB_OBJECT_LIMIT_PROCESS_MEMORY`, then
   `AssignProcessToJobObject` right after `Popen`; `sandbox.run` currently uses `subprocess.run`, so
   it needs `Popen` + `communicate(timeout)`). Tiny race before the assignment is acceptable. Blind
   Windows code: add a probe to `real_machine_check.py` and un-skip the test on win32.
3. **PowerShell and the pip hint**: with a space in the Python path the hint is
   `"C:\...\python.exe" -m pip install x`, which PowerShell does not execute without a leading `&`
   (cmd.exe does not accept the `&`). Decide: print `python -m pip install x` when `python` on PATH is
   this interpreter, else the quoted form.
4. **Terminal editor on Windows beyond input** (manual, in Windows Terminal and in the old console host,
   `conhost powershell`): `python -m pygym zip --ui editor` (use the venv's python or `pip install -e .`;
   bare `python -m pygym` fails with "No module named pygym" when the package is not installed). Check:
   resize redraw; `i`, type, Esc, `:w` Enter gives 3/3; `# 中文` keeps the cursor after the text;
   multi-line paste (Ctrl+Shift+V) arrives intact; `:q!` restores the terminal; in conhost an Alt+0224
   à appears exactly once (the code accepts a key-up of Alt that carries a character, never seen).
   Could be automated by injecting key events with `WriteConsoleInputW`.
5. **macOS**: nothing has run. Run `python tools/real_machine_check.py` on a Mac (probes the
   `sandbox-exec` rules one by one, incl. whether `RLIMIT_AS` is enforced). Likely issue: memory limit
   ignored. Apple's own Python 3.9 should now start inside the sandbox (unverified).
6. **Symlink Guard on Windows**: enable Developer Mode and run the symlink test.
7. Bump the version (Colab showed `v0.2.1`, the Windows copy `0.2.0`: two different builds in use).
8. Human review of the 34 new presets (`docs/EXERCISE_REVIEW.md` has the table), and of the 28 older
   presets that break the level rules (report-only; deliberately left unchanged).
9. Flaky tests: `test_prefetch.py` has at least three timing-dependent tests that fail in roughly 15% of
   runs on the original code too (`test_quit_cancels_the_prefetch`, `test_w_works_while_a_prefetch_waits...`,
   `test_a_failed_prefetch_gives_the_usual_fallback`). Worth hardening.
10. Known and accepted: Tk text is not DPI-aware (may look blurry at 125-150% scaling; not reported yet);
    by reading the code, the tk front-end runs a check on the UI thread, so the window freezes until the check ends (up to the timeout); not observed.

## 7. Lessons that are not obvious from the code

- `msvcrt.kbhit()` does not see the buffered second half of an arrow key; do not use it to tell "à" from
  an arrow. Raw console events can.
- Alt codes with a leading zero use the ANSI code page: Alt+0279 is 23 (a control character), not ė.
- Windows Terminal delivers an Alt+numpad character as a key-down with vk 0; the old console host sends it
  when Alt is released (handled, never seen).
- `ctypes.wintypes.BOOL`/`WCHAR` change size on Linux: use fixed-width types for structures that tests
  check off-Windows (the `INPUT_RECORD` is 20 bytes).
- Rich exports an explicit `"default"` background as white in HTML (notebooks) and, under pytest on
  Windows, replaces `╭` and `━` with ASCII because it assumes a legacy console.
- `exercises.json` is written by `python tools/validate_exercises.py --fill` (indent 2, no ASCII escaping);
  keep ids stable (progress is stored by id); examples shown to the model need 4-6 tests and the level
  rules; fake emails must use example.com/.org/.net (`ai/ethics.py` checks).
- Version numbers do not tell builds apart here: `notebook_check.ipynb` cell 0 checks for the Colab fix.
- Always attach the zip with `present_files`.

## 8. Commands

```
python tools/validate_exercises.py            # data checks      (--levels: level report, --fill: expected outputs)
python tools/check_py39.py                    # static 3.9 scan  (runs on any Python)
python tools/real_machine_check.py            # sandbox/console/encoding checks; add --keys3 or --tk on Windows
pytest -q                                     # the suite (needs: pip install -e ".[dev]")
```
