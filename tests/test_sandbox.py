"""pygym.sandbox + the audit hook in _runner: what exercise code can and can't touch.

Every behaviour is checked at the "audit-hook" level (all platforms) and, when bubblewrap works
on this machine, at the "bwrap" level too. Nothing here needs the network or a model.
"""
import os
import subprocess
import sys
import tempfile

import pytest

from pygym import grader, sandbox
from pygym._runner import Guard, write_files

HAS_BWRAP = sandbox.level() == "bwrap" or (
    sys.platform.startswith("linux") and sandbox._best.get("level") == "bwrap")
LEVELS = ["audit-hook"] + (["bwrap"] if HAS_BWRAP else [])
needs_bwrap = pytest.mark.skipif(not HAS_BWRAP, reason="bubblewrap is not usable here")


def run(code, inputs=((),), lv="audit-hook", target="len", imports=(), **kw):
    return grader.run_in_sandbox(code, "f", target, [list(i) for i in inputs], 10.0,
                                 allowed_imports=imports, level=lv, **kw)


def values(res):
    assert res["status"] == "ok", res
    return [o.get("value", o.get("error")) for o in res["outputs"]]


# ----- writing files ------------------------------------------------------------------------


@pytest.mark.parametrize("lv", LEVELS)
def test_writing_inside_the_folder_works(lv):
    code = ("def f():\n    len('x')\n    with open('out.txt', 'w') as fh:\n        fh.write('hi')\n"
            "    with open('out.txt') as fh:\n        return fh.read()")
    assert values(run(code, lv=lv, files={"seed.txt": ""})) == ["hi"]


@pytest.mark.parametrize("lv", LEVELS)
@pytest.mark.parametrize("where", ["../x.txt", "~/pygym-test-x.txt", "ABS"])
def test_writing_outside_the_folder_is_blocked(lv, where, tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    outside = tmp_path / "outside.txt"
    path = str(outside) if where == "ABS" else where
    code = ("import os\n"
            "def f():\n"
            f"    p = os.path.expanduser({path!r})\n"
            "    with open(p, 'w') as fh:\n"
            "        fh.write('oops')\n"
            "    return 'written'\n")
    res = run(code, lv=lv, target="os.getcwd", imports=["os"], files={"a.txt": "a"})
    assert res["status"] == "ok"
    out = res["outputs"][0]
    assert "blocked" in out.get("error", ""), out
    assert any("writing to" in b for b in res["blocked"])
    assert not outside.exists() and not (fake_home / "pygym-test-x.txt").exists()


@pytest.mark.parametrize("lv", LEVELS)
def test_deleting_outside_the_folder_is_blocked(lv, tmp_path):
    victim = tmp_path / "keep.txt"
    victim.write_text("precious")
    folder = tmp_path / "folder"
    folder.mkdir()
    code = f"import os\ndef f():\n    os.remove({str(victim)!r})\n    return 'gone'\n"
    assert "blocked" in values(run(code, lv=lv, target="os.remove", imports=["os"]))[0]
    code = f"import shutil\ndef f():\n    shutil.rmtree({str(folder)!r})\n    return 'gone'\n"
    assert "blocked" in values(run(code, lv=lv, target="shutil.rmtree", imports=["shutil"]))[0]
    assert victim.read_text() == "precious" and folder.is_dir()


def test_file_libraries_can_only_be_imported_by_their_own_exercises():
    code = "import shutil\ndef f():\n    return len('x')"
    res = run(code, target="os.remove", imports=["os", "shutil"])
    assert res["status"] == "error" and "'shutil' is not allowed" in res["message"]


@pytest.mark.parametrize("lv", LEVELS)
def test_students_folder_is_untouched(lv, tmp_path, monkeypatch):
    student = tmp_path / "project"
    student.mkdir()
    (student / "marker.txt").write_text("mine")
    monkeypatch.chdir(student)
    code = ("import os\n"
            "def f():\n"
            "    with open('marker.txt', 'w') as fh:\n"
            "        fh.write('changed')\n"
            "    with open('new.txt', 'w') as fh:\n"
            "        fh.write('new')\n"
            "    return [os.path.basename(os.getcwd()), sorted(os.listdir('.'))]\n")
    res = run(code, lv=lv, target="os.listdir", imports=["os"], files={"marker.txt": "fixture"})
    assert values(res) == [["work", ["marker.txt", "new.txt"]]]
    assert sorted(os.listdir(student)) == ["marker.txt"]
    assert (student / "marker.txt").read_text() == "mine"


@pytest.mark.parametrize("lv", LEVELS)
def test_temporary_folder_is_removed_afterwards(lv, tmp_path, monkeypatch):
    base = tmp_path / "tmpbase"
    base.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(base))
    code = "def f():\n    len('x')\n    open('left.txt', 'w').write('x')\n    return 1"
    assert values(run(code, lv=lv, files={"a.txt": ""})) == [1]
    assert list(base.iterdir()) == []


@pytest.mark.parametrize("lv", LEVELS)
def test_fixture_files_are_visible_and_reset_before_every_test(lv):
    code = ("def f(name):\n"
            "    with open(name, 'a') as fh:\n"
            "        fh.write('+')\n"
            "    with open(name) as fh:\n"
            "        return len(fh.read())\n")
    res = run(code, inputs=[["data/a.txt"], ["data/a.txt"], ["b.txt"]], lv=lv,
              files={"data/a.txt": "hello", "b.txt": ""})
    assert values(res) == [6, 6, 1]          # the second test sees the original file again


def test_open_is_only_available_when_the_exercise_has_files():
    code = "def f():\n    len('x')\n    return open('x.txt', 'w')"
    assert "NameError" in values(run(code))[0]


# ----- programs, ctypes and the network -----------------------------------------------------


@pytest.mark.parametrize("lv", LEVELS)
def test_starting_programs_is_blocked(lv):
    code = ("import os\n"
            "def f(how):\n"
            "    if how == 'system': return os.system('echo hi')\n"
            "    return os.popen('echo hi').read()\n")
    res = run(code, inputs=[["system"], ["popen"]], lv=lv, target="os.getcwd", imports=["os"])
    assert all("blocked" in v for v in values(res))
    assert any("os.system" in b for b in res["blocked"])
    assert any("subprocess.Popen" in b for b in res["blocked"])


def test_subprocess_and_ctypes_can_not_be_imported():
    for module in ("subprocess", "ctypes"):
        res = run(f"import {module}\ndef f():\n    return len('x')", imports=[module])
        assert res["status"] == "error" and "not allowed" in res["message"]


@pytest.mark.parametrize("lv", LEVELS)
def test_network_is_blocked(lv):
    code = ("import socket\n"
            "def f(host):\n"
            "    s = socket.socket()\n"
            "    s.settimeout(2)\n"
            "    s.connect((host, 80))\n"
            "    return 'connected'\n")
    res = run(code, inputs=[["1.1.1.1"], ["example.com"]], lv=lv, target="socket.socket",
              imports=["socket"])
    assert all("blocked" in v for v in values(res))


@pytest.mark.parametrize("lv", LEVELS)
def test_base_url_fixture_server_answers(lv):
    code = ("import json\nimport urllib.request\nimport urllib.error\n"
            "def f(path):\n"
            "    try:\n"
            "        with urllib.request.urlopen(BASE_URL + path, timeout=5) as r:\n"
            "            body = r.read().decode()\n"
            "            return [r.status, json.loads(body) if path != '/hello' else body]\n"
            "    except urllib.error.HTTPError as e:\n"
            "        return [e.code, None]\n")
    fixtures = {"/users/1": {"status": 200, "json": {"name": "Ana"}},
                "/hello": {"text": "hi"}}
    res = run(code, inputs=[["/users/1"], ["/hello"], ["/nope"]], lv=lv,
              target="urllib.request.urlopen", imports=["urllib", "json"],
              http_fixtures=fixtures)
    assert values(res) == [[200, {"name": "Ana"}], [200, "hi"], [404, None]]
    assert res["blocked"] == []
    env = "import os\ndef f():\n    return os.environ['PYGYM_BASE_URL'] == BASE_URL"
    assert values(run(env, lv=lv, target="os.getcwd", imports=["os"],
                      http_fixtures=fixtures)) == [True]


def test_fixture_server_matches_methods_and_encoded_queries():
    code = ("import json\nimport urllib.request\n"
            "def f(method, path):\n"
            "    req = urllib.request.Request(BASE_URL + path, method=method,\n"
            "                                 data=b'{}' if method == 'POST' else None)\n"
            "    with urllib.request.urlopen(req, timeout=5) as r:\n"
            "        return [r.status, r.read().decode()]\n")
    fixtures = {"POST /orders": {"status": 201, "text": "made"}, "/orders": {"text": "list"},
                "/find?q=a b": {"text": "found"}}
    res = run(code, inputs=[["POST", "/orders"], ["GET", "/orders"], ["GET", "/find?q=a%20b"]],
              target="urllib.request.urlopen", imports=["urllib", "json"],
              http_fixtures=fixtures, lv=sandbox.level())
    assert values(res) == [[201, "made"], [200, "list"], [200, "found"]]


def test_base_url_in_results_does_not_depend_on_the_port():
    code = "def f(path):\n    len('x')\n    return BASE_URL + path"
    res = run(code, inputs=[["/a"]], http_fixtures={"/a": {"text": "a"}})
    assert values(res) == ["BASE_URL/a"]


def test_requests_reaches_the_fixture_server_only():
    pytest.importorskip("requests")
    code = ("import requests\n"
            "def f(path):\n"
            "    if path.startswith('http'):\n"
            "        return requests.get(path, timeout=3).status_code\n"
            "    r = requests.get(BASE_URL + path, timeout=3)\n"
            "    return [r.status_code, r.json()]\n")
    res = run(code, inputs=[["/users/2"], ["https://example.com/"]], lv=sandbox.level(),
              target="requests.get", imports=["requests"],
              http_fixtures={"/users/2": {"json": {"name": "Bo"}}})
    out = values(res)
    assert out[0] == [200, {"name": "Bo"}]
    assert "blocked" in out[1] or "Error" in out[1]


# ----- the OS layer on its own (no audit hook) ----------------------------------------------


@needs_bwrap
def test_bwrap_alone_hides_home_and_the_network(tmp_path):
    home_file = os.path.join(os.path.expanduser("~"), f"pygym-bwrap-test-{os.getpid()}.txt")
    outside = tmp_path / "outside.txt"
    code = (
        "import os, socket\n"
        "out = []\n"
        f"open({home_file!r}, 'w').write('x')\n"          # lands in the empty, temporary home
        f"try:\n    open({str(outside)!r}, 'w').write('x')\n"
        "except OSError as e:\n    out.append('tmp-error')\n"
        "try:\n    open('/usr/pygym-x', 'w')\n"
        "except OSError:\n    out.append('usr-readonly')\n"
        "s = socket.socket()\n"
        "try:\n    s.connect(('1.1.1.1', 80))\n"
        "except OSError:\n    out.append('no-network')\n"
        "open('ok.txt', 'w').write('fine')\n"
        "print(' '.join(out))\n")
    root = sandbox._new_root()
    try:
        cmd = sandbox.command("bwrap", root, [sandbox.python(), "-E", "-B", "-c", code])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                              env=sandbox.child_env())
        assert proc.returncode == 0, proc.stderr
        assert "usr-readonly" in proc.stdout and "no-network" in proc.stdout
        assert os.path.exists(os.path.join(root, "work", "ok.txt"))
    finally:
        sandbox._remove_tree(root)
    assert not os.path.exists(home_file)
    assert not outside.exists()        # /tmp is a new, empty tmpfs inside


# ----- levels and command lines -------------------------------------------------------------


def test_level_names_and_override(monkeypatch):
    assert grader.sandbox_level() in sandbox.LEVELS
    monkeypatch.setenv("PYGYM_SANDBOX", "audit-hook")
    assert grader.sandbox_level() == "audit-hook"
    assert sandbox.label() == "basic (Python audit hook only)"
    monkeypatch.setenv("PYGYM_SANDBOX", "bwrap")          # can't force a missing layer
    monkeypatch.setattr(sandbox, "_best", {"level": "audit-hook"})
    assert sandbox.level() == "audit-hook"


def test_detection_falls_back_when_the_sandbox_does_not_work(monkeypatch):
    monkeypatch.setattr(sandbox, "_best", {})
    monkeypatch.setattr(sandbox, "_self_test", lambda lv: False)
    assert sandbox.level() == "audit-hook"
    monkeypatch.setattr(sandbox, "_best", {})
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sandbox, "_self_test", lambda lv: True)
    assert sandbox.level() == "audit-hook"


def test_self_test_fails_cleanly_when_bwrap_can_not_start(monkeypatch):
    def broken(*a, **k):
        raise OSError("user namespaces are disabled")
    monkeypatch.setattr(sandbox.subprocess, "run", broken)
    assert sandbox._self_test("bwrap") is False


def test_bwrap_command_line():
    cmd = sandbox.bwrap_command("/tmp/pygym-run-1", ["py", "runner.py"], home="/home/u",
                                read_only=["/home/u/venv", "/home/u/venv/lib", "/usr/lib",
                                           "/tmp/venv2"])
    line = " ".join(cmd)
    assert cmd[0] == "bwrap" and cmd[-2:] == ["py", "runner.py"]
    for part in ("--ro-bind / /", "--tmpfs /tmp", "--tmpfs /home/u",
                 "--ro-bind /home/u/venv /home/u/venv", "--ro-bind /tmp/venv2 /tmp/venv2",
                 "--bind /tmp/pygym-run-1 /tmp/pygym", "--chdir /tmp/pygym/work",
                 "--unshare-net", "--unshare-pid", "--die-with-parent"):
        assert part in line, part
    assert "/home/u/venv/lib" not in line and "--ro-bind /usr/lib" not in line
    assert line.index("--tmpfs /home/u") < line.index("--ro-bind /home/u/venv")


def test_macos_profile_and_command():
    profile = sandbox.macos_profile('/private/var/x"y', ["/usr/local/py"])
    assert profile.startswith("(version 1)\n(allow default)")
    for part in ("(deny network*)", '(remote ip "localhost:*")', "(deny file-write*)",
                 '(allow file-write* (subpath "/private/var/x\\"y"))', "(deny process-fork)",
                 "(deny process-exec)", '(allow process-exec (subpath "/usr/local/py"))'):
        assert part in profile, part
    cmd = sandbox.command("sandbox-exec", "/tmp/r", ["py", "runner.py"])
    assert cmd[:2] == ["sandbox-exec", "-p"] and cmd[-2:] == ["py", "runner.py"]
    assert "(deny network*)" in cmd[2]


def test_child_environment_drops_secrets(monkeypatch):
    monkeypatch.setenv("PYGYM_API_KEY", "secret")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:1")
    env = sandbox.child_env()
    assert "PYGYM_API_KEY" not in env and "HTTPS_PROXY" not in env
    code = "import os\ndef f():\n    return sorted(k for k in os.environ if 'KEY' in k)"
    assert values(run(code, target="os.getcwd", imports=["os"])) == [[]]


# ----- the audit hook, called directly ------------------------------------------------------


def test_guard_decisions(tmp_path):
    inside = tmp_path / "work"
    inside.mkdir()
    g = Guard([str(inside)], port=8123)
    g("open", (str(inside / "a.txt"), "w", 0))
    g("open", ("/etc/hostname", "r", 0))                  # reading is fine
    g("open", (5, "w", 0))                                # an fd that is already open
    g("socket.connect", (None, ("127.0.0.1", 8123)))
    g("socket.getaddrinfo", ("localhost", 8123, 0, 0, 0))
    g("os.remove", (str(inside / "a.txt"), None))
    for event, args in [
        ("open", (str(tmp_path / "x"), "w", 0)),
        ("open", (str(tmp_path / "x"), None, os.O_WRONLY | os.O_CREAT)),
        ("os.remove", (str(tmp_path / "x"), None)),
        ("os.rename", (str(inside / "a"), str(tmp_path / "b"), None, None)),
        ("shutil.rmtree", (str(tmp_path), None)),
        ("subprocess.Popen", ("ls", ["ls"], None, None)),
        ("os.system", (b"ls",)),
        ("os.exec", ("/bin/ls", ["ls"], None)),
        ("os.fork", ()),
        ("ctypes.dlopen", ("libc.so.6",)),
        ("webbrowser.open", ("https://example.com",)),
        ("socket.connect", (None, ("1.1.1.1", 80))),
        ("socket.connect", (None, ("127.0.0.1", 22))),
        ("socket.connect", (None, ("127.0.0.1", "http"))),
        ("socket.connect", (None, "/var/run/docker.sock")),
        ("socket.getaddrinfo", ("example.com", 443, 0, 0, 0)),
    ]:
        with pytest.raises(PermissionError, match="^blocked: "):
            g(event, args)
    assert len(g.blocked) == 5                     # recorded, at most 5


def test_guard_follows_symlinks_out_of_the_folder(tmp_path):
    inside = tmp_path / "work"
    inside.mkdir()
    (inside / "link").symlink_to(tmp_path)
    g = Guard([str(inside)])
    with pytest.raises(PermissionError):
        g("open", (str(inside / "link" / "x.txt"), "w", 0))


def test_write_files_refuses_paths_outside(tmp_path):
    with pytest.raises(ValueError):
        write_files(str(tmp_path), {"../evil.txt": "x"})
    write_files(str(tmp_path), {"ok/a.txt": "a"})
    assert (tmp_path / "ok" / "a.txt").read_text() == "a"


@pytest.mark.parametrize("files, ok", [
    ({"a.txt": "x", "dir/b.csv": "1,2"}, True),
    ({"../a.txt": "x"}, False), ({"/etc/a": "x"}, False), ({"~/a": "x"}, False),
    ({"C:/a": "x"}, False), ({"a/./b": "x"}, False), ({"a.txt": 3}, False),
    ({"big.txt": "x" * (64 * 1024 + 1)}, False), ([], False),
])
def test_check_files(files, ok):
    assert (sandbox.check_files(files) == []) is ok


@pytest.mark.parametrize("fixtures, ok", [
    ({"/users/1": {"status": 200, "json": {"a": 1}}, "/t": {"text": "hi"}}, True),
    ({"POST /orders": {"status": 201, "json": {}}}, True),
    ({'/total?items=["a", "b"]': {"json": 1}}, True),         # a space, but no method word
    ({"users/1": {"json": {}}}, False), ({"/a": {"status": 200}}, False),
    ({"/a": {"status": 999, "json": 1}}, False), ({"/a": {"text": 5}}, False),
    ({"/a": "hi"}, False), ([], False),
])
def test_check_fixtures(fixtures, ok):
    assert (sandbox.check_fixtures(fixtures) == []) is ok


def test_timeout_and_memory_limits_still_apply():
    loop = "def f():\n    len('x')\n    while True:\n        pass"
    assert grader.run_in_sandbox(loop, "f", "len", [[]], 1.5)["status"] == "timeout"
    hog = "def f():\n    len('x')\n    return len('x' * (2 * 1024 ** 3))"
    out = grader.run_in_sandbox(hog, "f", "len", [[]], 10.0)
    assert out["status"] == "error" or "MemoryError" in out["outputs"][0].get("error", "")
