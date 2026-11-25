"""No console window from any process the app starts (proc.no_window): on the user's PC Windows
Terminal is the default terminal, and a console child of a process without a console (the chart
app, a host) got a Terminal window of its own that stayed open when it exited with an error: 28 on
the desktop from the datasets' git calls (the user, 2026-11-01, "i have 30 cmd prompts open")."""
from __future__ import annotations

import os
import re
from pathlib import Path

import liminal_charts
from liminal_libs import datasets, proc, proctree, provenance

# the library's package (liminal-libs), the app's own, and the integrator's when it is installed
ROOTS = [Path(proc.__file__).parent, Path(liminal_charts.__file__).parent]
try:
    import liminal_integrator

    ROOTS.append(Path(liminal_integrator.__file__).parent)
except ImportError:
    pass


def test_no_window_is_the_windows_flag_and_nothing_elsewhere():
    kw = proc.no_window()
    assert kw == ({"creationflags": 0x08000000} if os.name == "nt" else {})


def test_every_git_call_opens_no_window(monkeypatch, tmp_path):
    calls: list[dict] = []

    class Done:
        returncode, stdout, stderr = 0, b"ok", b""

    class Started:
        """proctree.run's Popen: the datasets' git runs through proctree since liminal-libs 1.87.0 (its tree ended at
        its timeout), so a stand-in for subprocess.run alone let a real git start here."""
        returncode = 0

        def __init__(self, args, **kw):
            calls.append(kw)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def communicate(self, input=None, timeout=None):
            return b"ok", b""
    monkeypatch.setattr(proctree.subprocess, "Popen", Started)
    assert datasets._git(tmp_path, "status", binary=True) == b"ok"
    assert calls[-1].get("creationflags", 0) == (0x08000000 if os.name == "nt" else 0)

    class Text(Done):
        stdout = "abc123def456\n"
    monkeypatch.setattr(provenance.subprocess, "run", lambda args, **kw: calls.append(kw) or Text())
    provenance.code_version.cache_clear()
    assert provenance.code_version().startswith("abc123def456")
    assert all(c.get("creationflags", 0) == (0x08000000 if os.name == "nt" else 0) for c in calls[-2:])
    provenance.code_version.cache_clear()


def test_every_subprocess_start_in_the_app_says_how_its_window_is_kept_away():
    """Each subprocess.run or Popen in the package passes creationflags (CREATE_NO_WINDOW, or a
    host's detached flags) or proc.no_window(); the console commands run by hand (schtasks) aside."""
    missing = []
    for path in (p for root in ROOTS for p in root.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.(run|Popen)\(", src):
            call = src[m.start():src.find(")\n", m.start()) + 1]
            if "schtasks" in call or "powershell" in call and "creationflags" in call:
                continue
            if "creationflags" not in call and "no_window()" not in call:
                missing.append(f"{path.name}: {call[:60]}...")
    assert missing == [], missing
