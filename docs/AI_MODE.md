# AI mode

pygym has two modes:

- **preset** (default): exercises written and checked by people. Works offline.
- **ai**: a small language model (LLM) on your own computer writes a new exercise each time,
  for almost any function: built-ins (`zip`), methods (`str.join`) and functions from some
  standard modules (`statistics.median`, `itertools.chain`).

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
2. It asks the model for one exercise as JSON: a question, a function name, a reference
   solution, 4 to 6 test inputs and a hint. A random story ("a weather station", "a quiz
   game"...) and focus make each exercise different.
3. It checks the answer **before you see it**:
   - the JSON has all fields, the reference solution parses and defines the function;
   - the reference really uses the target function and only allowed imports;
   - the reference runs in the sandbox (a separate Python process with a time limit) on every
     test input. Test inputs that crash are dropped; at least 3 must be left;
   - the **expected results come from running the reference solution**, never from the model;
   - the results are not all the same, and they are the same when run a second time;
   - the reference passes the normal grader, like a student solution would.
4. If a check fails, pygym tells the model what was wrong and asks again (up to 4 times in
   about 20 seconds).
5. Good exercises are saved in `~/.pygym/generated/<function>.json`. When the model is slow,
   wrong or not running, pygym reuses a saved one you have not seen yet.
6. If nothing works, pygym uses a preset exercise and tells you why (for example
   "AI mode unavailable (Ollama is not running at http://localhost:11434)").

The model's code only ever runs inside the sandbox, never inside pygym itself.

## Settings

Settings live in `~/.pygym/config.json` (or `$PYGYM_HOME/config.json`):

| Field | Default | Meaning |
|---|---|---|
| `mode` | `"preset"` | `"preset"` or `"ai"` |
| `provider` | `"ollama"` | `"ollama"` or `"openai"` (any OpenAI-compatible server) |
| `model` | `"qwen2.5:1.5b"` | the model name for that provider |
| `base_url` | `"http://localhost:11434"` | where the server is |

Environment variables:

- `PYGYM_NO_AI=1` turns AI mode off completely (no network at all). Useful in tests and
  offline classes.
- `PYGYM_API_KEY` is the API key for the `openai` provider. It is **never** saved in
  `config.json`.

### Other servers (LM Studio, llama.cpp, vLLM, hosted APIs)

Set `provider` to `"openai"` and `base_url` to the server's address **including `/v1`**, for
example `"http://localhost:1234/v1"` for LM Studio. The server must support JSON-schema
structured output (`response_format` of type `json_schema`).

## Privacy

- With Ollama (the default) everything stays on your computer.
- With the `openai` provider, the **prompt** is sent to that server: the function's name, its
  documentation and short descriptions of earlier exercises for that function. **Your code is
  never sent.** If `base_url` points to a cloud service, that service sees the prompt.
- Your progress, saved exercises and the last 30 rejected model replies
  (`~/.pygym/generated/rejected.jsonl`, to help improve the prompt) stay in `~/.pygym`.

## Which functions work

- Built-ins, except ones that make bad exercises (`print`, `input`, `open`, `eval`, `exec`,
  `id`, `hash`, exception classes...).
- Methods of `str`, `list`, `dict`, `set`, `tuple`, `int`, `float`, `bytes` (e.g. `str.join`).
- Functions and classes from these modules: `array bisect calendar cmath collections copy
  datetime decimal difflib fractions functools heapq itertools json keyword math operator re
  statistics string textwrap unicodedata`.

Not allowed: modules that touch files, processes, the network or Python itself (`os`, `sys`,
`subprocess`, `pathlib`...), and `random`/`time` (their results change between runs).

## Limits (read these)

- **Small models are often wrong.** Many replies are rejected, so you may wait up to about
  20 seconds and then get a preset exercise. In our first tests most replies from 1.5B-3B
  models were rejected.
- **A checked exercise can still be misleading.** The checks prove the reference solution runs
  and uses the function. They do not prove that the question text describes what the code
  does. The tests come from the code, so if the question and the code disagree, trust the
  examples and use `:solution` to see the code.
- **Levels are a wish.** Small models mostly ignore beginner / intermediate / advanced.
- **Slow without a GPU.** On a laptop without a graphics card, one model call can take longer
  than the whole 20-second budget.
- **"Did you really call it?" checks.** For built-ins and module functions, pygym checks while
  your code runs that the function was really called (code like `if False: zip(a, b)` fails).
  For methods of built-in types (`str.join`, `list.sort`) Python does not allow that check, so
  pygym only checks that your code mentions the method. While a module class such as
  `collections.Counter` is being watched, `isinstance(x, collections.Counter)` does not work.
- **The sandbox is not a security boundary.** It stops mistakes and simple cheating, not a
  determined attacker. Only use models and servers you trust.

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
GPU (shared with other programs). Results swing a lot between runs, so a few dozen requests per
model are needed before choosing a default. Generated exercises are cached in
`PYGYM_HOME/generated/`: when a later request for the same function fails, the cached one is
reused, so repeat requests succeed more often than the first one.
