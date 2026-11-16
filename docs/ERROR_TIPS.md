# Tips at your error

When the student's own code crashes, pygym prints one line under the normal traceback:

```text
Traceback (most recent call last):
  File "scores.py", line 5, in <module>
    for name, score in zip(names, len(scores)):
                       ~~~^^^^^^^^^^^^^^^^^^^^
TypeError: 'int' object is not iterable
pygym: this line uses zip() and len(); 30-second exercise? pygym zip
```

There is no model and no network involved. The tip comes from the traceback, the source of the
failing line and the bundled exercises, so it is instant (about 15 ms when pygym is already
imported) and the same every time for the same error. The code is in `src/pygym/tips.py`.

## Turning it on and off

| | effect |
|---|---|
| `import pygym`, `from pygym import exercise` | on for this process, unless the saved setting `tips` is off |
| `%load_ext pygym` / `%unload_ext pygym` | on / off for this IPython session (ignores the setting) |
| `pygym.tips(True)` / `pygym.tips(False)` | on / off for this process (ignores the setting) |
| `pygym --tips on` | the global hook (below) + saves `tips: true` |
| `pygym --tips off` | removes the global hook files + saves `tips: false` |
| `pygym --tips status`, `pygym --status` | the setting, and where the global hook is installed |
| `PYGYM_NO_TIPS=1` (environment) | no tips at all in that process |

`pygym.tips` is both the module and the switch: the module is callable, so `pygym.tips(False)`
and `import pygym.tips` never shadow each other.

## Per environment

| environment | hook | where the failing line's source comes from | command shown |
|---|---|---|---|
| script (`python app.py`) | `sys.excepthook` | `linecache` (the file) | `pygym zip`, or `<python> -m pygym zip` when no `pygym` is on PATH |
| new REPL (3.13+) | `sys.excepthook` (the REPL calls it when it is not the default) | `linecache`, registered by code object (`<python-input-N>`) | `exercise('zip')` / `pygym.exercise('zip')` / `from pygym import exercise; exercise('zip')` |
| classic REPL (`PYTHON_BASIC_REPL=1`, or before 3.13) | `sys.excepthook` | 3.13+: `linecache` by code object (`<stdin-N>`); older: the last readline history items | as above |
| IDLE shell | `sys.excepthook` | the GUI process, over IDLE's RPC (`<pyshell#N>`) | as above |
| IPython terminal | `ip.set_custom_exc` | `linecache` (IPython registers cells) | as above; printed on stdout like IPython's tracebacks |
| Jupyter, JupyterLab, VS Code notebooks, Colab (IPython kernels) | `ip.set_custom_exc` | `linecache` (ipykernel registers cells) | as above; shown as a small output (text/plain + text/html) |

The interactive command is chosen from the user namespace (IPython's `user_ns`, else
`__main__`): a name bound to `pygym.exercise` gives `exercise('zip')` (or the alias), a name
bound to the `pygym` module gives `pygym.exercise('zip')` (or `pg.exercise(...)`), otherwise
`from pygym import exercise; exercise('zip')`. "Interactive" means an IPython shell, `sys.ps1`,
`python -i`, or IDLE. In a script the command is for the shell: `pygym zip` if a `pygym`
executable is on PATH, else this interpreter with `-m pygym zip` (its short name when that name
on PATH is this interpreter, else its full path; `py -m` is never guessed).

The exercise then picks its own front-end as before (`ui="auto"`: notebook -> jupyter,
terminal -> editor, Windows and IDLE -> tk window).

### The output

- Always after the unchanged traceback: the hook calls the previous `sys.excepthook` (or
  IPython's `showtraceback`) first. Any failure while making the tip is swallowed.
- Terminal, REPL, IDLE: one line on stderr, flushed. Dim `pygym:` and a bold command only when
  stderr is a terminal and `NO_COLOR` is not set (never in IDLE). ASCII text; a console that can
  not encode a character gets `?` instead of a crash.
- Notebook: `text/plain` + `text/html` (the command in `<code>`), no colours or background of its
  own, so it follows light and dark themes.
- The first tip of a process ends with `(turn off: pygym --tips off)`.
- One tip per error, also when two hooks see it (global hook + `import pygym`).

### IPython details

`run_code()` in IPython 9.18 (`IPython/core/interactiveshell.py`) calls
`self.CustomTB(etype, value, tb)` for the exceptions in `custom_exceptions` and ignores what it
returns. pygym's handler therefore calls `shell.showtraceback(...)` itself, exactly like the
default branch (`self.showtraceback(running_compiled_code=True)`): the same text, `sys.last_*`
set by `_get_exc_info`, ipykernel's error message and `_last_traceback` for the execute reply,
`%pdb`. `result.error_in_exec` is set by `run_code` before the handler runs, so `%tb`, `%debug`
and `ip.last_execution_result` behave as without pygym. pygym registers for `Exception` (not
`KeyboardInterrupt` or `SystemExit`). A handler that was installed before pygym keeps receiving
the exception types it asked for; `pygym.tips(False)` puts it back exactly.

## Which functions

- Always: built-ins that have hand-written exercises (the dataset, through `pygym.loader`).
- In AI mode only (the saved `mode` is `ai`): library functions called on the line, such as
  `statistics.median(...)`, `st.median(...)` after `import statistics as st`, or `median(...)`
  after `from statistics import median`. The name is resolved in the failing frame. It must be a
  target that AI mode accepts (`pygym.ai.targets.quick_check`: the same rules as `resolve()`,
  without the sandbox probe; libraries about files or the network are never suggested, and only
  libraries already imported in that process count). The tip says "AI exercise (local model)?".
- Never: methods (`df.mean()`, `"".join()`), the user's own functions and modules, a built-in
  name the user has shadowed (`sum = 0`, `def max(...)`, a local variable).

### The failing line and the ranking

1. The last traceback frame that is user code (not the standard library, not site-packages /
   dist-packages, not pygym, not `<frozen ...>`). An error deep inside `statistics` that was
   called from the student's line gives that line.
2. The source is parsed with `ast`. Calls whose span covers the failing line count; for
   `for` / `if` / `while` / `with` / `def` / `class` only the header, not the body. A multi-line
   call counts on any of its lines. If the file does not parse, the tokenizer reads that one line.
3. Ranking: (a) Python 3.11+: the call whose span contains the failing instruction, innermost
   first; (b) functions named in the error message (`zip() argument 2 is shorter ...`, `max()
   iterable argument is empty`, `object of type 'int' has no len()`); (c) left to right.
   At most 3 names; the command is for the first.

Skipped: `SyntaxError` / `IndentationError`, `KeyboardInterrupt`, `SystemExit`,
`GeneratorExit`, errors with no user frame, and pygym's own `ExerciseUnavailable`.

## The global hook (`pygym --tips on`)

Two files, both removed by `pygym --tips off`:

- `pygym_tips.pth` in this interpreter's site-packages (`sysconfig` purelib). When that is not
  writable: the user site-packages (outside a virtual environment only; Python does not read the
  user site in a venv). It holds one line that installs a tiny `sys.excepthook` wrapper at
  start-up. It does **not** import pygym: pygym is imported only when an exception reaches the
  hook, and if pygym is gone (uninstalled) the previous hook runs as if nothing was there.
  Cost at start-up, measured on Linux with Python 3.14 (20 runs each, median): 26.1 ms without
  the `.pth`, 26.8 ms with it. The first tip in a process that had not imported pygym costs
  about 70 ms (mostly importing pygym), later tips about 15 ms.
- `<ipython dir>/profile_default/startup/50-pygym-tips.py` (`$IPYTHONDIR`, else `~/.ipython`),
  only when IPython is installed. Silent and harmless when pygym is not installed in that
  kernel's Python; it adds nothing to the user namespace.

`PYGYM_TIPS_SITE_DIR` puts the `.pth` somewhere else (the tests use it; Python only reads it from
a site directory). pygym's sandbox processes get `PYGYM_NO_TIPS=1`, so an exercise run never
shows a tip, with or without the global hook.

## Limits

- Classic REPL before Python 3.13 without readline (some Windows builds): no source, no tip.
- `python -c "..."` and `exec("...")` strings (`<string>`): no tip.
- Errors in tkinter callbacks (tkinter prints them itself, not through `sys.excepthook`) and in
  threads (`threading.excepthook`): no tip.
- IPython's `%run script.py`: `%run` shows the traceback itself, so no tip there.
- A program that installs its own `sys.excepthook` after pygym and does not call the previous one
  hides the tip (pygym calls the hook that was there before it, never the reverse).
- Tested by hand and in the test suite on Linux only (Python 3.14; the analysis also on 3.10).
  Not tested: Windows, macOS, Colab, the PyCharm console, Spyder. `tools/real_machine_check.py`
  (section 6) and `tools/notebook_check.ipynb` (cells 6a / 6b) check it there.
