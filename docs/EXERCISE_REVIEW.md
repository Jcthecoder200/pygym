# Exercise review

Every hand-written exercise must be checked by a person before a release. A teammate who is not
the author reads each row below, runs the exercise, and writes their name and the date in the
**Reviewer** column. Put `OK` in the last column when all eight checks are true, or write the
problem there.

Status: **all new exercises below are pending review.** They have been checked by machine only: the
reference solution passes the grader, an obvious solution without the target is rejected, and typical
wrong answers fail. No person has read them yet.

## How to review one exercise

Run `exercise("<target>", <level>)` until the id appears (or read `src/pygym/data/exercises.json`).
Then check:

1. The question is unambiguous and its example matches what the reference returns.
2. The target is the natural tool (not forced). The level fits (1 = one line, 3 = needs thought;
   `--levels` below shows where the reference differs from the AI level rules).
3. The tests cover normal cases and an edge case. A wrong but close solution fails at least one.
4. No float equality traps. No answer depends on the order of a set or dict of strings.
5. Returned values survive JSON: tuples become lists. The question says "list" when the checker
   compares lists.
6. The hint helps without giving the answer. The solution reads well for a first-year student.
7. Python 3.9 syntax (no `match`, no `X | Y` types). No imports unless in `allowed_imports`.
8. Plain English, short sentences.

Machine checks (already done, rerun any time): `python tools/validate_exercises.py` and
`python -m pytest tests/test_dataset.py`.

### Level rules report (`--levels`)

AI-generated exercises must follow fixed rules for each level (beginner: no if/else and no loop;
intermediate: a loop and a condition; advanced: plus a data structure or algorithm; see
[AI_MODE.md](AI_MODE.md#what-each-level-means)). Hand-written exercises are not held to them:
a reviewer decides. To see which ones differ, run

```
python tools/validate_exercises.py --levels
```

It prints one line per exercise whose reference solution does not follow its level's rule, with
what is missing (`LEVEL max-03 (3 advanced): no loop; no condition; no data structure or
algorithm`), and a count at the end. It only reports: it never fails (exit code 0) and changes
nothing. When it was added, **33 of 49** hand-written exercises differed (most level-2 and
level-3 ones are one-line calls with `key=` or a comprehension, written when level 3 meant
"tricky", not "algorithmic"). For each listed exercise, decide: keep it (and note why in the OK?
column), move it to another level, or rewrite the reference.

## New exercises (44)

| Id | Target | Level | What it asks | Reviewer | OK? |
|---|---|---|---|---|---|
| `map-01` | `map` | 1 | Square every number with map; wrap the iterator in list. |  |  |
| `map-02` | `map` | 2 | map over two lists adds items by position; stops at the shorter list. |  |  |
| `map-03` | `map` | 3 | Lengths of words plus their total; a map object can be read only once. |  |  |
| `filter-01` | `filter` | 1 | Keep the even numbers with filter and a lambda. |  |  |
| `filter-02` | `filter` | 2 | filter(None, items) drops falsy values (0, '', None, [], False). |  |  |
| `filter-03` | `filter` | 3 | Numbers strictly above the average; filter with a variable from outside. |  |  |
| `enumerate-01` | `enumerate` | 1 | All positions where a value appears. |  |  |
| `enumerate-02` | `enumerate` | 2 | Number lines from 1 with start=1 ('1. apple'). |  |  |
| `enumerate-03` | `enumerate` | 3 | Positions of peaks (bigger than both neighbours); enumerate the inner items with start=1. |  |  |
| `len-01` | `len` | 1 | Count the words in a string with len(text.split()). |  |  |
| `len-02` | `len` | 2 | True if no item repeats: len(set(items)) == len(items). |  |  |
| `len-03` | `len` | 3 | Middle character(s) of a string (odd or even length). |  |  |
| `max-01` | `max` | 1 | Largest number in a non-empty list. |  |  |
| `max-02` | `max` | 2 | Longest word with key=len and default=''; first one wins a tie. |  |  |
| `max-03` | `max` | 3 | Most common item with key=items.count; first one wins a tie; None for empty. |  |  |
| `min-01` | `min` | 1 | First word alphabetically; capital letters sort before lowercase. |  |  |
| `min-02` | `min` | 2 | Number closest to zero with key=abs and default=None; first wins a tie. |  |  |
| `min-03` | `min` | 3 | Shortest word, ties alphabetical: key returns a tuple (len, word). |  |  |
| `range-01` | `range` | 1 | List 1..n including n (the end is excluded in range). |  |  |
| `range-02` | `range` | 2 | Positive multiples of k up to limit with a step. |  |  |
| `range-03` | `range` | 3 | Every step-th item counting backwards from the last item (negative step). |  |  |
| `any-01` | `any` | 1 | True if some number is negative. |  |  |
| `any-02` | `any` | 2 | True if two lists share an item. |  |  |
| `any-03` | `any` | 3 | True if any cell in a grid is truthy (any inside any). |  |  |
| `all-01` | `all` | 1 | True if every number is positive; empty list is True. |  |  |
| `all-02` | `all` | 2 | True if every word starts with a prefix; empty list is True. |  |  |
| `all-03` | `all` | 3 | Every row is a permutation of 1..n (n = number of rows). |  |  |
| `sorted-02` | `sorted` | 2 | Sort [name, score] pairs by score (high first), then name; key returns a tuple. |  |  |
| `sorted-03` | `sorted` | 3 | Median of a list using sorted; None for empty input. |  |  |
| `sum-03` | `sum` | 3 | Concatenate lists with sum(groups, []) (the start argument). |  |  |
| `zip-03` | `zip` | 3 | Differences between neighbours with zip(nums, nums[1:]). |  |  |
| `reversed-01` | `reversed` | 1 | Reverse the order of the words in a sentence. |  |  |
| `reversed-02` | `reversed` | 2 | Palindrome check; reversed gives an iterator, so join it first. |  |  |
| `abs-01` | `abs` | 1 | Distance between two numbers. |  |  |
| `abs-02` | `abs` | 2 | Count numbers within a tolerance of a centre (boundary counts). |  |  |
| `divmod-01` | `divmod` | 1 | Seconds to [minutes, seconds]. |  |  |
| `divmod-02` | `divmod` | 2 | Digits of an integer with repeated divmod(n, 10). |  |  |
| `divmod-03` | `divmod` | 3 | Move a 24-hour clock by a negative or positive offset; floor division on negatives. |  |  |
| `round-01` | `round` | 1 | Round to 2 decimal places. |  |  |
| `round-02` | `round` | 2 | round(x) on halves goes to the even integer (0.5 -> 0, 2.5 -> 2). |  |  |
| `round-03` | `round` | 3 | round(n, -2) to the nearest hundred; halves go to even hundreds. |  |  |
| `isinstance-01` | `isinstance` | 1 | Count the strings in a mixed list. |  |  |
| `isinstance-02` | `isinstance` | 2 | Count real ints; bool is a subclass of int, so exclude it. |  |  |
| `isinstance-03` | `isinstance` | 3 | Sum numbers in nested lists; isinstance with a tuple of types and recursion. |  |  |

## Older exercises (written before this review list)

`zip-01`, `zip-02`, `sum-01`, `sum-02`, `sorted-01`. Their question texts do not name the target
function, only the hint does. A student who does not know to use `zip` or `sum` is graded
`target_not_used`. Reviewers should decide whether to add the target name to the question, like the
new exercises do. They also have only 3 tests (the new ones have 4 to 6).

## Notes for reviewers

- `round-02` and `round-03` teach that Python rounds halves to the even number. The question says
  so on purpose.
- `max-03` and `most_common_item` use `items.count`, which is slow for big lists. That is fine for
  practice, but do not praise it as good style.
- `reversed-02`, `map-03` and `isinstance-02` are built around a common mistake. Check that the
  hint points at the mistake without giving the code.
- Not covered on purpose: `list`, `dict`, `set` (a student writes `[]` or `{}` and never calls them),
  `print`, `input`, `open`, `exec`, `eval` (the sandbox cannot run them).
