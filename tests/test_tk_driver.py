"""The tkinter popup's logic, with a stand-in for tkinter (CI has no display; Windows has one).

Nothing tested run_tk before. The stand-in keeps just what run_tk uses; the real window is checked
on a real machine with tools/real_machine_check.py --tk.
"""
import sys
import types

import pytest

from pygym import ui
from pygym.session import Session


class Widget:
    def __init__(self, master=None, **options):
        self.options = dict(options)

    def pack(self, **kw):
        pass

    def config(self, **options):
        self.options.update(options)

    def cget(self, key):
        return self.options.get(key)


class Button(Widget):
    def invoke(self):
        self.options["command"]()


class Text(Widget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.content = ""

    def delete(self, start, end):
        self.content = ""

    def insert(self, index, text):
        self.content = text + self.content

    def get(self, start, end):
        return self.content + "\n"           # a real Tk text widget ends with a newline


class Root(Widget):
    def __init__(self):
        super().__init__()
        self.later, self.alive, self.heading = [], True, ""

    def title(self, text):
        self.heading = text

    def after(self, ms, callback):
        self.later.append(callback)

    def update(self):
        pass

    def destroy(self):
        self.alive = False

    def mainloop(self):
        while self.alive and self.later:
            self.later.pop(0)()


@pytest.fixture
def tk(monkeypatch, tmp_path):
    monkeypatch.setenv("PYGYM_HOME", str(tmp_path))
    module, scrolled = types.ModuleType("tkinter"), types.ModuleType("tkinter.scrolledtext")
    module.Tk, module.Label, module.Frame, module.Button = Root, Widget, Widget, Button
    scrolled.ScrolledText = Text
    module.scrolledtext = scrolled
    monkeypatch.setitem(sys.modules, "tkinter", module)
    monkeypatch.setitem(sys.modules, "tkinter.scrolledtext", scrolled)


def drive(script):
    session = Session("zip", 1)
    seen = {}

    def driver(root, box, status, buttons):
        try:
            script(session, root, box, status, buttons, seen)
        finally:
            root.destroy()

    ui.run_tk(session, _driver=driver)
    return session, seen


def test_the_window_shows_the_exercise_and_offers_four_buttons(tk):
    def script(session, root, box, status, buttons, seen):
        seen["buttons"] = list(buttons)
        seen["code"] = box.content
        seen["title"] = root.heading
    session, seen = drive(script)
    assert seen["buttons"] == ["Submit", "Hint", "Show solution", "Skip"]
    assert seen["code"].startswith(session.exercise.function_signature) and seen["title"] == "pygym"


def test_wrong_then_right_code_with_chinese_and_accents_in_a_comment(tk):
    def script(session, root, box, status, buttons, seen):
        ex = session.exercise
        box.delete("1.0", "end")
        box.insert("1.0", ex.function_signature + "\n    return {}\n")
        buttons["Submit"].invoke()
        seen["wrong"] = (status.cget("text"), status.cget("fg"))
        box.delete("1.0", "end")
        box.insert("1.0", ex.reference_solution + "\n# 中文 à é\n")
        buttons["Submit"].invoke()
        seen["right"] = (status.cget("text"), status.cget("fg"))
    _, seen = drive(script)
    assert seen["wrong"][0].startswith("Failed.") and seen["wrong"][1] == "red"
    assert seen["right"][0].startswith("Passed!") and seen["right"][1] == "green"


def test_hint_solution_and_skip(tk):
    def script(session, root, box, status, buttons, seen):
        buttons["Hint"].invoke()
        seen["hint"] = status.cget("text")
        buttons["Show solution"].invoke()
        seen["solution"] = box.content
        buttons["Skip"].invoke()
        seen["skipped"] = True
    session, seen = drive(script)
    assert seen["hint"].startswith("Hint: ") and len(seen["hint"]) > 8
    assert "def " in seen["solution"] and seen["skipped"]
