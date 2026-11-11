"""One learning session. The UI only ever talks to this object.

All command rules live here (the "backbone"), so every front-end (text CLI, tkinter popup,
anything later) behaves the same way. A front-end only:
  1. collects the student's code and the command they chose,
  2. calls  session.handle(command, code)  and
  3. displays the returned Response and obeys its flags (quit / ask_reveal / ask_bigger /
     clear_code).

A command that loads a new exercise (:s, :level 2) can take up to 20 s in AI mode, because a local
model writes it. A front-end that wants to stay responsive calls  session.start(command, code)
instead: it returns a Job at once, the exercise is written in a background thread, and
job.result() gives the same Response handle() would have given (see Job).

Prefetch: while the student works on an exercise that the AI wrote (or would write), the next one
is already being written in the background (one at a time, at the level :s would use; see
Session.prefetch). :s then takes it at once, or, if it is still being written, start(":s")
returns a Job that follows THAT work (its progress, no second model request).

Bigger tasks: a function too small for an advanced exercise on its own (bin, abs, str.upper; see
ai.targets.TargetInfo.simple), when the AI writes its exercises, is not moved to advanced without
asking. :level 3, a level-up to 3 or a session started at level 3 give a Response with
ask_bigger=True; the front-end asks bigger_prompt() [Y/n] and sends the answer as the command
":bigger yes" (advanced exercises are bigger tasks that use the function as one step) or
":bigger no" (stay at intermediate). The answer is remembered for the rest of the session. Until
it is known the level stays below advanced, so neither :s nor the prefetch writes an advanced
exercise for such a function.
"""
from __future__ import annotations

import builtins
import inspect
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import grader, loader, progress
from .models import Exercise, GradeResult, Response

# The command table: one place for names and wording, shown by the intro screen.
COMMANDS: List[Tuple[str, str]] = [
    (":w", "check your code"),
    (":wq", "check, then quit if it passes (if it fails you can reveal the solution)"),
    (":out", "show the test results of the last check again (editor: open / close them)"),
    (":q", "quit (refused if your code changed since the last :w or the last check failed)"),
    (":q!", "quit and throw your code away"),
    (":h", "hint"),
    (":help", "the function's own documentation"),
    (":s", "skip to another exercise"),
    (":solution", "show the solution"),
    (":level", "show your level; :level 1-3 (or b, i, a) changes it (short: :lv)"),
    (":intro", "show this list of commands again"),
]
COMMAND_NAMES = frozenset(name for name, _ in COMMANDS)
# Short descriptions for tight spaces (the editor's start screen). Same commands as COMMANDS.
SHORT = {":w": "check your code", ":wq": "check, then quit", ":out": "show test results",
         ":q": "quit",
         ":q!": "quit without checking", ":h": "hint", ":help": "the function's documentation",
         ":s": "skip to another exercise", ":solution": "show the solution",
         ":level": "show / change your level", ":intro": "show this list again"}
# Commands that take one word after them (":level 2"). Every other command is the whole line.
# ":bigger yes|no" answers the bigger-task question (front-ends send it; not in the intro list).
ARG_COMMANDS = frozenset({":level", ":lv", ":bigger"})
# Shown after "Unknown command": :level is left out so the line still fits 80 columns
# (:intro and the start screen list it).
_TRY = " ".join(name for name, _ in COMMANDS if name != ":level")

NOTHING_CHECKED = "Nothing checked yet. :w to check your code."
OLD_RESULTS = "These results are from before your last changes: :w to check again."
BUSY = "Still writing the next exercise: wait for it, or cancel it (Ctrl-C)."
CANCELLED = "Cancelled. You are still on this exercise."
NO_OTHER = "No other exercise is available right now. Keep going with this one, or :q! to quit."
FALLBACK_DETAIL = "using a hand-written exercise instead"

# Levels. Solving LEVEL_UP_AFTER exercises at a level moves the session one level up (never
# down: going down is only the student's choice, with :level).
LEVELS: Dict[int, str] = {1: "beginner", 2: "intermediate", 3: "advanced"}
MAX_LEVEL = 3
LEVEL_UP_AFTER = 3

# The bigger-task question (see the module docstring). The Response says why (too_small_text);
# the front-end then asks this, with the function's name in it.
BIGGER_PROMPT = ("Practise it inside a bigger task ({name} + loops, conditions and data "
                 "structures)? [Y/n] ")
NOT_ANSWERED = "Not answered: you stay at this level (:level 3 asks again)."

# A line is a command only if the WHOLE line is one: ":" + letters/"!" ("…:wq"), or a command
# of ARG_COMMANDS followed by exactly one word (":level 2"). Real Python code practically never
# looks like this, so it is safe to keep such lines out of the code.
_PLAIN_COMMAND = re.compile(r"^:[A-Za-z!]+$")
_COMMAND_WITH_ARG = re.compile(r"^(:[A-Za-z]+)\s+(\S+)$")


def is_command_line(line: str) -> bool:
    """True if this line (spaces around it ignored) is a command, not code.

    ":w", ":wq", ":xyz" (unknown, but still not code), ":level 2" -> True;
    ":level 2 3", "x = :level", "    : 1}" (a dict continuation line) -> False."""
    s = (line or "").strip()
    if _PLAIN_COMMAND.match(s):
        return True
    m = _COMMAND_WITH_ARG.match(s)
    return bool(m and m.group(1) in ARG_COMMANDS)


def parse_level(text) -> Optional[int]:
    """'2', 'i', 'Intermediate', 'adv' -> 2 / 2 / 2 / 3. Anything else -> None."""
    t = str(text or "").strip().lower()
    if t in ("1", "2", "3"):
        return int(t)
    if t:
        for level, name in LEVELS.items():
            if name.startswith(t):
                return level
    return None


def level_dots(level: int) -> str:
    """●○○ for level 1, ●●○ for 2, ●●● for 3 (the same dots as style.dots)."""
    n = max(0, min(MAX_LEVEL, int(level or 0)))
    return "●" * n + "○" * (MAX_LEVEL - n)


def bigger_answer(text: Optional[str]) -> Optional[str]:
    """What a front-end sends after the [Y/n] bigger-task question: Enter, y or yes ->
    ":bigger yes"; n or no -> ":bigger no"; anything else, or None (EOF / Ctrl-C / Esc) -> None:
    not answered (send nothing; the level stays as it is)."""
    if text is None:
        return None
    t = str(text).strip().lower()
    if t in ("", "y", "yes"):
        return ":bigger yes"
    if t in ("n", "no"):
        return ":bigger no"
    return None


def _yes_no(arg: Optional[str]) -> Optional[bool]:
    t = (arg or "").strip().lower()
    return True if t in ("y", "yes") else False if t in ("n", "no") else None


def simple_target(name: str) -> bool:
    """True if the function is too small for an advanced exercise on its own (bin, abs, len,
    str.upper; ai.targets.TargetInfo.simple). False if it can not be looked up."""
    try:
        from .ai import targets
        return targets.resolve(name).simple
    except Exception:  # noqa: BLE001
        return False


def _valid_level(difficulty) -> int:
    """1, 2 or 3; anything else (None = "not chosen") starts at 1."""
    try:
        level = int(difficulty)
    except (TypeError, ValueError):
        return 1
    return level if 1 <= level <= MAX_LEVEL else 1


def commands_text() -> str:
    """The command list as plain text (what :intro returns)."""
    width = max(len(name) for name, _ in COMMANDS)
    return "\n".join(f"{name.ljust(width)}  {desc}" for name, desc in COMMANDS)


def _normalize(code: Optional[str]) -> str:
    """Ignore trailing spaces and blank lines when deciding whether code changed."""
    lines = [ln.rstrip() for ln in (code or "").splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _signature(obj) -> str:
    """"(data)" for statistics.median; "" when Python can't tell (some C functions)."""
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return ""


def _split_command(cmd: str) -> Tuple[str, Optional[str]]:
    """":level 2" -> (":level", "2"); ":w" -> (":w", None); anything else -> (cmd, None)."""
    m = _COMMAND_WITH_ARG.match(cmd)
    if m and m.group(1) in ARG_COMMANDS:
        return m.group(1), m.group(2)
    return cmd, None


# ----- background jobs ------------------------------------------------------------------------

class JobCancelled(BaseException):
    """Raised inside a cancelled job's worker (by its progress callback) to stop it early.

    A BaseException, so that the AI engine's "a bug must not crash a lesson" handlers
    (`except Exception`) let it through instead of turning it into a fallback."""


# The progress of a job before its first event. The AI engine reports dicts with these keys
# (see pygym.ai.generate_exercise(on_progress=...)); stage is one of
# "starting" | "ideas" | "asking" | "checking" | "rejected" | "done" | "fallback".
STARTING: Dict[str, Any] = {"stage": "starting", "attempt": 0, "max_attempts": 4,
                            "elapsed": 0.0, "budget": 20.0, "detail": ""}


class Job:
    """Work that may take a while (writing an exercise with the AI), run in a background thread.

    For a front-end:
        job.done        True once the work finished, failed or was cancelled (never blocks)
        job.progress    the latest progress event: a dict like STARTING (a copy)
        job.elapsed     seconds since the job started (for a smoothly moving bar)
        job.cancel()    stop waiting; the result is thrown away (Session: the exercise stays)
        job.wait(t)     block up to t seconds; True when done
        job.join(t)     like wait(), but for the worker thread itself, also after cancel()
        job.result()    block until done, then the outcome (Session jobs: a Response). The first
                        call applies it (a new exercise replaces the old one, on the CALLER's
                        thread): call it once the job is done, from the thread that owns the
                        session. Later calls return the same value.

    The worker never changes the Session: it only prepares the new exercise. So the student's
    code, the current exercise and the session state stay untouched until result() is called.
    """

    def __init__(self, work: Optional[Callable[[Callable[[Dict[str, Any]], None]], Any]] = None,
                 finish: Optional[Callable[[Any, Optional[BaseException]], Any]] = None, *,
                 background: bool = True, on_cancel: Any = None,
                 follow: Optional["Job"] = None) -> None:
        """work(report) does the job and returns its value; report(event_dict) updates progress
        (it raises JobCancelled once the job is cancelled). finish(value, error) turns the
        outcome into the result (default: the value, or the error raised again). on_cancel is
        what result() returns after cancel(). background=False runs work right here.

        follow=other (instead of work): this job waits for another job that is already running
        (a prefetch) and shows ITS progress and elapsed time. Cancelling this job does not stop
        the other one; result() is finish(other's value, other's error)."""
        self._lock = threading.Lock()
        self._follow = follow
        self._finished = follow._finished if follow is not None else threading.Event()
        self._progress: Dict[str, Any] = dict(STARTING)
        self._started = follow._started if follow is not None else time.monotonic()
        self._cancelled = False
        self._applied = False
        self._value: Any = None
        self._error: Optional[BaseException] = None
        self._result: Any = None
        self._finish = finish
        self._on_cancel = on_cancel
        self.background = bool(background and work is not None) or follow is not None
        if follow is not None:
            pass
        elif work is None:
            self._finished.set()
        elif self.background:
            threading.Thread(target=self._run, args=(work,), name="pygym-job", daemon=True).start()
        else:
            self._run(work)

    @classmethod
    def finished(cls, value: Any) -> "Job":
        """A job that is already done (a command that needs no waiting)."""
        job = cls()
        job._result, job._applied = value, True
        return job

    def _report(self, event: Dict[str, Any]) -> None:
        if self._cancelled:
            raise JobCancelled()
        with self._lock:
            self._progress = {**self._progress, **dict(event)}

    def _run(self, work) -> None:
        try:
            self._value = work(self._report)
        except JobCancelled:
            pass
        except Exception as e:  # noqa: BLE001  (handed to finish() on the caller's thread)
            self._error = e
        finally:
            self._finished.set()

    @property
    def done(self) -> bool:
        return self._cancelled or self._finished.is_set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def pending(self) -> bool:
        """True until the outcome was applied (result()) or the job was cancelled."""
        return not (self._applied or self._cancelled)

    @property
    def progress(self) -> Dict[str, Any]:
        if self._follow is not None:
            return self._follow.progress
        with self._lock:
            return dict(self._progress)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    def wait(self, timeout: Optional[float] = None) -> bool:
        if self._cancelled:
            return True
        return self._finished.wait(timeout)

    def join(self, timeout: Optional[float] = None) -> bool:
        """Wait for the worker itself to end, even after cancel() (wait() returns at once then):
        a cancelled job's thread may still be inside its model request."""
        return self._finished.wait(timeout)

    def cancel(self) -> bool:
        """Throw the outcome away. False if it was already applied (too late to cancel)."""
        if self._applied:
            return False
        self._cancelled = True
        return True

    def result(self) -> Any:
        if self._applied:
            return self._result
        if self._cancelled:
            return self._on_cancel
        self._finished.wait()
        if self._cancelled:  # cancelled while we waited (from another thread)
            return self._on_cancel
        source = self._follow if self._follow is not None else self
        if source is not self and source.cancelled:  # the followed job was thrown away
            self._cancelled = True
            return self._on_cancel
        self._applied = True
        if self._finish is not None:
            self._result = self._finish(source._value, source._error)
        elif source._error is not None:
            raise source._error
        else:
            self._result = source._value
        return self._result


def _wait_for(previous: Optional[Job], report=None) -> None:
    """Wait until an earlier generation's worker has ended. A cancelled job's thread may still be
    inside its model request; waiting keeps pygym at one request at a time, so a new request is
    not queued behind it on the model's clock. report({}) raises JobCancelled once the waiting
    job itself is cancelled."""
    if previous is None:
        return
    while not previous.join(0.1):
        if report is not None:
            report({})


def _prefetch_setting() -> bool:
    """config.Settings.prefetch (False if the settings can not be read)."""
    try:
        from . import config
        return bool(config.load().prefetch)
    except Exception:  # noqa: BLE001
        return False


MODES = ("preset", "ai")


class Session:
    def __init__(self, name: str, difficulty=None, mode: Optional[str] = None, *,
                 on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
                 prefetch: Optional[bool] = None, bigger: Optional[bool] = None):
        """mode: "preset" (bundled exercises, offline) or "ai" (generated by a local LLM, falls back
        to preset when generation fails). None = the saved setting (config.load().mode).

        difficulty: the starting level 1-3 (None = 1). on_progress: called with progress events
        while the FIRST exercise is generated (the menu shows a bar with it).

        prefetch: write the next exercise in the background as soon as one is shown, when the
        AI writes them (None = the saved setting, config.load().prefetch). Call close() when the
        session ends (a quit command does it itself).

        bigger: the answer to the bigger-task question, if the front-end already asked it (the
        menu does). For a function too small for advanced on its own (simple_target), a start at
        level 3 means: True -> advanced bigger tasks; False -> intermediate; None -> intermediate
        for now, and question() returns the question to ask at once."""
        self.name = name
        if mode is None:
            from . import config
            mode = config.load().mode
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
        self.mode = mode
        self.level = _valid_level(difficulty)  # the level of the next exercise (1-3)
        self.solved_at_level = 0  # exercises solved at this level (3 = one level up)
        self.notice = ""  # a one-line message front-ends show (e.g. AI warning or fallback note)
        self.seen: Set[str] = set()
        self.saved_code: Optional[str] = None  # the code at the last :w (None = never checked)
        self.last_passed: Optional[bool] = None  # result of the last :w (None = never checked)
        self.last_result: Optional[GradeResult] = None  # the last check, for :out
        self._job: Optional[Job] = None  # the background job loading the next exercise
        self._solved_this = False  # this exercise has passed a check already (counts once)
        self._revealed = False  # the solution of this exercise was shown
        self.prefetch = _prefetch_setting() if prefetch is None else bool(prefetch)
        self._prefetch: Optional[Job] = None  # the next exercise, being written in the background
        self._prefetch_level = 0  # the level it is written at
        self._last_worker: Optional[Job] = None  # the latest background generation (see _wait_for)
        self.closed = False
        # the bigger-task answer for this function (None = not asked yet), and why the question
        # waits for an answer ("" = it does not; "start" / "level" (:level 3) / "levelup")
        self.bigger_task: Optional[bool] = None if bigger is None else bool(bigger)
        self._asking = ""
        self._asked_now = False  # set by _count_solved when a level-up asks the question
        if self.level == MAX_LEVEL and self._needs_answer():
            self.level = MAX_LEVEL - 1  # never an advanced one-liner: intermediate until "yes"
            if self.bigger_task is None:
                self._asking = "start"
        ex, notice = self._produce(self.level, set(), on_progress)
        self._set_exercise(ex, notice)

    @property
    def difficulty(self) -> int:
        """The old name of `level` (kept for front-ends that read it)."""
        return self.level

    @difficulty.setter
    def difficulty(self, value) -> None:
        self.level = _valid_level(value)

    # ----- producing the next exercise (may run in a background thread: changes nothing) ------

    def needs_generation(self, level: Optional[int] = None) -> bool:
        """True if the next exercise will be written by the AI (it can then take up to 20 s):
        AI mode, or preset mode for a function without hand-written exercises."""
        from . import ai
        if ai.engine._turned_off():
            return False
        if self.mode == "ai":
            return True
        has_presets = getattr(self, "_has_presets", None)  # asked once per session
        if has_presets is None:
            try:
                has_presets = any(e.target_function == self.name for e in loader.load_all())
            except Exception:  # noqa: BLE001
                return False
            self._has_presets = has_presets
        return not has_presets

    def _produce(self, level: int, seen: Set[str], report=None) -> Tuple[Exercise, Optional[str]]:
        """The next exercise at `level` and the notice to show with it (None = keep the notice).
        Reads the session but never changes it. Raises loader.ExerciseUnavailable."""
        notice: Optional[str] = None
        why = ""
        if self.mode == "ai":
            ex, notice, why = self._generate(level, seen, report)
            if ex is not None:
                return ex, notice
        try:
            ex = loader.pick(self.name, level, seen)
        except loader.ExerciseUnavailable as e:
            # Not hard-coded: preset mode also falls back to the generator for such functions.
            ex = None
            if self.mode == "preset":
                ex, notice, why = self._generate(level, seen, report)
            if ex is None:
                if why:  # say why the AI could not help instead of only "no preset"
                    raise loader.ExerciseUnavailable(
                        f"No exercise for '{self.name}': there is no hand-written one, and the AI "
                        f"could not write one ({why}).") from e
                raise
        if self.mode == "ai" and report is not None:
            report({"stage": "fallback", "detail": FALLBACK_DETAIL})
        return ex, notice

    def _generate(self, level: int, seen: Set[str], report=None
                  ) -> Tuple[Optional[Exercise], str, str]:
        """Ask the generator: (exercise, notice, "") or, on any failure, (None, fallback notice,
        why). report (if given) receives the engine's progress events."""
        from . import ai
        kwargs: Dict[str, Any] = {"avoid_ids": set(seen)}
        if report is not None:
            kwargs["on_progress"] = report
        if level == MAX_LEVEL and self.bigger_task and self.is_simple:
            kwargs["bigger"] = True
        try:
            ex = ai.generate_exercise(self.name, level, **kwargs)
        except (NotImplementedError, ai.GenerationUnavailable) as e:
            why = str(e)
        except Exception as e:  # noqa: BLE001  (a broken generator must not end the lesson)
            why = f"{type(e).__name__}: {e}"
        else:
            return ex, ai.WARNING, ""
        return None, f"AI mode unavailable ({why}); using the preset exercises.", why

    def _set_exercise(self, ex: Exercise, notice: Optional[str]) -> None:
        self.exercise = ex
        self.seen.add(ex.id)
        if notice is not None:
            self.notice = notice
        self.saved_code = None
        self.last_passed = None
        self.last_result = None
        self._solved_this = False
        self._revealed = False
        self._start_prefetch()

    # ----- the bigger-task question -----------------------------------------------------------

    @property
    def is_simple(self) -> bool:
        """This function is too small for an advanced exercise on its own (simple_target)."""
        simple = getattr(self, "_is_simple", None)  # looked up once per session
        if simple is None:
            simple = self._is_simple = simple_target(self.name)
        return simple

    def bigger_applies(self) -> bool:
        """True if advanced exercises of this function are bigger tasks or nothing: it is simple
        and the AI writes its exercises (hand-written presets are used as they are)."""
        return self.is_simple and self.needs_generation(MAX_LEVEL)

    def _needs_answer(self) -> bool:
        """Going to advanced needs a "yes" first (not given yet, or "no")."""
        return self.bigger_task is not True and self.bigger_applies()

    @property
    def asking_bigger(self) -> bool:
        """True while the bigger-task question waits for an answer."""
        return bool(self._asking)

    def too_small_text(self) -> str:
        return f"`{self.name}` is too small for an advanced exercise on its own."

    def bigger_prompt(self) -> str:
        """The [Y/n] question a front-end asks when a Response has ask_bigger=True."""
        return BIGGER_PROMPT.format(name=self.name)

    def question(self) -> Optional[Response]:
        """The bigger-task question as a Response (ask_bigger=True) when a session that started
        at level 3 waits for it (front-ends show it right after the first exercise), else None."""
        if self._asking != "start":
            return None
        return Response(self.too_small_text(), "level", ask_bigger=True)

    def _set_level(self, level: int) -> None:
        """Change the level of the NEXT exercise (the current one stays); the prefetch follows."""
        self.level, self.solved_at_level = level, 0
        self._drop_prefetch()
        self._start_prefetch()

    def _stay_text(self, loading: bool) -> str:
        why = f"`{self.name}` alone is too small for advanced"
        if loading:
            return f"Intermediate {level_dots(2)} ({why}). Here is a new exercise."
        return f"Staying at intermediate {level_dots(2)}: {why}."

    def _bigger_plan(self, answer: Optional[bool]) -> Optional[int]:
        """The level :bigger <answer> loads an exercise at (None = it loads nothing)."""
        if answer is None or not self.bigger_applies():
            return None
        if answer:
            return None if self.level == MAX_LEVEL or self._asking == "levelup" else MAX_LEVEL
        wanted = self._asking in ("level", "start")  # the student asked for advanced
        return MAX_LEVEL - 1 if wanted and self.level < MAX_LEVEL - 1 else None

    def _bigger_command(self, arg: Optional[str], background: bool) -> Job:
        """:bigger yes|no: the answer to the bigger-task question (remembered)."""
        answer = _yes_no(arg)
        if answer is None:
            return Job.finished(Response("Answer :bigger yes or :bigger no.", "refused"))
        if not self.bigger_applies():
            return Job.finished(Response(
                f"`{self.name}` has advanced exercises of its own: :level 3 for one.", "refused"))
        plan = self._bigger_plan(answer)
        asking, self._asking = self._asking, ""
        self.bigger_task = answer
        if plan == MAX_LEVEL:
            return self._load(plan, self._level_changed_text(plan), background)
        if plan is not None:
            return self._load(plan, self._stay_text(loading=True), background)
        if answer:
            if self.level == MAX_LEVEL:
                return Job.finished(Response(
                    f"Advanced `{self.name}` exercises are bigger tasks.", "level"))
            self._set_level(MAX_LEVEL)  # the answer to a level-up question
            return Job.finished(Response(
                f"Level up! {self._advanced_name()}. :s for the next exercise.", "level"))
        if self.level == MAX_LEVEL:  # changed their mind: the next exercise is intermediate
            self._set_level(MAX_LEVEL - 1)
            return Job.finished(Response(self._stay_text(loading=False)
                                         + " :s for the next exercise.", "level"))
        if asking:
            return Job.finished(Response(self._stay_text(loading=False), "level"))
        return Job.finished(Response(f"Noted: `{self.name}` stays at intermediate "
                                     "(:bigger yes to change).", "level"))

    def _advanced_name(self) -> str:
        """"advanced ●●● (bin inside bigger tasks)" """
        bigger = ""
        if self.bigger_task and self.is_simple:
            bigger = f" ({self.name} inside bigger tasks)"
        return f"{LEVELS[MAX_LEVEL]} {level_dots(MAX_LEVEL)}{bigger}"

    # ----- prefetch: the next exercise, written while the student works ------------------------

    @property
    def next_ready(self) -> bool:
        """True if the next exercise was written in the background and :s will show it at once."""
        job = self._prefetch
        return (job is not None and self._prefetch_level == self.level and job.done
                and not job.cancelled)

    def _start_prefetch(self) -> None:
        """Start writing the next exercise (current level, everything seen so far avoided) in a
        background thread, if prefetch is on, the AI writes this function's exercises and none
        is being written yet. Never blocks; the session itself is not changed by the worker."""
        if self.closed or not self.prefetch or self._prefetch is not None:
            return
        if not self.needs_generation(self.level):
            return
        level, seen, previous = self.level, set(self.seen), self._last_worker

        def work(report):
            _wait_for(previous, report)
            return self._produce(level, seen, report)

        job = Job(work, None, background=True)
        self._prefetch, self._prefetch_level, self._last_worker = job, level, job

    def _drop_prefetch(self) -> None:
        """Throw the prefetched exercise away (its thread ends at its next step; a model request
        already sent finishes and is ignored)."""
        job, self._prefetch = self._prefetch, None
        if job is not None:
            job.cancel()

    def _prefetched(self, level: int) -> Optional[Job]:
        """The prefetch job for `level`, or None (one for another level is thrown away)."""
        job = self._prefetch
        if job is None:
            return None
        if self._prefetch_level != level or job.cancelled:
            self._drop_prefetch()
            return None
        return job

    def close(self) -> None:
        """The session is over: stop the background work (prefetch, a loading job). Threads are
        daemons, so a model request still on its way never keeps the program alive."""
        self.closed = True
        self._drop_prefetch()
        if self._job is not None and self._job.pending:
            self._job.cancel()

    # ----- the command interface (used by every front-end) -------------------------------

    @property
    def busy(self) -> bool:
        """True while a background job (start()) is writing the next exercise."""
        return self._job is not None and self._job.pending

    def handle(self, command: str, code: Optional[str] = None) -> Response:
        """Run one command against the student's current code.

        code=None means "no new code": the code from the last :w is used (or "" if never checked).
        Front-ends without a persistent code box (the line CLI, a Jupyter cell with only a command)
        pass None instead of re-implementing this rule themselves.

        Blocks until done (in AI mode :s can take up to 20 s): see start() for the same thing
        without blocking.
        """
        return self._dispatch(command, code, background=False).result()

    def start(self, command: str, code: Optional[str] = None) -> Job:
        """Like handle(), without blocking: returns a Job whose result() is the Response.

        Only a command that loads an AI-written exercise (:s, :level 2 in AI mode) runs in a
        background thread; every other command (and every preset exercise) is done at once
        (job.done is True right away, so a front-end shows no progress bar for it)."""
        return self._dispatch(command, code, background=True)

    def needs_wait(self, command: str) -> bool:
        """True if start(command) would run in the background: the command loads a new exercise
        (:s, or :level with another level) and the AI writes it. Lets a front-end call plain
        handle() for everything else."""
        if self.busy:
            return False
        name, arg = _split_command(command.strip())
        if name == ":s":
            return not self.next_ready and self.needs_generation(self.level)
        if name in (":level", ":lv") and arg is not None:
            new = parse_level(arg)
            if new == MAX_LEVEL and new != self.level and self._needs_answer():
                # asks first; after a "no" it loads intermediate when coming from beginner
                stay = self.bigger_task is False and self.level < MAX_LEVEL - 1
                return stay and self.needs_generation(MAX_LEVEL - 1)
            return new is not None and new != self.level and self.needs_generation(new)
        if name == ":bigger":
            plan = self._bigger_plan(_yes_no(arg))
            return plan is not None and self.needs_generation(plan)
        return False

    def start_next(self, level: Optional[int] = None) -> Job:
        """The next exercise (at `level`; None = the current level) in a background thread.
        job.result() is the Response of :s (or of :level when the level changes)."""
        if self.busy:
            return Job.finished(Response(BUSY, "refused"))
        if level is None or _valid_level(level) == self.level:
            return self._load(None, "Here is another one.", background=True)
        return self._level_command(str(_valid_level(level)), background=True)

    def _dispatch(self, command: str, code: Optional[str], background: bool) -> Job:
        cmd = command.strip()
        if self.busy:
            return Job.finished(Response(BUSY, "refused"))
        name, arg = _split_command(cmd)
        if name == ":s":
            return self._load(None, "Skipped. Here is another one.", background)
        if name in (":level", ":lv"):
            return self._level_command(arg, background)
        if name == ":bigger":
            return self._bigger_command(arg, background)
        resp = self._simple(cmd, code)
        if resp.quit:
            self.close()
        else:
            self._start_prefetch()  # e.g. after a cancelled :level, which threw the old one away
        return Job.finished(resp)

    def _load(self, level: Optional[int], text: str, background: bool) -> Job:
        """A Job that loads the next exercise (at `level`, None = the current one): the
        prefetched one when there is one for that level, else a new one."""
        target = self.level if level is None else level
        pre = self._prefetched(target)
        if pre is not None:
            return self._take_prefetch(pre, level, text, background)
        seen = set(self.seen)
        generating = self.needs_generation(target)
        threaded = background and generating
        previous = self._last_worker if generating else None

        def work(report):
            _wait_for(previous, report if threaded else None)
            return self._produce(target, seen, report if threaded else None)

        def finish(value, error):
            return self._loaded(level, text, value, error, threaded)

        job = Job(work, finish, background=threaded, on_cancel=Response(CANCELLED, "refused"))
        if threaded:
            self._job = self._last_worker = job
        return job

    def _take_prefetch(self, pre: Job, level: Optional[int], text: str, background: bool) -> Job:
        """:s with a prefetch: done at once if it is ready, else a Job that follows it (the same
        model request, its progress). Cancelling that Job keeps the prefetch running."""
        def finish(value, error):
            if self._prefetch is pre:
                self._prefetch = None
            resp = self._loaded(level, text, value, error, True)
            self._start_prefetch()  # not shown (refused): write another one for the next :s
            return resp

        job = Job(finish=finish, on_cancel=Response(CANCELLED, "refused"), follow=pre)
        if background and not job.done:
            self._job = job
        return job

    def _loaded(self, level: Optional[int], text: str, value, error, threaded: bool) -> Response:
        """Apply a loaded exercise (value = (exercise, notice)) or say why there is none."""
        if error is not None:
            if isinstance(error, loader.ExerciseUnavailable):
                # Only for a function without presets whose generation failed this time.
                return Response(NO_OTHER, "refused")
            if not threaded:
                raise error
            return Response(f"Could not load another exercise ({type(error).__name__}: "
                            f"{error}). Keep going with this one.", "refused")
        ex, notice = value
        if level is not None and level != self.level:
            self.level, self.solved_at_level = level, 0
            self._drop_prefetch()  # written for the old level
        self._set_exercise(ex, notice)
        return Response(text, "exercise", clear_code=True)

    def _level_command(self, arg: Optional[str], background: bool) -> Job:
        if arg is None:
            return Job.finished(Response(self.level_text(), "level"))
        new = parse_level(arg)
        if new is None:
            # short: it has to fit the editor's message line (:intro names the levels)
            return Job.finished(Response(
                f"'{arg}' is not a level. Use :level 1, 2 or 3 (or b, i, a).", "refused"))
        if new == self.level:
            return Job.finished(Response(
                f"You are already at {LEVELS[new]} {level_dots(new)}.", "level"))
        if new == MAX_LEVEL and self._needs_answer():
            if self.bigger_task is None:  # ask first: bigger tasks, or stay at intermediate
                self._asking = "level"
                return Job.finished(Response(self.too_small_text(), "level", ask_bigger=True))
            if self.level == MAX_LEVEL - 1:  # answered "no" earlier
                return Job.finished(Response(self._stay_text(loading=False)
                                             + " (:bigger yes to change)", "level"))
            return self._load(MAX_LEVEL - 1, self._stay_text(loading=True), background)
        return self._load(new, self._level_changed_text(new), background)

    def _level_changed_text(self, level: int) -> str:
        if level == MAX_LEVEL:
            return f"Level: {self._advanced_name()}. Here is a new exercise."
        return f"Level: {LEVELS[level]} {level_dots(level)}. Here is a new exercise."

    def level_text(self) -> str:
        """What :level says: "Level: beginner ●○○ — 2 of 3 solved to level up. ..." """
        here = f"Level: {LEVELS[self.level]} {level_dots(self.level)}"
        if self.level >= MAX_LEVEL:
            return f"Level: {self._advanced_name()} (the highest). :level 1-3 to change"
        if self.level == MAX_LEVEL - 1 and self.bigger_task is False and self.bigger_applies():
            return (f"{here} (the highest for `{self.name}`; :bigger yes for bigger tasks). "
                    ":level 1-3 to change")
        return (f"{here} — {self.solved_at_level} of {LEVEL_UP_AFTER} solved to level up. "
                f":level 1-3 to change")

    def _simple(self, cmd: str, code: Optional[str]) -> Response:
        """Every command that does not load a new exercise."""
        if code is None:
            code = self.saved_code or ""
        if cmd == ":w":
            return self._check(code, then_quit=False)
        if cmd == ":wq":
            return self._check(code, then_quit=True)
        if cmd == ":q":
            # 1. unchecked changes win: the new code might be right, so don't offer the solution yet
            if self.is_dirty(code):
                return Response("Unchecked changes: :w to check them, "
                                "or :q! to quit without checking.", "refused")
            # 2. the last check failed: stay and offer the solution, like a failed :wq
            if self.last_passed is False:
                return Response("Your last check didn't pass. "
                                "Fix it and :w, :s to skip, or :q! to quit.",
                                "refused", ask_reveal=True)
            # 3. passed, or 4. nothing attempted yet
            return Response("Bye!", "quit", quit=True)
        if cmd == ":q!":
            return Response("Bye! (code not checked)", "quit", quit=True)
        if cmd == ":h":
            return Response(self.hint(), "hint")
        if cmd == ":help":
            return Response(self.help(), "help")
        if cmd == ":solution":
            return Response(self.solution(), "solution")
        if cmd == ":out":
            return self._results(code)
        if cmd == ":intro":
            return Response(commands_text(), "commands")
        # "Try:" not "Commands:": with :out the list must still fit an 80-column message line
        return Response(f"Unknown command '{cmd}'. Try: {_TRY}", "unknown")

    def is_dirty(self, code: str) -> bool:
        """True if the code changed since the last :w (empty code that was never checked is clean)."""
        return _normalize(code) != _normalize(self.saved_code)

    def _results(self, code: str) -> Response:
        result = self.last_result
        if result is None:
            return Response(NOTHING_CHECKED, "results")
        if self.is_dirty(code):
            return Response(OLD_RESULTS, "results", result=result)
        return Response(f"Last check: {result.message}", "results", result=result)

    def _count_solved(self) -> bool:
        """A passing check: count it (once per exercise, not after the solution was shown).
        True when that was the LEVEL_UP_AFTER-th one and the level went up."""
        if self._solved_this:
            return False
        self._solved_this = True
        if self._revealed:
            return False
        self.solved_at_level += 1
        if self.solved_at_level >= LEVEL_UP_AFTER and self.level < MAX_LEVEL:
            if self.level + 1 == MAX_LEVEL and self._needs_answer():
                # too small for advanced on its own: ask first (or stay, after a "no")
                self.solved_at_level = 0
                if self.bigger_task is None:
                    self._asking, self._asked_now = "levelup", True
                return False
            self.level += 1
            self.solved_at_level = 0
            return True
        return False

    def _check(self, code: str, then_quit: bool) -> Response:
        result = self.submit(code)
        self.saved_code = code
        self.last_passed = result.passed
        self.last_result = result
        if result.passed:
            level_up = self._count_solved()
            asked, self._asked_now = self._asked_now, False
            if level_up:  # the prefetched exercise is for the old level
                self._drop_prefetch()
                self._start_prefetch()
            up = f" Level up! Next exercise: {LEVELS[self.level]} {level_dots(self.level)}"
            if then_quit:
                return Response(f"Passed! {result.message}" + (up if level_up else ""), "pass",
                                quit=True, result=result)
            if asked:  # the next level would be advanced: ask how (bigger tasks or stay)
                # kept short: it has to fit the editor's message line
                return Response(f"Passed! {result.message} `{self.name}` alone is too small "
                                "for advanced.", "pass", result=result, ask_bigger=True)
            if level_up:  # kept short: it has to fit the editor's message line
                return Response(f"Passed! {result.message}{up} (:s)", "pass", result=result)
            return Response(f"Passed! {result.message} Type :s for another exercise or :q to quit.",
                            "pass", result=result)
        if then_quit:
            return Response(f"Failed: {result.message}", "fail", ask_reveal=True, result=result)
        return Response(f"Failed: {result.message}", "fail", result=result)

    # ----- the building blocks (also used directly by the tkinter popup) -----------------

    def submit(self, code: str) -> GradeResult:
        result = grader.grade(code, self.exercise)
        progress.record(self.exercise.id, result.passed)
        return result

    def hint(self) -> str:
        return self.exercise.hint or "No hint for this one. Re-read the question carefully."

    def help(self) -> str:
        name = self.exercise.target_function
        if "." in name:  # "str.join", "statistics.median": the same lookup the AI engine uses
            target = None
            try:
                from .ai import targets
                target = targets.target_object(targets.resolve(name))
            except Exception:  # unknown or not allowed: no documentation, never a crash
                pass
            doc = inspect.getdoc(target) if target is not None else None
            if doc:
                sig = _signature(target)
                return f"{name}{sig}\n\n{doc}" if sig else doc
        else:
            target = getattr(builtins, name, None)
            doc = target.__doc__ if target is not None else None
        if not doc:
            return f"No documentation available for '{name}'."
        return doc.strip()

    def solution(self) -> str:
        """The reference solution. Showing it means solving this exercise no longer counts
        towards the next level."""
        self._revealed = True
        return self.exercise.reference_solution

    def skip(self) -> Exercise:
        """Load the next exercise now (blocking; the prefetched one if there is one). Raises
        loader.ExerciseUnavailable."""
        pre = self._prefetched(self.level)
        value = None
        if pre is not None:
            self._prefetch = None
            try:
                value = pre.result()  # waits for it; raises what the worker raised
            except BaseException:
                self._start_prefetch()  # try again for the next skip
                raise
        if value is None:
            _wait_for(self._last_worker)
            value = self._produce(self.level, set(self.seen))
        ex, notice = value
        self._set_exercise(ex, notice)
        return self.exercise
