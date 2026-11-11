# experiments/

Throwaway trials. Nothing here is part of the installed package, and code here may use
libraries that pygym itself does not depend on.

| File | What it tries | Run (from repo root) |
|---|---|---|
| `rich_preview.py` | How the command-line screens could look with Rich (intro, exercise, fail + reveal prompt, hint, `:help`, pass). Static mock-ups, no grading. | `uv run --with rich python experiments/rich_preview.py` (add `--svg` to save images to `experiments/out/`) |
| `tk_handle_sketch.py` | A tkinter popup built only on `Session.handle()`, as a starting point for the real `run_tk`. See `docs/FRONTEND_GUIDE.md`. | `PYTHONPATH=src python experiments/tk_handle_sketch.py` |
| `keys_demo.py` | Raw terminal mode: shows the bytes each key sends (arrows = escape sequences). Linux/macOS only. | `python experiments/keys_demo.py` (q quits) |
| `llm_probe.py` | Can a small local model (Ollama) write one valid exercise? Asks for one exercise per model and target, then checks it in the sandbox (JSON, fields, uses the target, runs). Each run makes real model calls (default 9). Results: `docs/design/AI_MODE_AND_MENU.md` ("Model choice"). | `python experiments/llm_probe.py --models qwen2.5:1.5b --targets zip:2` (needs Ollama on localhost:11434) |
| `prompt_eval.py` | The exercise prompt with a real model: old prompt (a git revision) vs the current one, on a grid of targets × levels, under a hard call budget (`--max-calls`, counted in a JSON file). Variants: `--plan`, `--self-review`, `--model`. Results: `docs/PROMPT_DESIGN.md`. | `python experiments/prompt_eval.py --usecases DIR --counter calls.json --max-calls 40 --tries 2 --out new.jsonl` (needs Ollama) |

Without uv: `pip install rich` in a virtual environment, then `python experiments/rich_preview.py`.
