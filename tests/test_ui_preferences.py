"""The chart page on the UI's preferences (liminal-libs 1.23.0 ui: one file for every app's page on this machine, the
data folder's ui.json, which the platform's Preferences plugin sets too; the user, 2026-11-07: "add a preferences plugin
for the 'UI' and add the different themes"), and the rail's views turned off and on (the user, of the platform's tray:
"allow me to right click on the icons to disable them on the right icon menu, also allow for enable disable switch
(left to right)"). The front answers GET and POST /api/ui itself, the data API too (alone, the integrator's own page);
the page is painted by the library's theme.js and saves a theme picked in Preferences there; a right click on a view's
rail icon offers Disable (the library's menu.js) and Preferences lists the views, each with the library's switch.
Every test has its own UI file (conftest.ui_preferences_apart); no process starts."""
from __future__ import annotations

import json
import re
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from liminal_libs import ui
from liminal_libs.storage import Store

from liminal_charts.gui import front as front_mod, web
from liminal_charts.gui.front import Front, make_front_handler
from liminal_charts.gui.server import ChartApp, make_handler

STATIC = Path(web.__file__).parent / "static"
WRITE = {"Content-Type": "application/json", "X-Liminal-Charts": "1"}


def _text(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def _ask(port: int, method: str, path: str, body=None, headers=None):
    c = HTTPConnection("127.0.0.1", port)
    data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    c.request(method, path, body=data, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def _serving(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_the_ui_preferences_say_whether_a_theme_is_saved_and_take_a_theme_alone(tmp_path):
    file = ui.preferences_file()
    assert file == tmp_path / "ui.json" and not file.exists()  # the test's own (conftest), never the machine's
    p = web.ui_preferences()
    assert (p["theme"], p["base"], p["saved"]) == ("dark", "dark", False)  # none saved: the chart page may carry its own over
    assert [t["name"] for t in p["themes"]] == list(ui.THEMES)
    p = web.save_ui({"theme": "paper"})
    assert (p["theme"], p["base"], p["saved"]) == ("paper", "light", True) and json.loads(file.read_text(encoding="utf-8")) == {"theme": "paper"}
    for bad in ({"theme": "neon"}, {}, {"theme": "dark", "path": str(tmp_path / "elsewhere.json")}, {"theme": ["dark"]}, ["dark"], "dark"):
        with pytest.raises(ValueError):
            web.save_ui(bad)
    assert web.ui_preferences()["theme"] == "paper" and not (tmp_path / "elsewhere.json").exists()  # nothing else written
    file.write_text('{"theme": "neon"}', encoding="utf-8")  # a file no app wrote: no theme saved, never an error
    assert (web.ui_preferences()["theme"], web.ui_preferences()["saved"]) == ("dark", False)
    file.write_text("not json", encoding="utf-8")
    assert (web.ui_preferences()["theme"], web.ui_preferences()["saved"]) == ("dark", False)


def test_the_front_answers_the_ui_preferences_itself(tmp_path, monkeypatch):
    """Not the integrator's (none runs: its port off): the machine's file, read for the sign-in page before any
    integrator need run, and written by the chart page's Preferences; the library's theme.js and menu.js served."""
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", "none")
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    monkeypatch.setattr(front_mod, "start_integrator", lambda db_path, port, log=print: pytest.fail("nothing to start"))
    srv = _serving(make_front_handler(Front(tmp_path / "liminal.db", log=lambda m: None)))
    port = srv.server_address[1]
    try:
        status, p = _ask(port, "GET", "/api/ui")
        assert status == 200 and (p["theme"], p["saved"]) == ("dark", False)
        assert _ask(port, "GET", "/api/echo")[0] == 503  # anything else: the integrator's (none here)
        assert _ask(port, "GET", "/api/ui", headers={"Origin": "http://evil.example"})[0] == 403
        assert _ask(port, "POST", "/api/ui", {"theme": "paper"}, {"Content-Type": "application/json"})[0] == 403  # no write header
        assert _ask(port, "POST", "/api/ui", {"theme": "neon"}, WRITE)[0] == 400
        assert _ask(port, "POST", "/api/ui", b"{not json", WRITE)[0] == 400
        assert _ask(port, "PUT", "/api/ui", {"theme": "paper"}, WRITE)[0] == 405
        assert not ui.preferences_file().exists()  # nothing refused was written
        status, p = _ask(port, "POST", "/api/ui", {"theme": "midnight"}, WRITE)
        assert status == 200 and (p["theme"], p["base"], p["saved"]) == ("midnight", "dark", True)
        assert ui.preferences()["theme"] == "midnight"  # what the platform's Preferences plugin reads
        assert _ask(port, "GET", "/api/ui")[1]["theme"] == "midnight"
        for name in ("theme.js", "menu.js"):
            status, js = _ask(port, "GET", f"/{name}")
            assert status == 200 and js == (ui.STATIC / name).read_bytes()  # the library's own
        # a file that cannot be written (its folder a file): the page is told, never a dropped connection
        (tmp_path / "afile").write_text("", encoding="utf-8")
        monkeypatch.setenv(ui.UI_VAR, str(tmp_path / "afile" / "ui.json"))
        status, p = _ask(port, "POST", "/api/ui", {"theme": "paper"}, WRITE)
        assert status == 500 and p["error"].startswith("the UI's preferences: ")
        assert _ask(port, "GET", "/api/ui")[1]["theme"] == "dark"  # read as none saved
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_data_api_answers_the_ui_preferences_too(tmp_path):
    """The chart app alone and the integrator's own page are painted in the same theme; a pick there is saved alike."""
    store = Store(tmp_path / "liminal.db", check_same_thread=False)
    srv = _serving(make_handler(ChartApp(store), [0]))
    port = srv.server_address[1]
    try:
        status, p = _ask(port, "GET", "/api/ui")
        assert status == 200 and (p["theme"], p["saved"]) == ("dark", False)
        assert _ask(port, "POST", "/api/ui", {"theme": "graphite"}, {"Content-Type": "application/json"})[0] == 403
        assert _ask(port, "POST", "/api/ui", {"theme": "neon"}, WRITE)[0] == 400
        # the machine's, not a profile's: a page loaded as another profile sets it as well
        status, p = _ask(port, "POST", "/api/ui", {"theme": "graphite"}, {**WRITE, "X-Liminal-Profile": "9"})
        assert status == 200 and (p["theme"], p["saved"]) == ("graphite", True) and ui.preferences()["theme"] == "graphite"
    finally:
        srv.shutdown()
        srv.server_close()
        store.close()


def test_the_pages_are_painted_in_the_ui_theme_and_the_charts_theme_is_saved_there():
    html = _text("index.html")
    body = html[html.index("<body>"):]
    assert body.index('<script src="theme.js"></script>') < body.index("<header")  # first: painted at once
    assert html.index('<script src="menu.js"></script>') < html.index('<script src="panels.js"></script>')
    login = _text("login.html")
    assert login.index('<script src="theme.js"></script>') < login.index("<main")
    # the login app's page held on the sign-in page (and in the bound view) in this page's theme
    assert "/login/?embedded=1${theme ? `&theme=${encodeURIComponent(theme)}` : ''}" in _text("login.js")
    assert "?embedded=1&theme=${encodeURIComponent(state.theme)}" in _text("panels.js")
    app = _text("app.js")
    assert "prefs.set('theme'" not in app  # no longer a profile's own
    assert "apiWrite('POST', '/api/ui', { theme: state.theme })" in app
    assert "hearUiTheme(window.LiminalTheme.ready)" in app and "window.LiminalTheme.load()" in app
    assert "document.addEventListener('visibilitychange', followUiTheme)" in app  # another app's pick follows here
    # a profile's own theme from before is carried over once, only while the file holds none (`saved` false)
    assert "ui.saved === false && THEMES[mine]" in app and "prefs.drop('theme')" in app
    assert "$('themeSelect').addEventListener('change', (e) => pickTheme(e.target.value))" in app
    # the chart page's themes are the library's, by name and base
    themes = dict(re.findall(r"^  (\w+): \{ base: '(\w+)'", app, re.M))
    assert themes == {name: t["base"] for name, t in ui.THEMES.items()}


def test_a_right_click_on_a_views_rail_icon_disables_it_and_preferences_enables_it_again():
    panels = _text("panels.js")
    assert "addEventListener('contextmenu'" in panels and "window.LiminalMenu.open(" in panels
    assert "items: [{ label: 'Disable', run: () => setViewOn(view[0], false) }]" in panels
    assert "const FIXED = new Set(['stock', 'prefs']);" in panels  # the app's own icon, and where they come back
    assert "prefs.set('viewsOff', [...viewsOff])" in panels and "prefs.get('viewsOff', [])" in panels  # per profile
    assert "!viewOn(name)" in panels.split("function showPane", 1)[1].split("\n  }\n", 1)[0]  # nothing opens one turned off
    views = re.findall(r"\['(\w+)', '[^']+', '\w+'\]", panels.split("const RAIL_VIEWS", 1)[1].split("];", 1)[0])
    assert views == re.findall(r'<button data-pane="(\w+)"', _text("index.html"))  # every view on the rail, in its order
    prefs = _text("prefs.js")
    assert 'class="switch" role="switch" aria-checked="${v.on}"' in prefs and "window.Views?.set(sw.dataset.view" in prefs
    assert "window.addEventListener('liminal:views'" in prefs
    assert "&& !button.hidden" in _text("liveicon.js")  # a Live icon turned off draws no trace
    # the menu has the keys while open: the Escape closing it neither leaves the view nor drops a drawing, a letter
    # does not open the symbol search (menu.js closes on Escape in the capture phase, its default prevented)
    assert "e.key === 'Escape' && !e.defaultPrevented && document.body.dataset.view !== 'chart'" in panels
    assert "if (window.LiminalMenu?.shown || (e.key === 'Escape' && e.defaultPrevented)) return;" in _text("app.js")
    css = _text("style.css")
    assert "#rail > [hidden] { display: none; }" in css  # #rail button's display: grid kept a hidden one on screen


def _rules(css: str) -> list[tuple[str, str]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    norm = lambda s: " ".join(s.split())  # noqa: E731
    return [(", ".join(norm(x) for x in sel.split(",")), norm(decl)) for sel, decl in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def test_the_page_carries_the_librarys_switch_and_menu_as_they_are():
    """style.css holds liminal.css's switch and context menu until the page moves onto that file (0.3.0): the same
    rules, the menu's for a menu.js box alone (the page's own .menu wraps a dropdown)."""
    lib = [(s, d) for s, d in _rules((ui.STATIC / "liminal.css").read_text(encoding="utf-8")) if s.startswith((".switch", ".menu"))]
    assert len(lib) >= 12
    page = _rules(_text("style.css"))
    for sel, decl in lib:
        assert (re.sub(r"\.menu(?![\w-])", '.menu[role="menu"]', sel), decl) in page, sel
