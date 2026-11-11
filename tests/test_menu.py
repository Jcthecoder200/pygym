"""The main menu and the `pygym` command line. No LLM, no real terminal, no network."""
import io
import subprocess
import sys

import pytest
from rich.console import Console

from pygym import ai, config, menu, style
from pygym.loader import ExerciseUnavailable


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    monkeypatch.setenv("PYGYM_NO_AI", "1")
    monkeypatch.setattr(ai, "available", lambda: False)


def make_console(width=100):
    return Console(file=io.StringIO(), width=width, color_system=None, force_terminal=False,
                   highlight=False, legacy_windows=False)


def output(console):
    return console.file.getvalue()


class Script:
    """input_fn that answers from a list; EOF when it runs out. Remembers the prompts."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class Starts:
    """start_fn that records the Choice objects."""

    def __init__(self, result=0):
        self.choices = []
        self.result = result

    def __call__(self, choice, console):
        self.choices.append(choice)
        return self.result


def run(*answers, **kw):
    console = make_console()
    script = Script(*answers)
    starts = kw.pop("starts", None) or Starts()
    rc = menu.run_menu(console=console, input_fn=script, start_fn=starts)
    return rc, console, script, starts


# ----- level parsing ----------------------------------------------------------------------


@pytest.mark.parametrize("text,level", [
    ("1", 1), ("2", 2), ("3", 3), ("b", 1), ("i", 2), ("a", 3), ("Beginner", 1),
    ("INTERMEDIATE", 2), (" advanced ", 3), ("inter", 2), ("adv", 3),
])
def test_parse_level_accepts(text, level):
    assert menu.parse_level(text) == level


@pytest.mark.parametrize("text", ["", "0", "4", "x", "expert", "12", None, "ab"])
def test_parse_level_rejects(text):
    assert menu.parse_level(text) is None


def test_clean_target():
    assert menu.clean_target(" zip() ") == "zip"
    assert menu.clean_target("builtins.zip") == "zip"
    assert menu.clean_target("statistics.median") == "statistics.median"


# ----- the library -> function -> level flow ------------------------------------------------


def test_full_flow_starts_with_the_right_choice():
    rc, console, script, starts = run("builtins", "zip", "2", "q")
    assert rc == 0
    assert [(c.target, c.level, c.mode, c.ui) for c in starts.choices] == [("zip", 2, "preset", "auto")]
    assert script.prompts[:3] == ["library [builtins] › ", "function › ", "level [beginner] › "]
    out = output(console)
    assert "Starting zip · intermediate · preset" in out


def test_menu_returns_home_after_an_exercise_and_remembers_choices():
    rc, console, script, starts = run("builtins", "zip", "3", "", "")
    # 2nd round: library Enter = builtins (remembered), then EOF
    assert starts.choices[0].level == 3
    assert config.load().level == 3 and config.load().library == "builtins"
    assert output(console).count("Practice one Python function at a time.") == 2
    assert script.prompts[3] == "library [builtins] › "


def test_enter_at_every_prompt_uses_the_defaults():
    rc, console, script, starts = run("", "zip", "")
    assert [(c.target, c.level) for c in starts.choices] == [("zip", 1)]


def test_library_by_number():
    rc, console, script, starts = run("1", "sorted", "a")
    assert [(c.target, c.level) for c in starts.choices] == [("sorted", 3)]


def test_bad_library_number_asks_again():
    rc, console, script, starts = run("99", "1", "zip", "1")
    assert "no library number 99" in output(console).lower()
    assert starts.choices[0].target == "zip"


def test_other_library_function_gets_a_dotted_target():
    rc, console, script, starts = run("statistics", "median", "2")
    assert [(c.target, c.level) for c in starts.choices] == [("statistics.median", 2)]
    assert "median" in output(console)


def test_type_library_gives_method_targets():
    rc, console, script, starts = run("str", "join", "1")
    assert starts.choices[0].target == "str.join"


def test_full_name_skips_the_function_step():
    rc, console, script, starts = run("statistics.median", "b")
    assert starts.choices[0].target == "statistics.median"
    assert "function › " not in script.prompts


def test_a_function_name_at_the_library_prompt_works():
    rc, console, script, starts = run("zip", "1")
    assert starts.choices[0].target == "zip"


def test_function_prompt_back_returns_to_library_prompt():
    rc, console, script, starts = run("math", "b", "builtins", "zip", "1")
    assert script.prompts[:4] == ["library [builtins] › ", "function › ",
                                  "library [builtins] › ", "function › "]
    assert starts.choices[0].target == "zip"


def test_level_back_returns_home():
    rc, console, script, starts = run("builtins", "zip", "back", "q")
    assert starts.choices == []
    assert output(console).count("Practice one Python function at a time.") == 2


def test_bad_level_asks_again():
    rc, console, script, starts = run("zip", "expert", "2")
    assert "not a level" in output(console)
    assert starts.choices[0].level == 2


def test_function_prompt_shows_preset_stars_and_levels():
    rc, console, script, starts = run("builtins", "q")
    out = output(console)
    assert "hand-checked exercises" in out and "zip" in out and "●●" in out
    assert "AI only" in out


def no_preset_builtin():
    """A built-in that has no hand-written exercise (the dataset grows, so look it up)."""
    return next(n for n in menu.functions_of("builtins") if n not in menu.preset_levels())


def test_level_screen_says_when_there_is_no_preset():
    name = no_preset_builtin()
    rc, console, script, starts = run(name, "1")
    assert "no hand-checked exercise" in output(console)
    assert starts.choices[0].target == name


# ----- unknown names -----------------------------------------------------------------------


def test_unknown_function_shows_suggestions_and_stays():
    rc, console, script, starts = run("builtins", "zi", "zip", "1")
    out = output(console)
    assert "No function 'zi' in builtins." in out and "Did you mean: zip" in out
    assert script.prompts.count("function › ") == 2
    assert starts.choices[0].target == "zip"


def test_unknown_library_says_so_and_asks_again():
    rc, console, script, starts = run("nosuchlibrary_xyz", "math", "b")
    out = output(console)
    assert "No library called 'nosuchlibrary_xyz'" in out
    assert script.prompts[1] == "library [builtins] › "
    assert starts.choices == []


def test_misspelt_library_gets_a_suggestion():
    rc, console, script, starts = run("statistcs", "q")
    assert "Did you mean: statistics" in output(console)


def test_unknown_dotted_name_is_a_friendly_message():
    rc, console, script, starts = run("statistics.medain", "q")
    assert "Traceback" not in output(console)
    assert starts.choices == []


def test_import_side_effect_libraries_are_never_imported(monkeypatch):
    real = menu.importlib.import_module

    def guarded(name, *a, **k):
        if name in ("antigravity", "this"):
            pytest.fail("must not import " + name)
        return real(name, *a, **k)

    monkeypatch.setattr(menu.importlib, "import_module", guarded)
    rc, console, script, starts = run("antigravity", "this", "q")
    assert starts.choices == []
    assert "not a library pygym can use" in output(console)


def test_module_that_fails_to_import_is_handled(monkeypatch):
    real = menu.importlib.import_module
    real_spec = menu.importlib.util.find_spec

    def boom(name, *a, **k):
        if name == "brokenlib":
            raise SystemExit(3)
        return real(name, *a, **k)

    monkeypatch.setattr(menu.importlib, "import_module", boom)
    monkeypatch.setattr(menu.importlib.util, "find_spec",
                        lambda name: object() if name == "brokenlib" else real_spec(name))
    rc, console, script, starts = run("brokenlib", "q")
    assert "could not be imported" in output(console)


# ----- mode, level and window switches ------------------------------------------------------


def test_m_toggles_the_mode_and_saves_it():
    rc, console, script, starts = run("m")
    assert config.load().mode == "ai"
    out = output(console)
    assert "Mode: AI." in out and ai.WARNING in out
    assert "fall back to the preset" in out          # the AI is offline in these tests
    assert "○ preset   ● AI" in out                   # the home screen after the switch
    assert rc == 0


def test_m_twice_goes_back_to_preset():
    run("m", "m")
    assert config.load().mode == "preset"


def test_mode_switch_applies_to_the_next_start():
    rc, console, script, starts = run("m", "zip", "1")
    assert starts.choices[0].mode == "ai"


def test_m_works_at_the_function_and_level_prompts():
    rc, console, script, starts = run("builtins", "m", "zip", "m", "1")
    assert starts.choices[0].mode == "preset"        # switched twice
    rc, console, script, starts = run("builtins", "m", "zip", "1")
    assert starts.choices[0].mode == "ai"


def test_ai_ready_shows_ready_line(monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    rc, console, script, starts = run("m")
    out = output(console)
    assert "✓ ready" in out
    assert "fall back" not in out


def test_ai_mode_with_offline_ai_says_it_uses_presets():
    config.save(config.Settings(mode="ai"))
    rc, console, script, starts = run("q")
    out = output(console)
    assert "✗ offline" in out
    assert "AI mode will use the preset exercises until the AI is ready." in out


def test_l_changes_the_default_level():
    rc, console, script, starts = run("l", "3", "zip", "")
    assert config.load().level == 3
    assert starts.choices[0].level == 3


def test_u_cycles_the_window_and_saves():
    run("u")
    assert config.load().ui == "editor"
    rc, console, script, starts = run("zip", "1")
    assert starts.choices[0].ui == "editor"


def test_help_and_quit_keys():
    rc, console, script, starts = run("?", "q")
    assert "switch between preset and AI mode" in output(console)
    assert rc == 0 and "Bye!" in output(console)


def test_save_problem_is_reported_not_raised(monkeypatch):
    def fail(settings):
        raise OSError("read-only")
    monkeypatch.setattr(config, "save", fail)
    rc, console, script, starts = run("m", "q")
    assert "Could not save your settings" in output(console) and rc == 0


# ----- leaving cleanly ------------------------------------------------------------------------


@pytest.mark.parametrize("first", [EOFError(), KeyboardInterrupt()])
@pytest.mark.parametrize("depth", [[], ["builtins"], ["builtins", "zip"]])
def test_ctrl_c_and_ctrl_d_exit_cleanly_at_any_prompt(first, depth):
    rc, console, script, starts = run(*depth, first)
    assert rc == 0 and starts.choices == []
    assert "Bye!" in output(console)


def test_prompts_are_not_called_after_quit():
    rc, console, script, starts = run("q", "zip")
    assert script.answers == ["zip"]


# ----- the home panel --------------------------------------------------------------------------


def test_home_panel_text():
    s = config.Settings(mode="ai", level=2, model="qwen2.5:1.5b", ui="text")
    text = style.to_plain(menu.home_panel(s, True))
    assert "○ preset   ● AI" in text and "✓ ready" in text and "qwen2.5:1.5b" in text
    assert "●●○ intermediate" in text and "text" in text
    assert ai.WARNING in text
    off = style.to_plain(menu.home_panel(s, False))
    assert "✗ offline" in off and "until the AI is ready" in off


def test_home_panel_in_preset_mode_has_no_ai_warning():
    text = style.to_plain(menu.home_panel(config.Settings(), False))
    assert "● preset   ○ AI" in text and ai.WARNING not in text


def test_functions_panel_marks_presets():
    text = style.to_plain(menu.functions_panel("builtins", {"zip": [1, 2], "sum": [1, 2, 3]},
                                               names=["abs", "sum", "zip"]))
    assert "zip ●●○" in text and "sum ●●●" in text and "AI only" in text and "abs" in text


def test_preset_levels_comes_from_the_dataset():
    levels = menu.preset_levels()
    assert levels["zip"][0] == 1 and set(levels) >= {"zip", "sum", "sorted"}


# ----- start() ------------------------------------------------------------------------------------


class FakeSession:
    notice = ""

    def __init__(self, name, difficulty, mode):
        self.args = (name, difficulty, mode)


def test_start_runs_the_ui_with_the_session(monkeypatch):
    seen = {}
    monkeypatch.setattr(menu, "Session", FakeSession)
    monkeypatch.setattr(menu.ui, "run", lambda session, ui: seen.update(session=session, ui=ui))
    console = make_console()
    rc = menu.start(menu.Choice("zip", 2, "preset", "text"), console)
    assert rc == 0 and seen["ui"] == "text" and seen["session"].args == ("zip", 2, "preset")


def test_start_leaves_the_notice_to_the_front_end(monkeypatch):
    # The front-end (ui.run) shows Session.notice itself; printing it here too would show it
    # twice in the line CLI (and the editor clears the screen anyway).
    seen = []
    console = make_console()

    class Noisy(FakeSession):
        notice = ai.WARNING

    monkeypatch.setattr(menu, "Session", Noisy)
    monkeypatch.setattr(menu.ui, "run", lambda s, u: seen.append(s.notice))
    menu.start(menu.Choice("zip", 1, "ai", "text"), console)
    assert seen == [ai.WARNING]
    assert ai.WARNING not in output(console)


def test_start_passes_a_fallback_notice_to_the_front_end(monkeypatch):
    class Fallback(FakeSession):
        notice = "AI mode unavailable (Ollama is not running); using the preset exercises."

    seen = []
    monkeypatch.setattr(menu, "Session", Fallback)
    monkeypatch.setattr(menu.ui, "run", lambda s, u: seen.append(s.notice))
    menu.start(menu.Choice("zip", 1, "ai", "text"), make_console())
    assert "Ollama is not running" in seen[0]


def test_start_without_notice_prints_nothing_extra(monkeypatch):
    monkeypatch.setattr(menu, "Session", FakeSession)
    monkeypatch.setattr(menu.ui, "run", lambda s, u: None)
    console = make_console()
    menu.start(menu.Choice("zip", 1, "preset", "text"), console)
    assert output(console) == ""


@pytest.mark.parametrize("mode,tip", [("preset", "switch to AI mode"), ("ai", "no preset")])
def test_start_unavailable_is_friendly(monkeypatch, mode, tip):
    def refuse(*a, **k):
        raise ExerciseUnavailable("No exercise available for 'divmod' yet.")


    monkeypatch.setattr(menu, "Session", refuse)
    monkeypatch.setattr(menu.ui, "run", lambda s, u: pytest.fail("must not open a window"))
    console = make_console()
    rc = menu.start(menu.Choice("divmod", 1, mode, "text"), console)
    out = output(console)
    assert rc == 1 and "No exercise available for 'divmod'" in out and tip in out
    assert "Traceback" not in out


def test_start_with_a_real_session_in_the_text_window(monkeypatch):
    """Session + preset data end to end; the window itself is replaced."""
    seen = {}
    monkeypatch.setattr(menu.ui, "run", lambda s, u: seen.update(ex=s.exercise.id))
    rc = menu.start(menu.Choice("zip", 1, "preset", "text"), make_console())
    assert rc == 0 and seen["ex"].startswith("zip-")


def test_start_uses_a_progress_bar_only_when_the_ai_may_run(monkeypatch):
    # (was: a spinner; the owner asked for the same progress bar as :s instead)
    class ProgressSession(FakeSession):
        def __init__(self, name, difficulty, mode, on_progress=None):
            super().__init__(name, difficulty, mode)
            self.on_progress = on_progress

    monkeypatch.setattr(menu, "Session", ProgressSession)
    seen = []
    monkeypatch.setattr(menu.ui, "run", lambda s, u: seen.append(s))
    calls = []
    real_wait = style.wait_with_bar
    monkeypatch.setattr(style, "wait_with_bar",
                        lambda job, console: calls.append(job) or real_wait(job, console))

    menu.start(menu.Choice("zip", 1, "ai", "text"), make_console())       # AI offline: no bar
    assert calls == [] and seen[-1].on_progress is None
    monkeypatch.setattr(ai, "available", lambda: True)
    menu.start(menu.Choice("zip", 1, "preset", "text"), make_console())   # preset exists: no bar
    assert calls == []
    console = make_console()
    menu.start(menu.Choice("zip", 1, "ai", "text"), console)
    assert len(calls) == 1 and "qwen2.5:1.5b" in output(console)
    assert callable(seen[-1].on_progress)  # the first generation reports to the bar


def test_start_cancelled_while_writing_goes_back_to_the_menu(monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(menu.ui, "run", lambda s, u: pytest.fail("must not open a window"))

    def ctrl_c(job, console):
        job.cancel()
        return False

    monkeypatch.setattr(style, "wait_with_bar", ctrl_c)
    console = make_console()
    assert menu.start(menu.Choice("zip", 1, "ai", "text"), console) == 1
    assert "Cancelled." in output(console)


# ----- the command line ---------------------------------------------------------------------------


def cli(*argv, input_fn=None):
    console = make_console()
    starts = Starts()
    rc = menu.main(list(argv), console=console, input_fn=input_fn, start_fn=starts)
    return rc, console, starts


def test_target_with_flags_starts_directly():
    rc, console, starts = cli("zip", "--level", "beginner", "--mode", "ai", "--ui", "text")
    assert rc == 0
    assert [(c.target, c.level, c.mode, c.ui) for c in starts.choices] == [("zip", 1, "ai", "text")]


def test_target_without_flags_uses_the_saved_settings():
    config.save(config.Settings(mode="ai", level=3, ui="tk"))
    rc, console, starts = cli("zip")
    assert [(c.level, c.mode, c.ui) for c in starts.choices] == [(3, "ai", "tk")]


def test_flags_apply_to_this_run_only():
    cli("zip", "-l", "3", "--mode", "ai")
    assert config.load() == config.Settings()


def test_dotted_target_and_short_level():
    rc, console, starts = cli("statistics.median", "-l", "i")
    assert [(c.target, c.level) for c in starts.choices] == [("statistics.median", 2)]


def test_target_cleaning():
    rc, console, starts = cli("builtins.zip()")
    assert starts.choices[0].target == "zip"


def test_start_result_becomes_the_exit_code():
    console = make_console()
    assert menu.main(["zip"], console=console, start_fn=Starts(result=1)) == 1


def test_unknown_function_exits_1_with_suggestions():
    rc, console, starts = cli("zipp")
    assert rc == 1 and starts.choices == []
    assert "No function 'zipp' in builtins." in output(console) and "zip" in output(console)


def test_unknown_library_function_exits_1():
    rc, console, starts = cli("nosuchlibrary_xyz.f")
    assert rc == 1 and "No library called" in output(console)


def test_bad_level_is_exit_code_2(capsys):
    assert menu.main(["zip", "--level", "expert"], console=make_console()) == 2
    assert "not a level" in capsys.readouterr().err


def test_bad_mode_is_exit_code_2(capsys):
    assert menu.main(["--mode", "robot"], console=make_console()) == 2


def test_mode_alone_is_saved_and_exits():
    rc, console, starts = cli("--mode", "ai")
    assert rc == 0 and starts.choices == [] and config.load().mode == "ai"
    cli("--mode", "preset")
    assert config.load().mode == "preset"


def test_level_and_ui_alone_are_saved():
    cli("--level", "advanced", "--ui", "text")
    s = config.load()
    assert (s.level, s.ui) == (3, "text")


def test_set_mode_and_set_model():
    rc, console, starts = cli("--set-mode", "ai", "--set-model", "qwen2.5:3b")
    s = config.load()
    assert rc == 0 and (s.mode, s.model) == ("ai", "qwen2.5:3b")


def test_empty_model_is_rejected():
    assert cli("--set-model", " ")[0] == 2
    assert config.load().model == config.Settings().model


def test_version(capsys):
    assert menu.main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("pygym ")


def test_help_mentions_the_options(capsys):
    assert menu.main(["--help"]) == 0
    out = capsys.readouterr().out
    for word in ("--level", "--mode", "--ui", "--list", "--status", "FUNCTION"):
        assert word in out


def test_list_libraries_and_functions():
    rc, console, starts = cli("--list")
    assert rc == 0 and "statistics" in output(console)
    rc, console, starts = cli("--list", "builtins")
    assert rc == 0 and "zip ●" in output(console) and "divmod" in output(console)
    rc, console, starts = cli("--list", "nosuchlibrary_xyz")
    assert rc == 1


def test_status_says_whether_ai_is_ready(monkeypatch):
    rc, console, starts = cli("--status")
    assert rc == 0 and "not reachable" in output(console) and "qwen2.5:1.5b" in output(console)
    monkeypatch.setattr(ai, "available", lambda: True)
    rc, console, starts = cli("--status")
    assert "ready" in output(console) and "not reachable" not in output(console)


def test_no_arguments_opens_the_menu():
    script = Script("q")
    console = make_console()
    assert menu.main([], console=console, input_fn=script) == 0
    assert script.prompts == ["library [builtins] › "]
    assert "Practice one Python function at a time." in output(console)


# ----- main() never leaks a traceback ---------------------------------------------------------------


def test_main_menu_ctrl_d_is_clean():
    console = make_console()
    assert menu.main([], console=console, input_fn=Script()) == 0
    assert "Bye!" in output(console)


# ----- the real thing in a child process --------------------------------------------------------------


def _python_m_pygym(*args, stdin=""):
    # -B: -I also ignores PYTHONDONTWRITEBYTECODE, so say "no .pyc files" as a flag
    return subprocess.run([sys.executable, "-I", "-B", "-m", "pygym", *args], input=stdin,
                          capture_output=True, text=True, timeout=60)


def test_python_dash_m_pygym_help_and_version(tmp_path, monkeypatch):
    # -I ignores PYTHON* variables, so the package must be importable on its own (editable install)
    r = _python_m_pygym("--version")
    assert r.returncode == 0 and r.stdout.startswith("pygym ")
    r = _python_m_pygym("--help")
    assert r.returncode == 0 and "usage: pygym" in r.stdout


def test_python_dash_m_pygym_first_screen_and_quit(tmp_path):
    r = _python_m_pygym(stdin="q\n")
    assert r.returncode == 0, r.stderr
    assert "Practice one Python function at a time." in r.stdout and "Bye!" in r.stdout
    assert "Traceback" not in r.stderr
