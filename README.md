# pygym

Learn a Python built-in without leaving your code.

```python
from pygym import exercise
exercise("zip")
```

A popup (or a text prompt in a terminal) gives you a small coding challenge about `zip`.
Write the function, press Submit, ask for a hint, or skip to a new one.

> Status: pilot (v0.0.x). Covers a small set of built-ins. Exercises are pre-generated and work fully offline.

## Development

```bash
pip install -e .[dev]
pytest
python tools/validate_exercises.py          # check every exercise's reference solution
python tools/validate_exercises.py --fill   # (re)compute expected_outputs from reference solutions
python tools/validate_exercises.py --levels # list exercises whose reference differs from the level rules
```

## Layout

| File | Job |
|---|---|
| `src/pygym/models.py` | `Exercise`, `GradeResult` (the shared contract) |
| `src/pygym/loader.py` | load / pick exercises from `data/exercises.json` |
| `src/pygym/grader.py` + `_runner.py` | safe checking engine |
| `src/pygym/session.py` | one exercise attempt: submit / hint / solution / skip |
| `src/pygym/ui.py` | text UI and tkinter popup |
| `src/pygym/progress.py` | local progress file |
| `src/pygym/data/exercises.json` | the exercise dataset |

## Releasing (for maintainers)

1. Update `__version__` in `src/pygym/__init__.py` and add an entry to `CHANGELOG.md`.
2. Commit and push to `main`.
3. On GitHub: **Releases -> Draft a new release**, create a tag matching the version (e.g. `v0.0.1`), click **Publish release**.
4. The `Publish to PyPI` workflow runs the tests, builds, and uploads automatically.

One-time setup: on PyPI add a *Trusted Publisher* (workflow `publish.yml`, environment `pypi`),
and create an environment named `pypi` in the GitHub repo under Settings -> Environments.
