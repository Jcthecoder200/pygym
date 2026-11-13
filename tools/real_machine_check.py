"""Run this ONCE on each real machine (Windows, macOS, Python 3.9, ...) and send back the output.

    pip install -e .            (or: set PYTHONPATH=src)
    python tools/real_machine_check.py            # automatic checks, about 30 seconds
    python tools/real_machine_check.py --keys     # Windows: what msvcrt.getwch() returns per key
    python tools/real_machine_check.py --keys2    # Windows: raw console events (ReadConsoleInputW)
    python tools/real_machine_check.py --keys3    # Windows: what the EDITOR's input layer makes of keys

Every line starts with PASS, FAIL, INFO or SKIP. The script changes nothing outside a temporary
folder (the macOS probes try to write OUTSIDE the sandbox on purpose, but only next to the
temporary folder, and they delete what they find).

What it covers (the parts that the Linux test run cannot):
  1. which sandbox level this machine gets, and whether a trivial run works;
  2. the audit hook alone (the Windows level): blocked writes / programs / network, refused
     libraries, the local fixture server, timeouts, the memory limit;
  3. macOS: the sandbox-exec profile probed one rule at a time (real profile from pygym.sandbox);
  4. Python 3.9 and friends: compile every file with THIS interpreter, import every module,
     run tools/check_py39.py and tools/validate_exercises.py;
  5. Windows: console ANSI mode, and (with --keys) what msvcrt.getwch() really returns.
"""
import compileall
import importlib
import os
import pkgutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

RESULTS = []


def report(kind: str, name: str, detail: str = "") -> None:
    RESULTS.append(kind)
    print(f"{kind:4}  {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    report("PASS" if ok else "FAIL", name, "" if ok else detail)


def guarded(name):
    """Run a check function; a crash in the CHECK is reported, never raised."""
    def deco(fn):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            report("FAIL", name, f"the check itself crashed: {type(e).__name__}: {e}")
        return fn
    return deco


# ----- 0. the machine ----------------------------------------------------------------------

print("=" * 78)
print(f"python   {sys.version.split()[0]} ({sys.implementation.name}) at {sys.executable}")
print(f"system   {sys.platform} / {os.name}")
try:
    import platform
    print(f"os       {platform.platform()}  machine={platform.machine()}")
except Exception:  # noqa: BLE001
    pass
print("=" * 78)

try:
    import rich  # noqa: F401
    from pygym import grader, sandbox
    import pygym
    report("INFO", "pygym", f"version {pygym.__version__}, imported from {Path(pygym.__file__).parent}")
except Exception as e:  # noqa: BLE001
    report("FAIL", "import pygym", f"{type(e).__name__}: {e}  (pip install -e . ?)")
    sys.exit(1)

# ----- 1. level ----------------------------------------------------------------------------

LEVEL = sandbox.level()
report("INFO", "sandbox level", f"{LEVEL} = {sandbox.label()}")
if sys.platform == "darwin":
    check("macOS: sandbox-exec exists", bool(__import__("shutil").which("sandbox-exec")))
    check("macOS: level is sandbox-exec (self-test passed)", LEVEL == "sandbox-exec",
          "the self-test failed, so pygym silently fell back to the audit hook; "
          "see the probes in section 3 for the reason")
if os.name == "nt":
    check("Windows: level is audit-hook", LEVEL == "audit-hook")


def run(code, inputs=((),), target="len", imports=(), lv=None, timeout=10.0, **kw):
    return grader.run_in_sandbox(code, "f", target, [list(i) for i in inputs], timeout,
                                 allowed_imports=imports, level=lv or LEVEL, **kw)


def values(res):
    return [o.get("value", o.get("error")) for o in res.get("outputs", [])] \
        if res.get("status") == "ok" else [f"<{res.get('status')}: {res.get('message', '')}>"]


@guarded("trivial run")
def _():
    res = run("def f(x):\n    return len(x)\n", inputs=[["abc"]])
    check("a trivial exercise runs and is graded", values(res) == [3], str(res))


@guarded("unicode")
def _():
    res = run("def f(x):\n    print('héllo 你好')\n    return len(x)\n", inputs=[["你好"]])
    check("non-ASCII text in code, input and print survives (Windows code pages)",
          values(res) == [2] and "你好" in res["outputs"][0].get("stdout", ""), str(res))


# ----- 2. the audit hook alone (what Windows gets) ----------------------------------------

def _lan_listener():
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 9))      # UDP connect sends nothing; it only picks an address
        ip = probe.getsockname()[0]
        probe.close()
        if ip.startswith("127."):
            return None, None
        srv = socket.socket()
        srv.bind((ip, 0))
        srv.listen(1)
        return ip, srv
    except OSError:
        return None, None


print("-" * 78 + "\n2. audit hook (forced; this is all that protects on Windows)")
AH = "audit-hook"
outside = Path(__import__("tempfile").gettempdir()) / "pygym_check_outside.txt"


@guarded("write outside")
def _():
    code = ("import os\ndef f(path):\n    open(path, 'w').write('x')\n    return 'written'\n")
    res = run(code, inputs=[[str(outside)]], target="os.getcwd", imports=["os"], lv=AH,
              files={"seed.txt": "s"})              # `files` is what makes open() available
    check("audit hook blocks a write outside the exercise folder",
          values(res) and "blocked" in str(values(res)[0]) and not outside.exists(), str(values(res)))
    if outside.exists():
        outside.unlink()


@guarded("write inside")
def _():
    code = "def f(name):\n    open(name, 'w').write('x')\n    return open(name).read()\n"
    res = run(code, inputs=[["a.txt"]], target="os.getcwd", imports=["os"], lv=AH,
              files={"seed.txt": "s"})
    check("writing INSIDE the exercise folder works", values(res) == ["x"], str(values(res)))


@guarded("programs")
def _():
    code = ("import os\ndef f():\n    return os.system('echo hi')\n")
    res = run(code, inputs=[()], target="os.getcwd", imports=["os"], lv=AH)
    check("audit hook blocks os.system", "blocked" in str(values(res)), str(values(res)))


@guarded("network")
def _():
    code = ("import socket\ndef f(host, port):\n    s = socket.socket()\n    s.settimeout(3)\n"
            "    s.connect((host, port))\n    return 'connected'\n")
    # a listener on a NON-loopback address of this machine: connecting is possible without the
    # hook, so a block is the hook's doing (no internet needed)
    lan, srv = _lan_listener()
    if lan is None:
        report("SKIP", "audit hook blocks a connection to a non-local address", "no LAN address found")
    else:
        res = run(code, inputs=[[lan, srv.getsockname()[1]]], target="socket.socket",
                  imports=["socket"], lv=AH)
        check(f"audit hook blocks a connection to {lan} (this machine, not localhost)",
              "blocked" in str(values(res)), str(values(res)))
        srv.close()


@guarded("fixture server")
def _():
    code = ("import urllib.request, json\ndef f(path):\n"
            "    with urllib.request.urlopen(BASE_URL + path, timeout=5) as r:\n"
            "        return json.loads(r.read().decode())\n")
    res = run(code, inputs=[["/u"]], target="urllib.request.urlopen", imports=["urllib", "json"],
              lv=AH, http_fixtures={"/u": {"json": {"name": "Ana"}}})
    check("the local fixture server (BASE_URL) answers", values(res) == [{"name": "Ana"}], str(values(res)))


@guarded("refusal")
def _():
    r = {name: sandbox.refusal(name, AH)[0] for name in
         ("os", "os.path.join", "pathlib", "socket", "requests", "urllib.request", "math", "statistics.median")}
    check("file/network libraries are refused at audit-hook level",
          all(r[n] for n in ("os", "os.path.join", "pathlib", "socket", "requests", "urllib.request")), str(r))
    check("pure libraries are still offered", not r["math"] and not r["statistics.median"], str(r))


@guarded("timeout")
def _():
    t0 = time.time()
    res = run("def f():\n    while True:\n        pass\n", inputs=[()], timeout=2.0, lv=AH)
    took = time.time() - t0
    check("an endless loop is stopped by the timeout", res.get("status") == "timeout" and took < 6,
          f"status={res.get('status')} after {took:.1f}s")


@guarded("memory")
def _():
    res = run("def f():\n    b = bytearray(700 * 1024 * 1024)\n    return len(b)\n", inputs=[()],
              timeout=15.0, lv=AH)
    limited = res.get("status") != "ok" or "MemoryError" in str(values(res)) or "memory" in str(res).lower()
    report("INFO", "memory limit (512 MB) vs a 700 MB allocation",
           "enforced" if limited else "NOT enforced on this system: only the timeout limits memory "
           f"(expected on Windows; on macOS RLIMIT_AS is often ignored)  result={str(values(res))[:80]}")


# ----- 3. macOS probes ---------------------------------------------------------------------

print("-" * 78 + "\n3. macOS sandbox-exec, one rule at a time")
if sys.platform != "darwin" or not __import__("shutil").which("sandbox-exec"):
    report("SKIP", "macOS probes", "not macOS")
else:
    def probe(name, code, expect):
        root = sandbox._new_root()
        try:
            cmd = sandbox.command("sandbox-exec", root, [sandbox.python(), "-E", "-B", "-c", code])
            p = subprocess.run(cmd, cwd=os.path.join(root, "work"), capture_output=True, text=True,
                               timeout=25, env=sandbox.child_env())
            out = (p.stdout or "").strip()
            ok = expect in out
            report("PASS" if ok else "FAIL", f"sandbox-exec: {name}",
                   "" if ok else f"wanted {expect!r}; rc={p.returncode} stdout={out[:120]!r} "
                                 f"stderr={(p.stderr or '').strip()[:240]!r}")
        except Exception as e:  # noqa: BLE001
            report("FAIL", f"sandbox-exec: {name}", f"{type(e).__name__}: {e}")
        finally:
            try:
                sandbox._remove_tree(root)
            except Exception:  # noqa: BLE001
                pass

    probe("python itself starts (exec rule, interpreter outside its prefix)", "print('ok')", "ok")
    probe("write inside <root>/work", "open('a.txt','w').write('1'); print('ok')", "ok")
    sibling = os.path.join(os.path.dirname(os.path.realpath(__import__("tempfile").mkdtemp())),
                           "pygym_sbx_outside_probe.txt")
    probe("write OUTSIDE <root> is denied",
          "import os\ntry:\n    open(%r,'w').write('1'); print('WRITTEN')\nexcept OSError as e:\n    print('denied', e.errno)" % sibling,
          "denied")
    if os.path.exists(sibling):
        os.unlink(sibling)
        report("FAIL", "cleanup", f"{sibling} had been written by the sandboxed process and was removed")
    lan_ip, lan_srv = _lan_listener()
    if lan_ip:
        probe("connect to this machine's non-loopback address is denied",
              "import socket\ns=socket.socket(); s.settimeout(3)\ntry:\n    s.connect(('%s', %d)); print('CONNECTED')\n"
              "except OSError as e:\n    print('denied', e.errno)" % (lan_ip, lan_srv.getsockname()[1]), "denied")
        lan_srv.close()
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    threading.Thread(target=lambda: srv.accept(), daemon=True).start()
    probe("connect to 127.0.0.1 is allowed",
          "import socket\ns=socket.socket(); s.settimeout(3); s.connect(('127.0.0.1', %d)); print('connected')"
          % srv.getsockname()[1], "connected")
    probe("a local server inside the sandbox can bind and be reached",
          "import socket\nsv=socket.socket(); sv.bind(('127.0.0.1',0)); sv.listen(1)\n"
          "c=socket.socket(); c.connect(('127.0.0.1', sv.getsockname()[1])); print('ok')", "ok")
    probe("starting another program is denied",
          "import os\ntry:\n    os.execv('/bin/echo', ['echo','hi'])\nexcept OSError as e:\n    print('denied', e.errno)",
          "denied")
    probe("fork is denied", "import os\ntry:\n    os.fork(); print('FORKED')\nexcept OSError as e:\n    print('denied', e.errno)",
          "denied")
    probe("RLIMIT_AS (512 MB) is enforced",
          "import resource\ntry:\n    resource.setrlimit(resource.RLIMIT_AS,(512<<20,512<<20)); print('limit set')\n"
          "except Exception as e:\n    print('cannot set', e)\n"
          "try:\n    b=bytearray(1500<<20); print('allocated 1500 MB: NOT enforced')\nexcept MemoryError:\n    print('limited')",
          "limited")
    srv.close()

# ----- 4. Python version / compile / import / data ----------------------------------------

print("-" * 78 + "\n4. this interpreter: compile, import, data")
check(f"every .py compiles on Python {sys.version_info.major}.{sys.version_info.minor}",
      all(compileall.compile_dir(str(ROOT / d), quiet=2, force=True, legacy=False,
                                 maxlevels=10) for d in ("src", "tools")) or False)
bad = []
import pygym as _pg  # noqa: E402
for mod in pkgutil.walk_packages(_pg.__path__, "pygym."):
    if mod.name.endswith("__main__"):
        continue
    try:
        importlib.import_module(mod.name)
    except Exception as e:  # noqa: BLE001
        bad.append(f"{mod.name}: {type(e).__name__}: {e}")
check("every pygym module imports", not bad, "; ".join(bad[:3]))
for script, name in (("check_py39.py", "static 3.9 scan"), ("validate_exercises.py", "exercise validator")):
    p = subprocess.run([sys.executable, str(ROOT / "tools" / script)], capture_output=True, text=True,
                       timeout=600, cwd=str(ROOT), env=dict(os.environ, PYTHONPATH=str(ROOT / "src")))
    tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
    check(name, p.returncode == 0, " | ".join(tail[-3:]) if p.returncode else "")

# ----- 5. Windows console ------------------------------------------------------------------

print("-" * 78 + "\n5. Windows console")
if os.name != "nt":
    report("SKIP", "Windows console checks", "not Windows")
else:
    from pygym import editor
    report("INFO", "stdin/stdout are a console", f"{sys.stdin.isatty()} / {sys.stdout.isatty()}")
    old = editor._win_vt_mode(True)
    check("ANSI/VT mode can be switched on (needed by the editor)", old is not None,
          "run this in Windows Terminal or conhost on Windows 10 1809+; not inside an IDE console")
    if old is not None:
        editor._win_vt_mode(False, old)
    report("INFO", "editor_available()", str(editor.editor_available()))


def key_probe():
    """Interactive: press keys; Esc ends. Shows what getwch() returns and what kbhit() says."""
    import msvcrt
    print("\nKEY PROBE. Press: an arrow key, Home, Delete, then the letter à (Alt+0224 on the numeric pad,\n"
          "or your layout's key), then é (Alt+0233), then Esc to finish.\n"
          "(Alt codes with a leading 0 use the ANSI code page, 0-255: Alt+0279 gives 279 mod 256 = 23, a\n"
          "control character, not ė. For other characters type them with your IME or paste them.)")
    while True:
        ch = msvcrt.getwch()
        extra = ""
        if ch in ("\x00", "\xe0"):
            pending = msvcrt.kbhit()
            extra = f"  kbhit_after_prefix={pending}"
            if pending:
                extra += f"  next={msvcrt.getwch()!r}"
        print(f"getwch() -> {ch!r} (U+{ord(ch):04X}){extra}", flush=True)
        if ch == "\x1b":
            return


def raw_event_probe():
    """Interactive: print the raw console key events (what ReadConsoleInputW delivers). The point:
    does an arrow key (virtual-key code, char 0) look different from the letter à (char U+00E0)?"""
    import ctypes
    from ctypes import wintypes

    # fixed-width types: the same layout on every platform (wintypes.BOOL/WCHAR change size on Linux)
    class KeyEvent(ctypes.Structure):
        _fields_ = [("bKeyDown", ctypes.c_int32), ("wRepeatCount", ctypes.c_uint16),
                    ("wVirtualKeyCode", ctypes.c_uint16), ("wVirtualScanCode", ctypes.c_uint16),
                    ("uChar", ctypes.c_uint16), ("dwControlKeyState", ctypes.c_uint32)]

    class Event(ctypes.Union):
        _fields_ = [("Key", KeyEvent), ("pad", ctypes.c_byte * 16)]

    class InputRecord(ctypes.Structure):
        _fields_ = [("EventType", ctypes.c_uint16), ("Event", Event)]

    assert ctypes.sizeof(InputRecord) == 20, ctypes.sizeof(InputRecord)
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = wintypes.HANDLE
    handle = k32.GetStdHandle(-10)
    k32.FlushConsoleInputBuffer(handle)             # drop the Enter that started this script
    print("\nRAW EVENT PROBE. Press: Left arrow, Home, Delete, Alt+0224 (à), Alt+0233 (é), a Chinese\n"
          "character typed with your IME, then Esc. Key-up events are shown only when they carry a\n"
          "character (Alt+numpad characters arrive that way).")
    record, count = InputRecord(), wintypes.DWORD()   # DWORD is 4 bytes on Windows
    while True:
        if not k32.ReadConsoleInputW(handle, ctypes.byref(record), 1, ctypes.byref(count)):
            print("ReadConsoleInputW failed (is stdin a console?)")
            return
        if record.EventType != 1:                  # 1 = KEY_EVENT; skip mouse/focus/resize
            continue
        key = record.Event.Key
        if not key.bKeyDown and key.uChar == 0:
            continue
        print(f"{'down' if key.bKeyDown else 'up  '}  vk=0x{key.wVirtualKeyCode:02X}  "
              f"char={chr(key.uChar)!r} (U+{key.uChar:04X})  control_keys=0x{key.dwControlKeyState:03X}",
              flush=True)
        if key.bKeyDown and key.wVirtualKeyCode == 0x1B:
            return


def editor_keys_probe():
    """Interactive: the editor's own input layer (pygym.editor._ConsoleReader), key names as the
    editor receives them. Expected: LEFT, HOME, DELETE, à, é, 中; nothing for Ctrl+Left, Shift+Tab, F1."""
    import time
    from pygym import editor
    try:
        reader = editor._ConsoleReader()
    except Exception as e:  # noqa: BLE001
        report("FAIL", "the editor's raw console reader starts", f"{type(e).__name__}: {e}")
        return
    report("PASS", "the editor's raw console reader starts")
    while reader.pending():                          # drop the Enter that started this script
        reader.read()
    print("\nEDITOR KEY PROBE. Press: Left arrow, Home, Delete, Alt+0224 (à), Alt+0233 (é), paste a\n"
          "Chinese character, Ctrl+Left, Shift+Tab, F1, then type: Hello (a paste-sized burst is\n"
          "wrapped in PASTE_START/PASTE_END), then Esc.")
    while True:
        if not reader.pending():
            time.sleep(0.02)
            continue
        keys = reader.read()
        if keys:
            print("editor receives:", keys, flush=True)
        if "ESC" in keys:
            return


if "--keys" in sys.argv:
    if os.name == "nt":
        key_probe()
    else:
        report("SKIP", "--keys", "Windows only")

if "--keys3" in sys.argv:
    if os.name == "nt":
        editor_keys_probe()
    else:
        report("SKIP", "--keys3", "Windows only")

if "--keys2" in sys.argv:
    if os.name == "nt":
        raw_event_probe()
    else:
        report("SKIP", "--keys2", "Windows only")

print("=" * 78)
print(f"{RESULTS.count('PASS')} PASS, {RESULTS.count('FAIL')} FAIL, {RESULTS.count('INFO')} INFO, "
      f"{RESULTS.count('SKIP')} SKIP")
print("Please send this whole output back (including the header lines at the top).")
sys.exit(1 if "FAIL" in RESULTS else 0)
