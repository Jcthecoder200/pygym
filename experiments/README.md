# experiments/

Throwaway trials. Nothing here is part of the installed package, and code here may use
libraries that pygym itself does not depend on.

| File | What it tries | Run (from repo root) |
|---|---|---|
| `rich_preview.py` | How the command-line screens could look with Rich (intro, exercise, fail + reveal prompt, hint, `:help`, pass). Static mock-ups, no grading. | `uv run --with rich python experiments/rich_preview.py` (add `--svg` to save images to `experiments/out/`) |
| `tk_handle_sketch.py` | A tkinter popup built only on `Session.handle()`, as a starting point for the real `run_tk`. See `docs/FRONTEND_GUIDE.md`. | `PYTHONPATH=src python experiments/tk_handle_sketch.py` |
| `keys_demo.py` | Raw terminal mode: shows the bytes each key sends (arrows = escape sequences). Linux/macOS only. | `python experiments/keys_demo.py` (q quits) |

Without uv: `pip install rich` in a virtual environment, then `python experiments/rich_preview.py`.
