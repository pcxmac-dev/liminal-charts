"""The Codebase view (Monday list item 15: "Codebase in the login's, the integrator's and the charts' pages (the
platform has it)"): the library's object every app holds (liminal_libs.apps.Codebase, GET /api/codebase, POST
/api/codebase/fetch) answered on the charts page by the charts app the front holds, never passed on to the integrator,
and on the integrator's page by its frame; drawn by the library's codebase.js (codeview.js holds it). The code base is
given a workspace of the test's own and a stand-in runner: no git runs, no repository of this machine is read."""
from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from dataclasses import replace

import pytest

from liminal_libs.apps import App, Codebase, EnvironmentLogin
from liminal_libs.storage import Store

from liminal_charts.gui import web
from liminal_charts.gui.front import Front, make_front_handler
from liminal_charts.gui.server import ChartApp, make_handler

from .test_front import upstream  # noqa: F401 - the integrator's data API as a small echoing server


def _workspace(tmp_path):
    """A workspace with one git working copy of two files (its .git a folder of nothing: the runner answers for git)."""
    ws = tmp_path / "ws"
    (ws / "app" / ".git").mkdir(parents=True)
    (ws / "app" / "src").mkdir()
    (ws / "app" / "src" / "x.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (ws / "app" / "src" / "y.js").write_text("let c = 3;\n", encoding="utf-8")
    return ws


def _stand_in(app, tmp_path):
    """The app's code base over the test's workspace, its commands answered by a stand-in that keeps what was asked."""
    ws = _workspace(tmp_path)
    asked: list[tuple[str, str]] = []

    def runner(args, cwd, timeout):
        said = " ".join(args)
        asked.append((said, cwd))
        if said.startswith("git status"):
            return 0, b"# branch.head main\n# branch.upstream origin/main\n# branch.ab +0 -2\n", b""
        if said.startswith("git log"):
            return 0, b"abc1234\x1f2026-11-24T10:00:00-08:00\x1fthe last commit\n", b""
        if said.startswith("git config --get-regexp"):
            return 0, b"remote.origin.url https://example.test/app.git\n", b""
        if said.startswith("git config --get"):
            return 0, b"https://someone:tok3n@example.test/app.git\n", b""
        if said.startswith("git ls-files"):
            return 0, b"src/x.py\0src/y.js\0", b""
        if said.startswith("git fetch"):
            return 0, b"", b""
        return 127, b"", b"not installed here"

    app.codebase = Codebase(app, roots=[ws], runner=runner, own_parts=lambda: [(f"the {app.NAME} app", ws / "app" / "src")])
    return app.codebase, asked, ws


def _ended(codebase, seconds=10.0):
    """The asking of the remotes waited for (its thread), then the reading after it."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        f = codebase.snapshot().get("fetching")
        if f and f.get("ended"):
            return codebase.wait()
        time.sleep(0.02)
    raise AssertionError("the remotes were never asked")


def _call(port, method, path, body=None, header=True):
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": f"127.0.0.1:{port}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if header and method != "GET":
        headers["X-Liminal-Charts"] = "1"
    c.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
    r = c.getresponse()
    raw = r.read()
    try:
        return r.status, json.loads(raw)
    except ValueError:
        return r.status, raw


def _check_the_shape(got, name):
    assert got["state"] == "ready" and got["app"] == name and got["totals"]["repositories"] == 1
    repo = got["repositories"][0]
    assert (repo["name"], repo["kind"], repo["branch"], repo["behind"], repo["ahead"]) == ("app", "git", "main", 2, 0)
    assert repo["remote"] == "https://example.test/app.git" and "tok3n" not in json.dumps(got)  # never what opens it
    assert repo["used"] == [f"the {name} app"] and got["mine"]["repositories"] == 1
    assert {lang["name"]: lang["lines"] for lang in got["languages"]} == {"Python": 2, "JavaScript": 1}


def test_the_charts_page_asks_the_charts_app_the_front_holds_never_the_integrator(tmp_path, monkeypatch, upstream):  # noqa: F811
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", str(upstream.server_address[1]))  # an integrator answering: not asked
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    front = Front(tmp_path / "liminal.db", log=lambda m: None)
    codebase, asked, ws = _stand_in(front.charts, tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        status, first = _call(port, "GET", "/api/codebase")
        assert status == 200 and "path" not in first and first["state"] in ("reading", "ready")  # (the echo has a path)
        codebase.wait()
        status, got = _call(port, "GET", "/api/codebase")
        assert status == 200
        _check_the_shape(got, "charts")
        assert not [a for a, _ in asked if a.startswith("git fetch")]  # a reading asks no remote
        # the remotes asked on the user's click: a write, the page's own alone
        assert _call(port, "POST", "/api/codebase/fetch", {}, header=False)[0] == 403
        assert _call(port, "POST", "/api/codebase/fetch", {}) == (202, {"started": True})
        _ended(codebase)
        assert ("git fetch --quiet", str((ws / "app").resolve())) in asked
        # the library's drawing is served beside the page's own files
        status, drawing = _call(port, "GET", "/codebase.js")
        assert status == 200 and b"window.LiminalCodebase = {" in drawing
    finally:
        srv.shutdown()
        srv.server_close()


class IntegratorFrame(App):
    """The integrator as the data API's frame (liminal-integrator's app.py), its code base the library's."""

    NAME = "integrator"

    def __init__(self):
        super().__init__(log=lambda m: None, login=EnvironmentLogin())


def test_the_integrators_page_asks_its_frame_its_reading_and_its_remotes(tmp_path, monkeypatch):
    frame = IntegratorFrame()
    codebase, asked, ws = _stand_in(frame, tmp_path)
    store = Store(tmp_path / "liminal.db", check_same_thread=False)
    try:
        app = ChartApp(store, frame=frame)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, [0]))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        try:
            assert _call(port, "GET", "/api/codebase")[0] == 200
            codebase.wait()
            _check_the_shape(_call(port, "GET", "/api/codebase")[1], "integrator")
            assert _call(port, "POST", "/api/codebase/fetch", {}, header=False)[0] == 403
            assert _call(port, "POST", "/api/codebase/fetch", {}) == (202, {"started": True})  # (it was a 404 before)
            _ended(codebase)
            assert ("git fetch --quiet", str((ws / "app").resolve())) in asked
            # a copy that reads only asks no remote
            monkeypatch.setattr(app, "_settings", replace(app.settings, read_only=True))
            status, said = _call(port, "POST", "/api/codebase/fetch", {})
            assert status == 409 and "reads only" in said["error"]
        finally:
            httpd.shutdown()
    finally:
        store.close()


def _text(name):
    return (web.STATIC / name).read_text(encoding="utf-8")


def test_the_page_draws_codebase_as_a_view_of_its_rail():
    html = _text("index.html")
    rail = html.split('<nav id="rail"', 1)[1].split("</nav>", 1)[0]
    assert rail.index('id="railResources"') < rail.index('id="railCodebase"') < rail.rindex("<hr>") < rail.index('id="railProfile"')
    assert '<div class="view cb-view" id="viewCodebase" data-view="codebase" hidden>' in html
    assert '<div id="codebaseData"' in html and '<aside id="codebaseSide"' in html
    at = {s: html.index(f'<script src="{s}"></script>') for s in ("app.js", "panels.js", "codebase.js", "codeview.js")}
    assert at["app.js"] < at["panels.js"] < at["codebase.js"] < at["codeview.js"]  # its api and WRITE_HEADERS first
    assert not (web.STATIC / "codebase.js").exists()  # the library's file, never shadowed by one of the page's own
    panels = _text("panels.js")
    assert "databases: 'databases', codebase: 'codebase' };" in panels and "['codebase', 'Codebase', 'branch']" in panels
    assert "$('railCodebase').innerHTML = icon('branch', RAIL_ICON);" in panels
    view = _text("codeview.js")
    assert "C.mount(dataEl, sideEl, { get, send });" in view and "C.unmount();" in view
    assert "if (ev.detail.view === 'codebase') show(); else leave();" in view
    assert "headers: WRITE_HEADERS" in view and "if (b) b.hidden = true;" in view  # an older library: no entry
    css = _text("style.css")
    assert "main > .view.cb-view:not([hidden]) { display: grid; }" in css
    assert "main > .view.cb-view:not([hidden]) { display: block; overflow-y: auto; }" in _text("views-touch.css")  # phones
    assert 'data-pane="codebase"' in web.fetcher_page().decode("utf-8")  # the integrator's page has it too
