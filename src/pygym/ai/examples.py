"""Complete example exercises for the prompt (few-shot examples), and how one is chosen.

Small models copy the SHAPE of an example far more than they follow written rules: with one
beginner one-liner as the only example, qwen2.5:1.5b kept writing one-liners at intermediate and
advanced level, even after being told "too simple" (docs/AI_MODE.md, measurement 8). So the
prompt now shows examples OF THE REQUESTED LEVEL.

Two sources, both in the exact reply shape (question_text, function_name, function_signature,
reference_solution, tests, hint):

  origin "bank"    written here for the prompt: an everyday situation, every input and every
                   part of the result explained, one example call, the import (if any) named
                   and explained in the question and the hint, 4-6 tests with an edge case.
  origin "preset"  the hand-written exercises in data/exercises.json (reviewed by people) that
                   follow the level rules (levels.check_level) and have 4-6 tests. Presets that
                   break the rules are never shown. (A team suggestion: let the exercises we
                   already have show the model what each level means.)

Every example must pass the real validator at its level (validate.to_exercise); a test checks
this, so a broken example can never ship. To add one, append an Example to BANK (see
docs/PROMPT_DESIGN.md) and run the tests.

select() picks 1-2 examples for a request: same level (bigger-task examples in bigger mode),
never about the requested function and never calling it (the model would copy it), a bank
example first, of the most similar kind (library function, built-in, str/list/dict method).
The choice is deterministic for a given seed.
"""
from __future__ import annotations

import ast
import json
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from . import levels

PRESETS = Path(__file__).resolve().parents[1] / "data" / "exercises.json"
REPLY_FIELDS = ("question_text", "function_name", "function_signature", "reference_solution",
                "tests", "hint")
MIN_TESTS, MAX_TESTS = 4, 6       # what the prompt asks for; examples follow it too


@dataclass(frozen=True)
class Example:
    id: str
    origin: str          # "bank" | "preset"
    target: str          # the function the example teaches: "enumerate", "math.ceil"
    kind: str            # "builtin" | "method" | "module" (like TargetInfo.kind)
    level: int
    why: str             # what makes it this level, for the prompt ("a for loop ... and an if")
    reply: dict = field(compare=False)
    bigger: bool = False
    plan: str = ""       # 1-3 sentences: situation -> inputs -> steps -> output

    def as_json(self, with_plan: bool = False) -> str:
        """The example as the model should answer (schema field order)."""
        data = {"plan": self.plan} if with_plan and self.plan else {}
        data.update((k, self.reply[k]) for k in REPLY_FIELDS)
        return json.dumps(data, ensure_ascii=False)

    def uses(self, target: str) -> bool:
        """Is `target` ("str.join", "sorted") this example's function, or does its code call it?"""
        if target == self.target:
            return True
        short = target.rpartition(".")[2]
        tree = ast.parse(self.reply["reference_solution"])
        return any((isinstance(n, ast.Name) and n.id == short)
                   or (isinstance(n, ast.Attribute) and n.attr == short)
                   for n in ast.walk(tree))


def _ex(id, target, kind, level, why, plan, question, name, params, reference, tests, hint,
        bigger=False) -> Example:
    return Example(id=id, origin="bank", target=target, kind=kind, level=level, why=why,
                   plan=plan, bigger=bigger, reply={
                       "question_text": question, "function_name": name,
                       "function_signature": f"def {name}({params}):",
                       "reference_solution": reference, "tests": tests, "hint": hint})


# ----- the bank ------------------------------------------------------------------------------
# Names come from many cultures; amounts are in neutral, explained units; no brands, no real
# people, no sensitive topics (the content rules in prompt.SYSTEM apply to examples too).

BANK: List[Example] = [
    # --- beginner: one line, or a few straight lines; no if, no loop -------------------------
    _ex("bank-round-1", "round", "builtin", 1,
        "one return line: arithmetic and round, no if and no loop",
        "Friends split a restaurant bill. Inputs: the total and the number of people. "
        "Step: divide and round. Output: each person's share.",
        "Friends at a restaurant split the bill evenly. Write a function split_bill(total, "
        "people). total is the whole bill (a number in the local currency). people is how many "
        "friends pay (an int, at least 1). Return the amount each person pays: total divided "
        "by people, rounded to 2 decimal places with round(). Example: split_bill(100, 3) "
        "returns 33.33.",
        "split_bill", "total, people",
        "def split_bill(total, people):\n    return round(total / people, 2)",
        [{"total": 100, "people": 3}, {"total": 45.5, "people": 2}, {"total": 12, "people": 1},
         {"total": 0, "people": 4}, {"total": 10, "people": 6}],
        "round(x, 2) keeps two digits after the decimal point."),
    _ex("bank-str.split-1", "str.split", "method", 1,
        "two straight lines (split the text, then compute), no if and no loop",
        "A bus timetable shows times as text. Input: a time like '14:35'. Steps: split at ':' "
        "and convert. Output: minutes since midnight.",
        "A bus timetable app stores times as text like \"14:35\" (hours:minutes, 24-hour "
        "clock). Write a function minutes_since_midnight(time_text). time_text is a string "
        "\"HH:MM\". Return the number of minutes from midnight (00:00) to that time, as an int: "
        "hours times 60 plus minutes. Use the .split() method of the string to cut it at the "
        "\":\". Example: minutes_since_midnight(\"14:35\") returns 875.",
        "minutes_since_midnight", "time_text",
        "def minutes_since_midnight(time_text):\n    hours, minutes = time_text.split(\":\")\n"
        "    return int(hours) * 60 + int(minutes)",
        [{"time_text": "14:35"}, {"time_text": "00:00"}, {"time_text": "09:05"},
         {"time_text": "23:59"}],
        "\"14:35\".split(\":\") gives [\"14\", \"35\"]; int() turns each part into a number."),
    _ex("bank-math.ceil-1", "math.ceil", "module", 1,
        "one return line that uses math.ceil directly on the input, no if and no loop",
        "A school plans a trip. Inputs: number of students, seats per bus. Step: divide and "
        "round up. Output: buses needed.",
        "A school is planning a trip and needs buses. Write a function buses_needed(students, "
        "seats_per_bus). students is how many students go (an int, 0 or more). seats_per_bus "
        "is how many students fit in one bus (an int, at least 1). Return how many buses are "
        "needed, as an int: a bus that is only partly full still counts. Use the math module "
        "(import math): math.ceil rounds a number up to the next whole number. 0 students need "
        "0 buses. Example: buses_needed(130, 50) returns 3.",
        "buses_needed", "students, seats_per_bus",
        "import math\n\ndef buses_needed(students, seats_per_bus):\n"
        "    return math.ceil(students / seats_per_bus)",
        [{"students": 130, "seats_per_bus": 50}, {"students": 100, "seats_per_bus": 50},
         {"students": 0, "seats_per_bus": 40}, {"students": 1, "seats_per_bus": 30},
         {"students": 61, "seats_per_bus": 20}],
        "import math, then math.ceil(students / seats_per_bus) rounds the division up."),
    _ex("bank-dict.get-1", "dict.get", "method", 1,
        "one return line that calls .get on the dict, no if and no loop",
        "A small shop checks its stock. Inputs: a dict of product -> pieces, a product name. "
        "Step: look it up with a default. Output: pieces in stock, 0 if unknown.",
        "A small corner shop keeps its stock in a dict. Write a function in_stock(stock, "
        "product). stock maps a product name (a string) to how many pieces are on the shelf "
        "(an int). product is the name to look up. Return how many pieces of that product are "
        "in stock, as an int. A product that is not in the dict has 0 pieces. Use the .get() "
        "method of the dict. Example: in_stock({\"rice\": 12, \"tea\": 3}, \"tea\") returns 3.",
        "in_stock", "stock, product",
        "def in_stock(stock, product):\n    return stock.get(product, 0)",
        [{"stock": {"rice": 12, "tea": 3}, "product": "tea"},
         {"stock": {"rice": 12, "tea": 3}, "product": "rice"},
         {"stock": {"rice": 12}, "product": "salt"}, {"stock": {}, "product": "tea"}],
        "d.get(key, default) returns default when the key is missing."),

    # --- intermediate: a loop AND a condition -------------------------------------------------
    _ex("bank-enumerate-2", "enumerate", "builtin", 2,
        "a for loop goes through the days and an if decides for each day",
        "A step counter checks a week. Inputs: steps per day, a daily goal. Steps: loop with "
        "day numbers, keep days below the goal. Output: those day numbers.",
        "A step-counter app checks which days missed the daily goal. Write a function "
        "missed_days(steps, goal). steps is a list of ints: the steps walked on each day, day 1 "
        "first. goal is the daily goal (an int). Return a list of the day numbers (day 1 is the "
        "first value) where the steps were below the goal, in order. A day that reaches the "
        "goal exactly is not missed. An empty list gives []. Use enumerate(steps, start=1) to "
        "get each day number with its steps. Example: missed_days([8000, 4500, 10000], 6000) "
        "returns [2].",
        "missed_days", "steps, goal",
        "def missed_days(steps, goal):\n    days = []\n"
        "    for day, count in enumerate(steps, start=1):\n        if count < goal:\n"
        "            days.append(day)\n    return days",
        [{"steps": [8000, 4500, 10000], "goal": 6000}, {"steps": [], "goal": 5000},
         {"steps": [100, 200, 300], "goal": 1000}, {"steps": [6000, 5999], "goal": 6000},
         {"steps": [7000], "goal": 3000}],
        "enumerate(steps, start=1) gives pairs (day number, steps), so the loop knows the day."),
    _ex("bank-str.strip-2", "str.strip", "method", 2,
        "a for loop goes through the names and an if skips the empty ones",
        "A club cleans a sign-up sheet. Input: names as typed. Steps: strip each name, skip "
        "empty ones. Output: the clean names in order.",
        "Students typed their names into a club sign-up sheet, sometimes with extra spaces or "
        "an empty line. Write a function clean_names(entries). entries is a list of strings, "
        "one per line of the sheet. Return a new list with each name without spaces at the "
        "start and end, in the same order. Leave out lines that are empty after removing the "
        "spaces. Spaces inside a name stay. Use the .strip() method of each string. Example: "
        "clean_names([\"  Amara \", \"\", \"Kenji Sato\"]) returns [\"Amara\", \"Kenji Sato\"].",
        "clean_names", "entries",
        "def clean_names(entries):\n    names = []\n    for entry in entries:\n"
        "        name = entry.strip()\n        if name:\n            names.append(name)\n"
        "    return names",
        [{"entries": ["  Amara ", "", "Kenji Sato"]}, {"entries": []},
         {"entries": ["   ", "\t"]}, {"entries": ["Sofia", " Mateo", "Priya  "]}],
        "\"  Amara \".strip() gives \"Amara\"; an empty string counts as False in an if."),
    _ex("bank-math.hypot-2", "math.hypot", "module", 2,
        "a for loop goes through the drop points and an if checks each distance",
        "A delivery drone has a range. Inputs: drop points as [x, y] in km, the range. Steps: "
        "distance of each point, count those in range. Output: the count.",
        "A delivery drone starts at its base, the point (0, 0) on a map in km. Write a function "
        "reachable(points, max_km). points is a list of [x, y] pairs: the drop points, in km "
        "east (x) and north (y) of the base. max_km is the drone's range in km. Return how many "
        "points are at most max_km away from the base in a straight line, as an int. A point "
        "exactly max_km away counts. Use the math module (import math): math.hypot(x, y) gives "
        "the straight-line distance from (0, 0) to (x, y). Example: reachable([[3, 4], "
        "[6, 8]], 5) returns 1.",
        "reachable", "points, max_km",
        "import math\n\ndef reachable(points, max_km):\n    count = 0\n"
        "    for x, y in points:\n        if math.hypot(x, y) <= max_km:\n"
        "            count += 1\n    return count",
        [{"points": [[3, 4], [6, 8]], "max_km": 5}, {"points": [], "max_km": 10},
         {"points": [[1, 1], [-2, 0], [0, -9]], "max_km": 3},
         {"points": [[5, 12]], "max_km": 13}, {"points": [[5, 12]], "max_km": 12.9}],
        "import math; math.hypot(3, 4) is 5.0, the distance from (0, 0)."),

    # --- advanced: a loop, a condition AND a data structure or algorithm ---------------------
    _ex("bank-sorted-3", "sorted", "builtin", 3,
        "a loop with an if fills a dict of totals, then sorted(..., key=) ranks them",
        "A book club ranks readers. Input: [name, pages] entries, several per person. Steps: "
        "add pages per name in a dict, sort by total then name. Output: [name, total] pairs.",
        "A book club logs reading: each entry is [name, pages], and one member can have many "
        "entries. Write a function ranking(entries). entries is a list of [name, pages] pairs "
        "(name a string, pages an int). Add up the pages of each member. Return a list of "
        "[name, total_pages] pairs, the member with the most pages first. Members with the "
        "same total are in alphabetical order of name. An empty list gives []. Use "
        "sorted(..., key=...) to order the members. Example: ranking([[\"Olu\", 30], "
        "[\"Lena\", 50], [\"Olu\", 40]]) returns [[\"Olu\", 70], [\"Lena\", 50]].",
        "ranking", "entries",
        "def ranking(entries):\n    totals = {}\n    for name, pages in entries:\n"
        "        if name not in totals:\n            totals[name] = 0\n"
        "        totals[name] += pages\n"
        "    order = sorted(totals, key=lambda name: (-totals[name], name))\n"
        "    return [[name, totals[name]] for name in order]",
        [{"entries": [["Olu", 30], ["Lena", 50], ["Olu", 40]]}, {"entries": []},
         {"entries": [["Wei", 10], ["Ana", 10]]},
         {"entries": [["Fatima", 5], ["Diego", 20], ["Fatima", 25], ["Diego", 1]]}],
        "Collect totals in a dict first; then sorted(totals, key=...) can sort by the total."),
    _ex("bank-str.split-3", "str.split", "method", 3,
        "nested loops (reviews, then words), an if that skips short words, and a dict of counts",
        "A cafe reads short reviews. Inputs: review texts, a minimum count. Steps: split each "
        "review into words, count words of 3+ letters in a dict, keep frequent ones. Output: "
        "a dict word -> count.",
        "A small cafe wants to know which words its customers use most in short reviews. Write "
        "a function frequent_words(reviews, min_count). reviews is a list of strings; words in "
        "a review are separated by spaces. min_count is an int. Count every word in lower case, "
        "over all reviews, but skip words with fewer than 3 letters. Return a dict that maps "
        "each word used at least min_count times to its count. Use the .split() method of each "
        "review to get its words. No reviews give {}. Example: frequent_words([\"Good coffee\", "
        "\"good cake\"], 2) returns {\"good\": 2}.",
        "frequent_words", "reviews, min_count",
        "def frequent_words(reviews, min_count):\n    counts = {}\n    for review in reviews:\n"
        "        for word in review.lower().split():\n            if len(word) < 3:\n"
        "                continue\n            counts[word] = counts.get(word, 0) + 1\n"
        "    return {w: c for w, c in counts.items() if c >= min_count}",
        [{"reviews": ["Good coffee", "good cake"], "min_count": 2},
         {"reviews": [], "min_count": 1},
         {"reviews": ["so so tea", "Tea is hot", "hot tea"], "min_count": 2},
         {"reviews": ["Nice place"], "min_count": 1}],
        "review.lower().split() gives the words of one review; a dict keeps a count per word."),
    _ex("bank-math.floor-3", "math.floor", "module", 3,
        "a loop with an if that skips invalid scores, a dict of counts per band, then a second "
        "loop that builds the result in band order",
        "A teacher groups exam scores. Input: scores 0-100. Steps: skip invalid scores, find "
        "each score's band of 10 with math.floor, count per band in a dict. Output: [band, "
        "count] pairs in band order.",
        "A teacher wants to see how exam scores are spread. Write a function score_bands(scores). "
        "scores is a list of numbers; a valid score is from 0 to 100. Put each valid score in a "
        "band of 10 points: 0-9 is band 0, 10-19 is band 10, and so on; 100 is band 100. Skip "
        "scores below 0 or above 100. Return a list of [band, count] pairs, smallest band first, "
        "only for bands that have at least one score. Use the math module (import math): "
        "math.floor(x) rounds x down, so math.floor(score / 10) * 10 is the band. Example: "
        "score_bands([72, 75.5, 91, 104]) returns [[70, 2], [90, 1]].",
        "score_bands", "scores",
        "import math\n\ndef score_bands(scores):\n    counts = {}\n    for score in scores:\n"
        "        if score < 0 or score > 100:\n            continue\n"
        "        band = math.floor(score / 10) * 10\n"
        "        counts[band] = counts.get(band, 0) + 1\n    result = []\n"
        "    for band in range(0, 101, 10):\n        if band in counts:\n"
        "            result.append([band, counts[band]])\n    return result",
        [{"scores": [72, 75.5, 91, 104]}, {"scores": []}, {"scores": [0, 9.9, 10, 100]},
         {"scores": [-5, 55, 58, 51]}],
        "import math; math.floor(score / 10) * 10 turns 75.5 into band 70; count bands in a dict."),

    # --- bigger task: a simple function as one step of a larger algorithm --------------------
    _ex("bank-abs-3-bigger", "abs", "builtin", 3,
        "`abs` is one step: nested loops go through each city's readings, an if keeps the "
        "biggest change, and a dict holds the result per city",
        "A weather site compares days. Input: a dict city -> daily temperatures. Steps: for "
        "each city, loop over neighbouring days, abs of the difference, keep the biggest. "
        "Output: a dict city -> biggest change.",
        "A weather website shows how fast the temperature changed in each city. Write a "
        "function biggest_change(readings). readings is a dict that maps a city name to a list "
        "of daily temperatures in °C (numbers, in day order). For each city, find the biggest "
        "change between two days in a row, as a positive number: use abs() of the difference. "
        "Return a dict that maps each city to its biggest change. A city with fewer than 2 "
        "readings has a change of 0. Example: biggest_change({\"Lima\": [18, 21, 17]}) returns "
        "{\"Lima\": 4}.",
        "biggest_change", "readings",
        "def biggest_change(readings):\n    result = {}\n"
        "    for city, temps in readings.items():\n        biggest = 0\n"
        "        for i in range(1, len(temps)):\n"
        "            change = abs(temps[i] - temps[i - 1])\n            if change > biggest:\n"
        "                biggest = change\n        result[city] = biggest\n    return result",
        [{"readings": {"Lima": [18, 21, 17]}}, {"readings": {}},
         {"readings": {"Oslo": [-3, 2], "Pune": [30]}},
         {"readings": {"Accra": [27, 27, 26.5], "Quito": [14, 9, 15]}}],
        "abs(a - b) is the size of a change, whatever its direction.", bigger=True),
    _ex("bank-str.upper-3-bigger", "str.upper", "method", 3,
        "`.upper()` is one step: a loop with an if fills a dict of groups, and "
        "sorted(..., key=) orders the names inside each group",
        "A badge desk groups guests. Input: guest names. Steps: skip empty names, group by "
        "the first letter in capitals in a dict, sort each group by length then name. Output: "
        "a dict letter -> names.",
        "At a festival, the badge desk puts guests into queues by the first letter of their "
        "name. Write a function badge_queues(names). names is a list of strings. Skip empty "
        "strings. Group the other names by their first letter in capitals (use the .upper() "
        "method), so \"amara\" and \"Ali\" are both in queue \"A\". Return a dict that maps each "
        "capital letter to the list of names in that queue, shortest name first; names of the "
        "same length are in alphabetical order. Names keep their original spelling. Example: "
        "badge_queues([\"Kenji\", \"ali\", \"Amara\"]) returns {\"K\": [\"Kenji\"], \"A\": "
        "[\"ali\", \"Amara\"]}.",
        "badge_queues", "names",
        "def badge_queues(names):\n    queues = {}\n    for name in names:\n"
        "        if not name:\n            continue\n        letter = name[0].upper()\n"
        "        queues.setdefault(letter, []).append(name)\n"
        "    for letter in queues:\n"
        "        queues[letter] = sorted(queues[letter], key=lambda n: (len(n), n))\n"
        "    return queues",
        [{"names": ["Kenji", "ali", "Amara"]}, {"names": []}, {"names": ["", "Zoe"]},
         {"names": ["bo", "Bea", "Ben", "Chen"]}],
        "name[0].upper() gives the capital first letter; a dict of lists holds the queues.",
        bigger=True),
]


# ----- hand-written exercises (data/exercises.json) as examples -----------------------------

def _level_words(feats: levels.Features, level: int) -> str:
    """'a comprehension and an if inside a comprehension' etc., from levels.analyse."""
    if level == 1:
        return "one return line, no if and no loop" if feats.lines <= 1 else \
            "a few straight lines, no if and no loop"
    parts = [feats.loops[0], feats.branches[0]] + feats.marker_words()[:2]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _params(signature: str) -> List[str]:
    fn = ast.parse(signature.strip() + "\n    pass").body[0]
    return [a.arg for a in fn.args.posonlyargs + fn.args.args]


def _from_preset(d: dict) -> Optional[Example]:
    """A hand-written exercise as an Example, or None when it can't be one: it does not follow
    its level, has fewer than 4 or more than 6 tests, or is a beginner one-liner with a loop or
    an if in it (allowed at beginner, but as an example it blurs the line to intermediate)."""
    try:
        level = d["difficulty"]
        tree = ast.parse(d["reference_solution"])
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == d["function_name"])
        if levels.gaps(tree, fn, level):          # the rule itself (check_level words it)
            return None
        feats = levels.analyse(tree, fn)
        if level == 1 and (feats.loops or feats.branches):
            return None
        tests = d["test_inputs"]
        if not MIN_TESTS <= len(tests) <= MAX_TESTS:
            return None
        params = _params(d["function_signature"])
        if any(len(t) != len(params) for t in tests):
            return None
        reply = {"question_text": d["question_text"], "function_name": d["function_name"],
                 "function_signature": d["function_signature"],
                 "reference_solution": d["reference_solution"],
                 "tests": [dict(zip(params, t)) for t in tests], "hint": d["hint"]}
        return Example(id=f"preset-{d['id']}", origin="preset", target=d["target_function"],
                       kind="builtin", level=level, why=_level_words(feats, level),
                       reply=reply)
    except Exception:  # noqa: BLE001  (a broken preset is simply not an example)
        return None


@lru_cache(maxsize=1)
def presets() -> tuple:
    """The hand-written exercises that can be examples (see _from_preset)."""
    try:
        data = json.loads(PRESETS.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return ()
    return tuple(e for e in (_from_preset(d) for d in data if isinstance(d, dict)) if e)


def all_examples() -> List[Example]:
    return list(BANK) + list(presets())


# ----- choosing examples for a request -------------------------------------------------------

def select(target: str, kind: str, level: int, *, bigger: bool = False, seed: int = 0,
           n: int = 2) -> List[Example]:
    """Up to n examples for an exercise about `target` (its TargetInfo.kind) at `level`.

    Same level; in bigger mode (advanced only) the bigger-task examples (the plain advanced
    ones if none is left). Never an example about `target` or whose code calls it. The first
    is a bank example when there is one (they show the full shape: situation, explained
    result, example call), of the most similar kind; the others are drawn at random from the
    rest, presets included. The same seed gives the same choice."""
    level = level if level in levels.LEVEL_NAMES else 1
    bigger = bigger and level == 3
    usable = [e for e in all_examples() if e.level == level and not e.uses(target)]
    pool = [e for e in usable if e.bigger == bigger] or [e for e in usable if not e.bigger]
    if not pool or n <= 0:
        return []
    rng = random.Random(seed)
    keyed = [(e.origin != "bank", e.kind != kind, rng.random(), e) for e in pool]
    first = min(keyed, key=lambda k: k[:3])[3]
    rest = [e for e in pool if e is not first]
    rng.shuffle(rest)
    return [first] + rest[:n - 1]
