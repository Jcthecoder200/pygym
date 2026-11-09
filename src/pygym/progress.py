"""Local-only progress log: one line per attempt in a plain txt file."""
from __future__ import annotations

import os
import time
from pathlib import Path


def _path() -> Path:
    home = Path(os.environ.get("PYGYM_HOME", Path.home() / ".pygym"))
    home.mkdir(parents=True, exist_ok=True)
    return home / "progress.txt"


def record(exercise_id: str, passed: bool) -> None:
    try:
        with _path().open("a", encoding="utf-8") as f:
            f.write(f"{int(time.time())},{exercise_id},{'pass' if passed else 'fail'}\n")
    except OSError:
        pass  # progress is a nice-to-have; never crash the lesson


def solved_ids() -> set:
    try:
        lines = _path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {ln.split(",")[1] for ln in lines if ln.endswith(",pass")}
