"""Saved generated exercises: PYGYM_HOME/generated/<target>.json.

Every exercise that passed validation is saved here, so AI mode can reuse earlier exercises when
the model is slow, wrong or not running. File format: {"version": 1, "exercises": [<Exercise>]}.
Broken files are treated as empty; nothing here ever raises.
"""
from __future__ import annotations

import ast
import json
import os
import random
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List, Optional

from .. import grader, progress
from ..models import Exercise
from . import levels
from .validate import names_other_function

VERSION = 1
KEEP = 50
KEEP_REJECTED = 30


def _dir() -> Path:
    return Path(os.environ.get("PYGYM_HOME", Path.home() / ".pygym")) / "generated"


def path(target: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.]", "_", target) or "_"
    return _dir() / f"{safe}.json"


def load(target: str) -> List[Exercise]:
    """Saved exercises for a target, oldest first. [] on any problem."""
    try:
        data = json.loads(path(target).read_text(encoding="utf-8"))
        items = data["exercises"] if isinstance(data, dict) else []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for d in items:
        try:
            ex = Exercise.from_dict(d)
        except Exception:  # noqa: BLE001
            continue
        if ex.target_function == target and ex.source == "ai" and not grader.check_exercise(ex):
            out.append(ex)
    return out


def add(ex: Exercise, keep: int = KEEP) -> None:
    """Save one exercise (replacing an older copy with the same id). Never raises."""
    try:
        items = [e for e in load(ex.target_function) if e.id != ex.id] + [ex]
        items = items[-keep:]
        file = path(ex.target_function)
        file.parent.mkdir(parents=True, exist_ok=True)
        tmp = file.with_name(file.name + ".tmp")
        tmp.write_text(json.dumps({"version": VERSION, "exercises": [asdict(e) for e in items]},
                                  indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, file)
    except Exception:  # noqa: BLE001
        pass


def fits_level(ex: Exercise) -> bool:
    """Does the reference solution follow the rules of its level (levels.check_level)?
    Exercises saved by older pygym versions may not (a level-3 one-liner)."""
    try:
        tree = ast.parse(ex.reference_solution)
    except SyntaxError:
        return False
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == ex.function_name), None)
    return fn is not None and levels.check_level(tree, fn, ex.difficulty) is None


def pick(target: str, level: int, avoid_ids: Iterable[str] = ()) -> Optional[Exercise]:
    """A random saved exercise for this target and level that isn't in avoid_ids and fits the
    level's rules. Prefers ones the student hasn't solved yet."""
    avoid = set(avoid_ids or ())
    # Older pygym versions saved exercises whose question names another function than the code,
    # or that do not fit their level.
    pool = [e for e in load(target) if e.difficulty == level and e.id not in avoid
            and not names_other_function(e.question_text, e.function_name, e.target_function)
            and fits_level(e)]
    if not pool:
        return None
    solved = progress.solved_ids()
    fresh = [e for e in pool if e.id not in solved]
    return random.choice(fresh or pool)


def rejected_path() -> Path:
    return _dir() / "rejected.jsonl"


def log_rejected(target: str, level: int, generator: str, problem: str, raw: str) -> None:
    """Keep the last few rejected model replies (local file) so the prompt can be improved.
    Never raises."""
    try:
        file = rejected_path()
        file.parent.mkdir(parents=True, exist_ok=True)
        try:
            lines = file.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        lines.append(json.dumps({"time": int(time.time()), "target": target, "level": level,
                                 "generator": generator, "problem": problem,
                                 "reply": (raw or "")[:4000]}, ensure_ascii=False))
        tmp = file.with_name(file.name + ".tmp")
        tmp.write_text("\n".join(lines[-KEEP_REJECTED:]) + "\n", encoding="utf-8")
        os.replace(tmp, file)
    except Exception:  # noqa: BLE001
        pass
