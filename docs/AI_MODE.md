# AI mode

pygym has two modes:

- **preset** (default): exercises written and checked by people. Works offline.
- **ai**: a small language model (LLM) on your own computer writes a new exercise each time,
  for almost any function: built-ins (`zip`), methods (`str.join`), standard modules
  (`statistics.median`, `itertools.chain`) and **any library you have installed**
  (`numpy.cumsum`, `pandas.concat`, `requests.get`).

Even in preset mode, a function with no preset exercise gets an AI exercise, if AI mode is
installed.

Every AI exercise shows this warning:

> AI-generated exercise: it may be incorrect. Use it carefully.

## Install Ollama and a model

AI mode talks to [Ollama](https://ollama.com), a free program that runs models locally.

1. Install Ollama from <https://ollama.com/download> and start it (on Linux: `ollama serve`).
2. Download the default model (about 1 GB):

   ```
   ollama pull qwen2.5:1.5b
   ```

3. Check that pygym can see it:

   ```
   pygym --status
   ```

If you have a good GPU, a bigger model writes better code but is slower:

```
ollama pull qwen2.5:3b
pygym --set-model qwen2.5:3b
```

## Use it

```
pygym zip --level 2 --mode ai          # one AI exercise
pygym --set-mode ai                     # make AI mode the default
```

In Python: `exercise("statistics.median", 1, mode="ai")`. In the menu, press `m` to switch
modes.

## How it works

1. pygym reads the function's documentation and signature (for example `statistics.median`).
2. **Use cases first.** The first time a function is used, pygym asks the model for 6 to 8 short
   real-world situations where a programmer would naturally reach for it (for `numpy.cumsum`:
   "a bank account balance after each transaction", "rainfall so far this month"...). They are
   checked (non-empty text, at most 120 characters, no duplicates, at least 3 left) and saved in
   `~/.pygym/generated/usecases/<function>.json`. This call gets its own small slice of the
   time budget (at most 8 s and at most 40 % of it); the progress bar says
   `finding real-world uses`. If it fails, this exercise is written without a given situation
   ("a realistic everyday task where `<function>` is the natural tool") and the next exercise
   asks again.
3. It asks the model for one exercise as JSON: a question, a function name, a reference
   solution, 4 to 6 test inputs and a hint. The exercise is set in **one of the use cases**,
   taking turns (one never used yet, else the one used longest ago). The prompt says the
   situation must be one where the function is the natural tool, and the question must explain
   the inputs and the output and **state every rule the solution uses (no hidden rules)**.
   Why: the old prompt picked one of 30 fixed themes at random, with no link to the function.
   For `numpy.cumsum` the model wrote a "best plant watering schedule" exercise whose solution
   counted 'morning', 'afternoon' and 'evening', rules the question never mentioned. A use case
   teaches which problems the function solves. The old "focus" list (data shapes such as
   "working with strings" or "a list of pairs") was removed too: it often fought the situation
   and the function's natural input. Variety now comes from the use cases, depth from the level.
4. It checks the answer **before you see it**:
   - the JSON has all fields, the reference solution parses and defines the function;
   - the reference really uses the target function and only allowed imports;
   - the reference **fits the level** (see [What each level means](#what-each-level-means)): a
     level-3 one-liner is rejected and the model is told what was missing;
   - the reference runs in the sandbox (see [The sandbox](#the-sandbox)) on every test input.
     Test inputs that crash are dropped; at least 3 must be left. If the code tries something
     the sandbox blocks (for example deleting a file outside its folder), it is rejected and
     the model is told why;
   - the **expected results come from running the reference solution**, never from the model;
   - the results are not all the same, and they are the same when run a second time;
   - the reference passes the normal grader, like a student solution would.
5. If a check fails, pygym tells the model what was wrong and asks again, until a reply passes or
   the time budget is used (60 seconds by default, setting `ai_timeout`; at most 10 tries). A fresh try (not a repair) takes the next use case.
6. Good exercises are saved in `~/.pygym/generated/<function>.json`. When the model is slow,
   wrong or not running, pygym reuses a saved one you have not seen yet.
7. If nothing works, pygym uses a preset exercise and tells you why (for example
   "AI mode unavailable (Ollama is not running at http://localhost:11434)").

The model's code only ever runs inside the sandbox, never inside pygym itself.

### What each level means

The level is a rule about the **reference solution's code**, checked on its syntax tree before it
runs (`pygym/ai/levels.py`, one place for the prompt, the check and this page). Only the
student's function counts, plus helper functions it defines and calls; imports and other
top-level code do not. "Lines" are logical lines (statements: a `for` line, an `if` line, an
assignment, a `return`...; the `def` line and a docstring are not counted).

| Level | The reference solution must | Example idea in the prompt |
|---|---|---|
| 1 beginner | be **one line** (anything fits in that line, e.g. `return [x * x for x in nums]` or `return 'pos' if n > 0 else 'neg'`), or a few straight-line lines with **no** `if` statement, **no** `x if c else y`, **no** `for`/`while`, **no** comprehension or generator expression (they are loops), **no** `match`. `and` / `or` inside an expression are allowed: they combine values, they are not a branch the student writes. | apply the function to the input, with a little arithmetic or text |
| 2 intermediate | have **a loop** (`for`/`while` or a comprehension) **and a condition** (an `if` statement, `x if c else y`, or an `if` inside the comprehension), in at most 20 lines | go through a list and keep, count or change the items that meet a condition |
| 3 advanced | have a loop and a condition **and** use data structures or algorithms: **2 different markers**, or **1 marker in 7+ lines** | group records into a dict and sort the groups with `key=`, or walk the input with a stack |

The markers: a dict or set the code builds or updates (a literal, `dict()`, `set()`, a `{...}`
comprehension, `d[k] = ...`, `.add`, `.setdefault`), `collections` (`Counter`, `defaultdict`,
`deque`...), `heapq`, `bisect`, a stack or queue (`.append` and `.pop`), nested loops, recursion
(the function calls itself), sorting with `key=` (`sorted(..., key=)`, `.sort(key=)`).

Why 7 lines with one marker: a recursive sum over nested lists (the hand-written
`isinstance-03`) or grouping items into a dict by a key are real algorithms in 7 lines; a
6-line counter of even and odd numbers in a dict is not.

The prompt states the rule (from `levels.describe`) with one example idea, and says the reference
must follow it. A reply that breaks it is sent back to the model with what was missing, e.g.
"too simple for advanced: ... it has no loop; no condition; no data structure or algorithm. Make
the task bigger, with the target as one step". The progress line says
`try 1 rejected: too simple for the level`. Saved exercises are reused only if they fit their
level too (older versions saved level-3 one-liners).

Hand-written exercises are reviewed by people and are not checked against these rules.
`python tools/validate_exercises.py --levels` lists the ones that differ (see
[EXERCISE_REVIEW.md](EXERCISE_REVIEW.md)).

### Functions too small for advanced: the bigger-task question

Some functions can't make an advanced exercise by themselves: `bin(2)` is just `bin(2)`. pygym
calls a function **simple** when its signature has at most one input (a method's own value
counts: `str.upper` has one, `str.join` two) and nothing optional (no default value, no
`*args`, no `[optional]` part), and its documentation shows no other way to call it with more
arguments (`range(start, stop[, step])`). Simple: `bin`, `hex`, `oct`, `abs`, `len`, `chr`,
`ord`, `str.upper`, `any`, `reversed`, `math.sqrt`, `statistics.median`. Not simple: `sorted`
(`key`, `reverse`), `max` (`key`, `default`), `round` (`ndigits`), `numpy.cumsum` (`axis`,
`dtype`), `str.join`, `zip` (many iterables, `strict`), `range`. A function whose signature
Python can't read counts as not simple (`targets.TargetInfo.simple`).

When you ask for **advanced** with a simple function (menu level 3, `pygym bin --level 3`,
`:level 3`, or the automatic level-up to 3), pygym asks first:

```
`bin` is too small for an advanced exercise on its own.
Practise it inside a bigger task (bin + loops, conditions and data structures)? [Y/n]
```

- **Yes** (Enter, `y`): advanced exercises are bigger tasks. The prompt says the function is one
  necessary step of a larger realistic algorithmic task; the exercise must still call it and
  still follows the advanced rule.
- **No** (`n`): you stay at intermediate (from beginner, you get an intermediate exercise).
- No answer (end of input, `Ctrl-C`, `Esc`, another word): nothing changes; `:level 3` asks again.

The answer is remembered for the rest of the session: `:s` and later level-ups don't ask again.
`:bigger yes` / `:bigger no` changes it later. Until you answer, the level stays below advanced,
so neither `:s` nor the background prefetch writes an advanced exercise for such a function.
This is about generated exercises only: a function with hand-written exercises in preset mode
uses them as they are, at every level (`abs` in preset mode has no question). In preset mode, a
simple function without hand-written exercises is generated, so it gets the question too.

### The next exercise is written while you work (prefetch)

As soon as an exercise is shown, pygym starts writing the next one in the background, at the
level `:s` would use. This happens in AI mode, and in preset mode for functions without
hand-written exercises (those are generated too). By the time you type `:s` it is usually ready,
and `:s` is instant. If it is still being written, `:s` shows the progress bar of that same
request (no second request is sent). `Ctrl-C` stops waiting, but the background request goes on
and the next `:s` picks it up. If it failed, you get the usual fallback (a saved or hand-written
exercise).

- Only one is written at a time. A level change (`:level 2`, or the automatic level-up after 3
  solved exercises) throws away the one written for the old level and starts a new one. The new
  request waits until the thrown-away one has finished, so pygym never has two requests at the
  model at once.
- It is not added to "seen" until it is shown. It never slows your own `:w` checks: they run in
  the sandbox, not the model, and nothing makes them wait for each other.
- Quitting (`:q`, `:q!`, `:wq`, `Ctrl-C`, end of input) cancels it. A model request that was
  already sent may finish in the background; its answer is thrown away. It never keeps pygym
  running.
- Turn it off with `"prefetch": false` in `config.json` (`pygym --status` shows the setting).
  With it off, `:s` waits for the model as before (5-30 s).

### Thinking models (qwen3, qwen3.5, deepseek-r1...)

Some models "think" before they answer: they write a long hidden reasoning first. With a short time
budget they spent the whole time thinking: qwen3.5:2b timed out. pygym now asks Ollama once per
model and per run (`/api/show`, its `capabilities`) and sends `"think": false` to models that
list `thinking`. Measured: qwen3.5:2b then answers in about 4.5 s with no thinking text. Other
models get no `think` field. Ollama 0.18.1 also accepts `"think": false` for a non-thinking
model (tried with qwen2.5:1.5b), but an older server may not. If a server refuses the field
(HTTP 400 about `think`), the request is sent once more without it. The `openai` provider never
sends a `think` field.

## Settings

Settings live in `~/.pygym/config.json` (or `$PYGYM_HOME/config.json`):

| Field | Default | Meaning |
|---|---|---|
| `mode` | `"preset"` | `"preset"` or `"ai"` |
| `provider` | `"ollama"` | `"ollama"` or `"openai"` (any OpenAI-compatible server) |
| `model` | `"qwen2.5:1.5b"` | the model name for that provider |
| `base_url` | `"http://localhost:11434"` | where the server is |
| `prefetch` | `true` | write the next AI exercise in the background while you work |
| `ai_timeout` | `60` | seconds the AI may spend on one exercise (10-300); it keeps asking until a reply passes the checks or this time is used |

Environment variables:

- `PYGYM_NO_AI=1` turns AI mode off completely (no network at all). Useful in tests and
  offline classes.
- `PYGYM_API_KEY` is the API key for the `openai` provider. It is **never** saved in
  `config.json` and never passed to the sandbox.
- `PYGYM_SANDBOX=audit-hook` switches off the operating-system layer of the sandbox (see
  [The sandbox](#the-sandbox)).

### Other servers (LM Studio, llama.cpp, vLLM, hosted APIs)

Set `provider` to `"openai"` and `base_url` to the server's address **including `/v1`**, for
example `"http://localhost:1234/v1"` for LM Studio. The server must support JSON-schema
structured output (`response_format` of type `json_schema`).

## Privacy

- With Ollama (the default) everything stays on your computer.
- With the `openai` provider, the **prompt** is sent to that server: the function's name, its
  documentation and short descriptions of earlier exercises for that function. **Your code is
  never sent.** If `base_url` points to a cloud service, that service sees the prompt.
- Your progress, saved exercises, the saved use cases and the last 30 rejected model replies
  (`~/.pygym/generated/rejected.jsonl`, to help improve the prompt) stay in `~/.pygym`.

## Which functions work

- Built-ins, except ones that make bad exercises (`print`, `input`, `open`, `eval`, `exec`,
  `id`, `hash`, exception classes...).
- Methods of `str`, `list`, `dict`, `set`, `tuple`, `int`, `float`, `bytes` (e.g. `str.join`).
- Functions and classes of **any library that is installed** in the Python that runs pygym
  and that imports inside the sandbox: standard modules (`statistics`, `csv`, `datetime`...)
  and libraries you installed (`numpy`, `pandas`, `requests`...). Methods of a library's
  classes (`numpy.ndarray.cumsum`) are not targets; use the function (`numpy.cumsum`).
- Libraries about **files or the network** (`os`, `pathlib`, `shutil`, `glob`, `zipfile`,
  `urllib`, `http`, `socket`, `requests`, `httpx`...) only work when your computer has an
  operating-system sandbox (Linux with bubblewrap, macOS). On Windows pygym refuses them,
  because there only the basic protection is available (see [The sandbox](#the-sandbox)).

**Never allowed**, with the reason pygym shows:

| Libraries | Why |
|---|---|
| `tkinter turtle idlelib curses readline` and GUI toolkits (`PyQt5/6`, `PySide2/6`, `wx`, `kivy`, `pygame.display`, `pyautogui`) | they need a screen, sound or the keyboard |
| `subprocess multiprocessing signal pty` and `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `os.fork`, `os.kill`... | they start or control other programs |
| `ctypes` | it can change memory directly |
| `sys builtins importlib code pdb resource atexit` | they control the running Python itself |
| `random secrets time` | their results change on every run, so pygym can't know the right answer |
| `pip setuptools venv ensurepip site` | they install or change Python packages |
| `webbrowser antigravity this`, `pygym` | they open a browser / print something / are pygym itself |

The list is in one place (`DENIED_MODULES` in `src/pygym/_runner.py`) and the menu, the AI
engine and the sandbox all use it.

**Not installed?** If you type a library that is not installed, the menu says so. For
well-known libraries it also tells you the exact command, for example:

```
'numpy' is not installed in this Python. Install it with: /home/you/.venv/bin/python -m pip install numpy
```

pygym never installs anything by itself.

**Test values are JSON.** Test inputs are always lists, dicts, numbers, strings, `true/false`
and `null`. If the function needs something else (a numpy array, a DataFrame, a date), the
exercise's function converts its input itself, e.g. `np.array(values)`. Results are converted
the other way before they are compared:

| Your function returns | It is compared as |
|---|---|
| a tuple | a list |
| a set | a sorted list |
| numpy numbers / arrays | Python numbers / (nested) lists (`.tolist()`) |
| a pandas Series / DataFrame | a dict (`.to_dict()`, keys as text) |
| a dataclass | a dict of its fields |
| `Decimal`, `Fraction`, `complex`, dates... | their `repr` text, e.g. `"Decimal('1.50')"` (exact, so `1.5` and `1.50` differ) |
| anything else | its `repr`, with the type name in front if the repr doesn't show it |

`NaN` counts as equal to `NaN`; other floats may differ by at most `1e-9`.

**Imports.** Your code may import `math itertools functools collections operator string`,
the target's library, and any other library the reference solution imported (for example
`numpy` in a `pandas` exercise). `import numpy as np` is fine. A file or network library can
only be imported in an exercise about that library.

## The sandbox

Exercise code is never run inside pygym itself. That matters most for AI exercises: the
model's reference solution is code nobody has read, and a small model can write
`shutil.rmtree(...)` or ask a real web server for something by accident. Your own code runs the
same way (it is your code, but it should not depend on your files either).

**Every run, on every computer:**

- **A new, empty folder.** pygym makes a fresh temporary folder, runs the code there and
  deletes it afterwards. It never runs in the folder you started pygym from: copying that
  folder could be huge (it is often your whole home folder), and an exercise must not depend
  on what happens to be in it. An exercise that needs files brings them along (small text
  files, at most 64 KB together); they are written into the folder again before every test.
- **A local test server instead of the internet.** An exercise about `requests` or `urllib`
  brings the answers its server should give (for example `/users/1` →
  `{"name": "Ana"}`). pygym starts a tiny web server on your own computer (`127.0.0.1`,
  a random port) only while the code runs, and the code finds it in the variable `BASE_URL`.
  Any other address is blocked.
- **A separate Python process** with a memory limit (512 MB), a file-size limit (16 MB), a
  time limit and no secret environment variables (no API keys).
- **A guard inside Python** (an "audit hook") that stops: writing, deleting, renaming or
  changing files outside the folder; starting programs (`subprocess`, `os.system`); `ctypes`;
  opening a browser; and network connections other than `BASE_URL`. You see a message like
  `PermissionError: blocked: writing to '/home/you/x.txt' (only the exercise folder can be changed)`.

**On top of that, if your computer has it:**

| Computer | Extra protection | Shown as |
|---|---|---|
| Linux with `bwrap` (bubblewrap) installed | The whole system is read-only, your home folder is replaced by an empty one (only Python's own folders are visible), there is no network except inside the sandbox, and the code can't see your other programs. | `Sandbox: bubblewrap` |
| macOS | A `sandbox-exec` profile: no writing outside the folder, no network except `localhost`, no other programs. **Untested**: written carefully, used only if a quick self-test passes. | `Sandbox: macOS sandbox-exec` |
| Windows (and Linux without bubblewrap) | Only the guard inside Python. File and network libraries are refused. | `Sandbox: basic (Python audit hook only)` |

On Linux, install bubblewrap with your package manager (`sudo apt install bubblewrap`,
`sudo dnf install bubblewrap`). Some systems (some containers, some hardened kernels) forbid
it; pygym then notices and uses the basic level.

**See your level:** the menu header has a `Sandbox` line, and `pygym --status` shows it.
To switch the extra layer off (for example if bubblewrap misbehaves), set
`PYGYM_SANDBOX=audit-hook`. You can't force a layer your computer doesn't have.

**What is NOT protected:**

- **The guard inside Python is not a security wall.** Code written in C (a C extension, or
  `ctypes` tricks) can go around it. It stops accidents, not someone who wants to break out.
  On Windows it is the only layer, so use models and servers you trust.
- **Reading is allowed.** Without bubblewrap, code can *read* files your user can read (it
  just can't change them or send them anywhere over the network). With bubblewrap your home
  folder is hidden.
- **Computer power.** The code can use the CPU until the time limit stops it.
- **The model server.** pygym talks to Ollama (or the server in your settings) from pygym
  itself, not from the sandbox.

## Limits (read these)

- **Small models are often wrong.** Many replies are rejected, so you may wait up to about
  20 seconds and then get a preset exercise. In our first tests most replies from 1.5B-3B
  models were rejected.
- **A checked exercise can still be misleading.** The checks prove the reference solution runs
  and uses the function. They do not prove that the question text describes what the code
  does. The tests come from the code, so if the question and the code disagree, trust the
  examples and use `:solution` to see the code.
- **Levels are checked on the code, not on the difficulty of the idea.** A reference with a loop,
  a condition and a dict passes as advanced even when the task is easy to think through, and a
  clever one-liner never does. Small models often miss the rule on the first try; the repair
  message usually fixes it, but it costs a model call (see the measurements below).
- **Slow without a GPU.** On a laptop without a graphics card, one model call can take longer
  than the whole 20-second budget.
- **"Did you really call it?" checks.** For built-ins and module functions, pygym checks while
  your code runs that the function was really called (code like `if False: zip(a, b)` fails).
  For methods of built-in types (`str.join`, `list.sort`) Python does not allow that check, so
  pygym only checks that your code mentions the method. While a module class such as
  `collections.Counter` is being watched, `isinstance(x, collections.Counter)` does not work.
  For library functions, the method form is accepted too: in a `numpy.cumsum` exercise,
  `np.array(xs).cumsum()` passes with the mention check only (numpy's own methods can't be
  watched). numpy "ufuncs" (`numpy.sqrt`, `numpy.add`...) can't be watched either, so they get
  the mention check only. Calls a library makes to itself count as calls (pandas builds a
  `Series` inside `value_counts`).
- **The sandbox is not a security boundary on every computer.** See
  [What is NOT protected](#the-sandbox). Only use models and servers you trust.

## Measured on the development laptop

Laptop GPU: RTX 4050 (6 GB, shared with other programs), Ollama, 2026-11-11. Only 8 model calls
were allowed for this test, so the numbers are a first look, not a benchmark.

**1. The real command.** `python -m pygym zip --level 2 --mode ai --ui text`, typing a wrong
answer, then `:h`, `:solution`, `:q!` (default model qwen2.5:1.5b, 20 s budget):

- 2 model calls fit in the budget (about 9 s each). Both replies were rejected (a syntax error,
  then "all test cases give the same result").
- After 18.2 s pygym showed the preset exercise `zip-02` with the yellow line
  `! AI mode unavailable (no valid exercise after 2 tries (...)); using the preset exercises.`
- The wrong answer failed, `:h` showed the hint, `:solution` the code, `:q!` quit. Exit code 0.

**2. Generation for four targets.** `ai.generate_exercise(target, level, timeout=20)` with
a new, empty cache. Because of the call limit each target got at most 2 attempts (normally up to 4).

| Target | Level | Model | Calls | Seconds per call | Total | Valid? | Why it was rejected |
|---|---|---|---|---|---|---|---|
| `statistics.median` | 1 | qwen2.5:1.5b | 2 | 5.4, 5.3 | 10.7 s | no | the code crashed (`"apple,1.5".split()[1]`: IndexError), twice |
| `str.join` | 2 | qwen2.5:1.5b | 2 | 4.6, 4.5 | 9.1 s | no | the code never calls `.join` (the second reply was the same as the first) |
| `itertools.chain` | 3 | qwen2.5:3b | 1 | 9.3 | 9.3 s | no | the code crashed on its own tests (TypeError) |
| `enumerate` | 3 | qwen2.5:3b | 1 | 7.0 | 7.0 s | no | the code crashed on its own tests (TypeError) |

So: **0 of 5 real requests gave a usable exercise** (8 calls). Each time the checks caught
the broken reply, and pygym fell back to a preset (or reported "no exercise" for a function
without one).

**3. Saved real replies, checked again offline** (no model calls): the 12 replies from the
first probe plus the 8 above, run through the current checks: **2 of 20 are usable**.
For both, `grader.grade(reference_solution)` passes, and two wrong answers fail
(`return None` → "This exercise is about 'str.join'. Try using it."; the right result wrapped
in a list → "Not quite right yet."). One of them (qwen2.5:1.5b, `str.join`):

> Write a function that takes any number of strings and concatenates them using `str.join`,
> including an optional separator.
>
> `def join_strings(*strings, sep=' '): return sep.join(strings)`. Tests:
> `["Hello", "World"]` → `"Hello World"`, `["apple", "banana", "cherry"]`, `[]` → `""`, `["a"]`.

It was asked for level 3 but is really level 1, and the question does not say what the
default separator is. The checks cannot catch either problem.

What this means: with qwen2.5 1.5B/3B on this laptop, AI mode mostly falls back to presets.
The safety part works (no broken exercise reached the student). The useful part (a new, good
exercise) is rare. Before you rely on it, try a bigger model, run more calls, or fill the
cache ahead of time.

**4. A second, larger run** (after the build, fresh `PYGYM_HOME` per batch, full retry budget,
`ai.generate_exercise` directly; a "valid" result was then graded with its own reference and passed):

| Model | Budget | Targets (level) | Valid | Time per request |
|---|---|---|---|---|
| qwen2.5:3b | 30 s | zip (2), statistics.median (1), str.join (2), enumerate (3) | 3 of 4 | 5.7–29.5 s |
| qwen2.5:3b | 20 s | map (2), itertools.chain (1), max (3), str.split (1), sorted (3) | 2 of 5 | 8.3–20 s |
| qwen2.5:1.5b | 20 s | map (2), itertools.chain (1), max (3), str.split (1) | 3 of 4 | 4.7–20 s |

Failures were timeouts or crashing reference code. qwen3.5:2b did not answer within 30 s on this
GPU (shared with other programs) because it spent the time thinking. Now that `"think": false`
is sent, it answers in seconds (see 6). Results swing a lot between runs, so a few dozen requests per
model are needed before choosing a default. Generated exercises are cached in
`PYGYM_HOME/generated/`: when a later request for the same function fails, the cached one is
reused, so repeat requests succeed more often than the first one.

**5. Any library, in the sandbox** (2026-11-11, qwen2.5:3b, Linux with bubblewrap, fresh
`PYGYM_HOME` per target, 10 model calls in total, so each target got only 1-2 attempts):

| Target | Level | Calls | Seconds per call | Valid? | Why it was rejected |
|---|---|---|---|---|---|
| `numpy.cumsum` | 1 | 1 | 6.0 | **yes** | (`np.cumsum` of word lengths; the reference passes the grader) |
| `numpy.argsort` | 2 | 2 | 6.0, 6.4 | no | the code never called `argsort`; then it crashed (TypeError) |
| `requests.get` | 1 | 1 | 12.7 | no | the code set its own `BASE_URL = 'http://127.0.0.1:8000'` (now rejected with that reason) |
| `os.listdir` | 1 | 2 | 5.1, 5.2 | no | no `files`, so every test gave `[]` ("all test cases give the same result") |
| `pandas.concat` | 2 | 2 | 7.8, 8.0 | no | the code crashed on its own tests (ValueError), twice |
| `requests.get` (prompt fixed) | 1 | 1 | 9.5 | no | fixtures and `BASE_URL` right, but the function ignored its input |
| `os.listdir` (prompt fixed) | 1 | 1 | 7.2 | no | used `open()` without listing the file in `files` |

So 1 of 7 requests gave a usable exercise. As before, the checks caught every broken reply,
and no reply touched anything outside its temporary folder. One sandbox run costs about 21 ms
for a plain exercise (a bare `python -c pass` takes 14 ms) and about 150 ms for a numpy one
(numpy's import); bubblewrap adds about 3-5 ms.

**6. Use cases first, prefetch and `think: false`** (2026-11-11, Ollama 0.18.1, qwen2.5:1.5b,
level 1, fresh `PYGYM_HOME`). Only 12 model calls were allowed, so each exercise got **one
attempt** instead of the usual 4.

Use cases: one call per function, 1.8–3.1 s each, all passed the checks:

| Function | Use cases from the model |
|---|---|
| `numpy.cumsum` | calculating total rainfall over days · summing up the hours worked by employees · computing cumulative sales for marketing analysis · adding up scores on a quiz round-by-round · totalizing marks scored across all subjects in an exam · cumulating temperature highs for daily weather forecast |
| `zip` | matching customer orders with products · organizing meeting times for group tasks · concatenating ingredients in a recipe list · combining survey responses by age and gender · grouping email addresses into neighborhoods · sorting documents by author and creation date |
| `statistics.median` | determine the middle temperature for an average daily weather forecast · find the median age of employees at a company · calculate the midpoint price in a sales report · identify the central value in a set of test scores · estimate the typical travel time to school by calculating medians from past records · determine the balance point for average income distribution |
| `str.join` | concatenating names into a full name · joining elements of an array to form a string for a URL parameter · combining words separated by dashes for a product code · merging items with spaces for a file path · stitching together instructions in a recipe book · linking notes using semicolons for a project log |
| `sorted` | organizing student names alphabetically · arranging scores from highest to lowest for individual rankings · sorting temperatures by ascending order from coldest to hottest · displaying the earliest flight times on a train timetable · presenting entries in chronological order of a shopping list · grouping emails received by date descending · organizing books by author's last name and then first name |

For `cumsum`, `median`, `str.join` and `sorted` most items fit. For `zip` only about half do:
the 1.5B model confuses it with sorting and grouping.

One exercise each (both `sorted` exercises come from the line CLI run below):

| Function | Use case | Time | Valid? | Does the question state every rule its reference uses? |
|---|---|---|---|---|
| `numpy.cumsum` | cumulating temperature highs for daily weather forecast | 5.1 s | no: the code crashed (`float('25°C')`) | – |
| `zip` | sorting documents by author and creation date | 7.4 s | no: the code never called `zip` | – |
| `statistics.median` | estimate the typical travel time to school… | 4.5 s | yes | no: it never says the input is a list of `{"name", "time"}` records |
| `str.join` | joining elements of an array … for a URL parameter | 5.4 s | yes | no: the `separator` parameter is never mentioned |
| `sorted` (CLI, first) | presenting entries in chronological order of a shopping list | 3.9 s | yes | yes: "accepts a list of strings … sorts them in alphabetical order" |
| `sorted` (CLI, prefetched) | displaying the earliest flight times on a train timetable | 7.3 s | yes | no: it never says the input is a list of `{"number", "time"}` dicts sorted by `time` |

The stories now fit the function: no more plant watering for `cumsum`. But with a single try, a
1.5B model still often leaves out the shape of the input. In all three weak questions, one
parameter of the reference (`records`, `separator`, `flights`) is not described at all.

**The line CLI with prefetch** (`Session("sorted", 1, mode="ai")` and `ui.run_text`, scripted
input `:s` then `:q!`): the first exercise took 4.1 s. The prefetch started 0.01 s after the
exercise was shown and took 7.3 s. `:s` was sent 20 s after the exercise was shown and **took
0.001 s** (before: 5–30 s). To stay within the call budget, the test switched off the prefetch
that would have started after `:s`.

**`think: false`:** qwen3.5:2b lists `thinking` in its capabilities. Asked for the `zip` use
cases, it answered in 4.5 s (88 tokens, no thinking text). Before this change it timed out
after 30 s.

**7. Level rules and the bigger-task mode** (2026-11-11, Ollama, qwen2.5:1.5b, 20 s budget,
fresh `PYGYM_HOME` with the saved `sorted` and `zip` use cases; `bin` got its use-case call,
3.3 s). At most 12 model calls in total, so the later exercises got only one try.

| Function | Level | Tries | Time | Valid? | Why rejected (in order) |
|---|---|---|---|---|---|
| `bin` (bigger task) | 3 | 2 | 17.7 s | no | imports `os` and reads a file (the use case was "encoding an audio file"); the repair repeated the same reply |
| `sorted` | 3 | 3 of 4 (time) | 17.3 s | no | **too simple** (`return sorted(temps)`); **too simple** (a comprehension and `key=`, no condition); then it dropped `sorted` |
| `zip` | 3 | 2 | 11.1 s | no | never called `zip` (`return defaultdict(list)`), twice |
| `bin` | 1 | 1 | 5.8 s | yes | – : `return bin(number)[2:]`, one line, fits beginner |
| `bin` | 2 | 1 | 6.4 s | no | never called `bin` |
| `sorted` | 1 | 1 | 3.5 s | no | `names.sort()` with no `sorted` and no return |
| `sorted` | 2 | 1 | 7.2 s | no | **too simple** (`return sorted(records, key=...)`: no loop, no condition) |

The level check did its job: the three replies it rejected really were one-liners or nearly
so, where the old prompt would have shipped them as level 2 and 3. But it raises the bar for a
1.5B model: in this run no level-2 or level-3 exercise passed every check, and the repair
message did not get the model to add a loop and a condition within the budget. With this
model, intermediate and advanced exercises mostly come from saved exercises (reused only if
they fit their level) or hand-written ones; a larger model (qwen2.5:3b or more) is the way to
get new ones. The valid `bin` exercise still shows the older limit: its question says the
input is between 0 and 16777215, but one test uses -5 (`'b101'`).

**8. A one-minute budget** (2026-11-11, Ollama, qwen2.5:1.5b, 60 s, fresh `PYGYM_HOME`, the
level rules on):

| Function | Level | Time | Tries | Result | Rejected because |
|---|---|---|---|---|---|
| `bin` | 2 | 59.5 s | 10 | no exercise | did not call `bin` (7), syntax error (3) |
| `sorted` | 2 | 60 s | 7 | no exercise | too simple for the level (6), the last call ran out of time |
| `zip` | 2 | 60 s | 7 | no exercise | did not call `zip` (4), syntax error (2) |
| `sorted` | 3 | 58.6 s | 8 | no exercise | too simple (3), did not call `sorted` (2), not usable (3) |
| `bin` (bigger task) | 3 | 60 s | 10 | no exercise | did not call `bin` (6), too simple (2), not usable (2) |

Three times the tries did not help: the 1.5B model repeats the same mistake after it is
told what was wrong. The minute matters for slower models and for prefetch (which hides it);
it does not make this model write intermediate or advanced exercises.

