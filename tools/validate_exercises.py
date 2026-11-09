"""Content owner's tool.

  python tools/validate_exercises.py          check every exercise
  python tools/validate_exercises.py --fill   recompute expected_outputs from reference solutions
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pygym.grader import grade, run_in_sandbox  # noqa: E402
from pygym.models import Exercise  # noqa: E402

PATH = Path(__file__).resolve().parents[1] / "src" / "pygym" / "data" / "exercises.json"
REQUIRED = ["id", "target_function", "difficulty", "question_text", "function_name",
            "function_signature", "reference_solution", "test_inputs"]


def main() -> int:
    fill = "--fill" in sys.argv
    data = json.loads(PATH.read_text(encoding="utf-8"))
    bad, ids = 0, set()
    for d in data:
        label = d.get("id", "?")
        missing = [k for k in REQUIRED if k not in d]
        if missing or d["id"] in ids or d["difficulty"] not in (1, 2, 3):
            print(f"FAIL {label}: bad fields {missing or ''}")
            bad += 1
            continue
        ids.add(d["id"])
        res = run_in_sandbox(d["reference_solution"], d["function_name"],
                             d["target_function"], d["test_inputs"], timeout=10)
        if res["status"] != "ok" or any("error" in o for o in res["outputs"]):
            print(f"FAIL {label}: reference solution crashed: {res}")
            bad += 1
            continue
        if fill:
            d["expected_outputs"] = [o["value"] for o in res["outputs"]]
        ex = Exercise.from_dict(d)
        verdict = grade(ex.reference_solution, ex)
        print(f"{'ok  ' if verdict.passed else 'FAIL'} {label}: {verdict.reason}")
        bad += 0 if verdict.passed else 1
    if fill:
        PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("expected_outputs written.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
