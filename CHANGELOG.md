# Changelog

All notable changes to this project are recorded here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow [SemVer](https://semver.org/).

## [Unreleased]
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

### Still planned
- Grow the pilot to ~10 built-ins with 1-2 exercises each.
- Real-machine testing of the tkinter popup (and moving it to `Session.handle`) and of the editor
  on Windows and macOS.
- (Stretch) LLM mode.

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
