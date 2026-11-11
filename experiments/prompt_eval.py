"""Measure the exercise prompt with a real model: old prompt (a git revision) vs the current one.

Runs ai.generate_exercise on a fixed grid (targets x levels) and prints, per cell: valid?, tries,
seconds, why the replies were rejected, and the prompt size in tokens. Each cell gets a fresh
PYGYM_HOME with the target's use cases copied in (generated once, so every prompt sees the
same situations) and a fixed seed (same use case, same examples, same sampling seeds).

Every real model call goes through a counter (a small JSON file): when --max-calls is used up,
the next call fails like an unreachable server and the run stops. Needs Ollama on localhost.

    python experiments/prompt_eval.py --usecases DIR --counter calls.json --max-calls 120 \
        --prompt new --tries 2 --out new.jsonl
    python experiments/prompt_eval.py --prompt old --old-rev dbf2287 ...
    python experiments/prompt_eval.py --table old.jsonl new.jsonl     # compare saved runs

--prompt old loads src/pygym/ai/prompt.py from the given git revision (git show, read only)
and uses its build_messages/schema_for; everything else (engine, validator) is the current code,
so the comparison is about the prompt only. --plan asks for the optional "plan" field,
--self-review turns the ai_self_review setting on.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pygym import config  # noqa: E402
from pygym.ai import engine, prompt, providers  # noqa: E402

GRID_TARGETS = ["sorted", "zip", "str.join", "statistics.median", "numpy.cumsum", "bin"]
BIGGER_AT_3 = {"bin"}


class Counter:
    """Real /api/chat calls, persisted in a JSON file shared by all runs."""

    def __init__(self, path: Path, limit: int):
        self.path, self.limit = path, limit
        self.calls: list = []                   # this run's calls (tokens, time)

    def used(self) -> int:
        try:
            return int(json.loads(self.path.read_text())["used"])
        except Exception:  # noqa: BLE001
            return 0

    def take(self) -> None:
        used = self.used()
        if used >= self.limit:
            raise providers.ProviderError(f"call budget used ({used}/{self.limit})")
        self.path.write_text(json.dumps({"used": used + 1}))

    def install(self) -> None:
        real = providers._http

        def counted(method, url, body, headers, timeout):
            if not url.endswith("/api/chat"):
                return real(method, url, body, headers, timeout)
            self.take()
            start = time.monotonic()
            reply = real(method, url, body, headers, timeout)
            self.calls.append({
                "seconds": round(time.monotonic() - start, 2),
                "prompt_tokens": reply.get("prompt_eval_count"),
                "output_tokens": reply.get("eval_count"),
                "done_reason": reply.get("done_reason"),
                "prompt_ms": round((reply.get("prompt_eval_duration") or 0) / 1e6),
                "load_ms": round((reply.get("load_duration") or 0) / 1e6),
                "num_ctx": (body.get("options") or {}).get("num_ctx"),
                "chars": sum(len(m.get("content", "")) for m in body.get("messages", [])),
            })
            return reply

        providers._http = counted


def load_old_prompt(rev: str):
    """src/pygym/ai/prompt.py at `rev`, as a module inside pygym.ai (git show, read only)."""
    source = subprocess.run(["git", "show", f"{rev}:src/pygym/ai/prompt.py"], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout
    folder = Path(tempfile.mkdtemp(prefix="old_prompt_"))
    path = folder / "prompt_old.py"
    path.write_text(source, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("pygym.ai.prompt_old", path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "pygym.ai"
    spec.loader.exec_module(module)
    shutil.rmtree(folder, ignore_errors=True)
    return module


def use_prompt(kind: str, rev: str, plan: bool) -> None:
    if kind == "old":
        old = load_old_prompt(rev)

        def build(info, level, *, use_case=None, avoid=(), repair=None, bigger=False, **_):
            return old.build_messages(info, level, use_case=use_case, avoid=avoid,
                                      repair=repair, bigger=bigger)
        engine.build_messages = build
        engine.schema_for = old.schema_for
    else:
        prompt.PLAN_FIELD = plan


def run_cell(target: str, level: int, args, counter: Counter, seed: int) -> dict:
    home = Path(tempfile.mkdtemp(prefix="pygym_eval_"))
    os.environ["PYGYM_HOME"] = str(home)
    saved = Path(args.usecases) / f"{target}.json"
    if saved.exists():
        data = json.loads(saved.read_text(encoding="utf-8"))
        data["used"] = []
        (home / "generated" / "usecases").mkdir(parents=True)
        (home / "generated" / "usecases" / saved.name).write_text(json.dumps(data))
    settings = config.Settings(mode="ai", model=args.model, ai_self_review=args.self_review)
    events: list = []
    first_call = len(counter.calls)
    bigger = level == 3 and target in BIGGER_AT_3
    start = time.monotonic()
    ex, error = None, ""
    try:
        ex = engine.generate_exercise(target, level, settings=settings, timeout=args.timeout,
                                      on_progress=events.append, bigger=bigger, seed=seed)
    except engine.GenerationUnavailable as e:
        error = str(e)
    seconds = round(time.monotonic() - start, 1)
    rejected, raw_rejected = [], []
    log = home / "generated" / "rejected.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            rejected.append(item.get("problem", ""))
            raw_rejected.append(item.get("reply", ""))
    calls = counter.calls[first_call:]
    row = {"target": target, "level": level, "bigger": bigger, "prompt": args.prompt,
           "plan": args.plan, "self_review": args.self_review, "model": args.model,
           "seed": seed, "valid": ex is not None and ex.source == "ai", "error": error,
           "tries": sum(1 for e in events if e["stage"] == "asking"), "calls": len(calls),
           "seconds": seconds,
           "short": [e["detail"] for e in events if e["stage"] == "rejected"],
           "rejected": rejected, "raw_rejected": raw_rejected, "call_log": calls,
           "exercise": None if ex is None else {
               "question_text": ex.question_text, "function_name": ex.function_name,
               "reference_solution": ex.reference_solution, "test_inputs": ex.test_inputs,
               "expected_outputs": ex.expected_outputs, "hint": ex.hint}}
    shutil.rmtree(home, ignore_errors=True)
    return row


def print_table(rows: list) -> None:
    print(f"{'run':<22} {'target':<18} {'lv':>2} {'ok':>3} {'tries':>5} {'s':>5} "
          f"{'p.tok':>5} rejected because")
    for r in rows:
        run = r["prompt"] + ("+plan" if r.get("plan") else "") + \
            ("+review" if r.get("self_review") else "") + \
            ("" if r["model"] == "qwen2.5:1.5b" else f" {r['model']}")
        tokens = [c["prompt_tokens"] for c in r["call_log"] if c.get("prompt_tokens")]
        level = f"{r['level']}{'b' if r['bigger'] else ''}"
        print(f"{run:<22} {r['target']:<18} {level:>2} {'yes' if r['valid'] else 'no':>3} "
              f"{r['tries']:>5} {r['seconds']:>5} {min(tokens) if tokens else '-':>5} "
              f"{'; '.join(r['short']) or '-'}")
    runs: dict = {}
    for r in rows:
        key = (r["prompt"], r.get("plan"), r.get("self_review"), r["model"])
        runs.setdefault(key, []).append(r)
    print()
    for key, group in runs.items():
        by_level = {lv: [g for g in group if g["level"] == lv] for lv in (1, 2, 3)}
        parts = ", ".join(f"L{lv} {sum(g['valid'] for g in gs)}/{len(gs)}"
                          for lv, gs in by_level.items() if gs)
        calls = sum(g["calls"] for g in group)
        secs = [g["seconds"] for g in group if g["valid"]]
        print(f"{key}: valid {sum(g['valid'] for g in group)}/{len(group)} ({parts}); "
              f"{calls} calls; mean time of valid {sum(secs) / len(secs) if secs else 0:.1f} s")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--table", nargs="+", help="print saved runs (JSONL files) and stop")
    ap.add_argument("--prompt", choices=["old", "new"], default="new")
    ap.add_argument("--old-rev", default="HEAD")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--self-review", action="store_true")
    ap.add_argument("--model", default="qwen2.5:1.5b")
    ap.add_argument("--targets", default=",".join(GRID_TARGETS))
    ap.add_argument("--levels", default="1,2,3")
    ap.add_argument("--tries", type=int, default=engine.MAX_ATTEMPTS)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=1000)
    ap.add_argument("--usecases", default=".", help="folder with <target>.json use cases")
    ap.add_argument("--counter", default="prompt_eval_calls.json")
    ap.add_argument("--max-calls", type=int, default=120)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.table:
        rows = [json.loads(line) for f in args.table
                for line in Path(f).read_text(encoding="utf-8").splitlines() if line.strip()]
        print_table(rows)
        return 0
    os.environ.pop("PYGYM_NO_AI", None)
    counter = Counter(Path(args.counter), args.max_calls)
    counter.install()
    use_prompt(args.prompt, args.old_rev, args.plan)
    engine.MAX_ATTEMPTS = args.tries
    targets = [t for t in args.targets.split(",") if t]
    if "numpy.cumsum" in targets and importlib.util.find_spec("numpy") is None:
        targets.remove("numpy.cumsum")
    rows = []
    for i, target in enumerate(targets):
        for level in (int(x) for x in args.levels.split(",")):
            if counter.used() >= counter.limit:
                print("call budget used: stopping")
                break
            row = run_cell(target, level, args, counter, args.seed + 10 * i + level)
            rows.append(row)
            print_table([row])
            if args.out:
                with open(args.out, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print_table(rows)
    print(f"model calls used in total: {counter.used()}/{counter.limit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
