"""The charts app on the library's platform core (0.2.8, Monday list item 30, step 5; the user, 2026-11-24: "inheritance
bro"; 2026-11-25: the instance forms move to the platform's Databases section, the platform the only writer of instance
settings, the charts' view keeping its list and linking there). ChartsApp inherits liminal_libs.apps.platform.Platform,
needs the database plugins and is not the writer; the front refuses a change before it reaches any integrator and
passes a test on; the Databases view links into the platform's page for each plugin and posts no change. The integrator
stands in as a small server; no process starts and no database plugin's settings are read or written."""
from __future__ import annotations

import json
import re
import threading
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from liminal_libs.apps import App, Refused
from liminal_libs.apps import databases as db_section
from liminal_libs.apps.platform import ELSEWHERE, Platform
from liminal_libs.storage import Store

from liminal_charts.frame import ChartsApp
from liminal_charts.gui import front as front_mod
from liminal_charts.gui import server
from liminal_charts.gui.front import Front, make_front_handler
from liminal_charts.gui.server import ChartApp, make_handler

STATIC = Path(server.__file__).parent / "static"
CHANGES = ("new", "connect", "share", "port", "credentials", "forget")


def _js(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_the_charts_app_inherits_the_platform_core_needs_the_databases_and_is_not_the_writer():
    frame = ChartsApp(log=lambda m: None)
    assert isinstance(frame, Platform) and isinstance(frame, App)
    assert ChartsApp.PLATFORM_NEEDS == ("database",) and frame.needs("database") and not frame.needs("machine")
    assert frame.is_platform is False and ChartsApp.PLATFORM_WRITER is False  # the running platform is the one writer
    assert set(CHANGES) == set(db_section.CHANGES)  # the changes the page no longer posts, as the library names them
    for action in db_section.CHANGES:  # a change refused before anything is read or asked
        with pytest.raises(Refused) as e:
            frame.instances_asked({"plugin": "sqlite", "action": action, "name": "archive", "confirm": True})
        assert e.value.status == HTTPStatus.CONFLICT and e.value.reason == ELSEWHERE
    assert frame.plugins() == {}  # nothing attached by a refusal


def test_alone_the_charts_data_api_answers_a_test_and_refuses_every_change(tmp_path, monkeypatch):
    """gui --alone: the charts app's own frame answers the Databases routes (its inherited platform part): a test is
    asked of the section, a change answered 409 with the words, and the section never asked."""
    asked = []

    def ask(app, body):
        asked.append(body)
        return {"ok": True, "said": f"{body.get('action')} {body.get('name')}"}
    monkeypatch.setattr(db_section, "ask", ask)
    store = Store(tmp_path / "liminal.db", check_same_thread=False)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ChartApp(store), [0]))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]

            def post(form):
                c = HTTPConnection("127.0.0.1", port)
                c.request("POST", "/api/databases/instances", body=json.dumps(form),
                          headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json", "X-Liminal-Charts": "1"})
                r = c.getresponse()
                return r.status, json.loads(r.read())
            assert post({"plugin": "sqlite", "action": "test", "name": "main"}) == (200, {"ok": True, "said": "test main"})
            for action in CHANGES:
                assert post({"plugin": "sqlite", "action": action, "name": "archive", "confirm": True}) == (
                    409, {"error": ELSEWHERE})
            assert asked == [{"plugin": "sqlite", "action": "test", "name": "main"}]
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        store.close()


class Integrator(BaseHTTPRequestHandler):
    """The integrator's data API as far as the forms need one: what it was sent, kept."""

    got: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        type(self).got.append((self.path, body))
        data = json.dumps({"ok": True, "said": f"{body.get('action')} {body.get('name')}"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_the_front_passes_a_test_on_and_refuses_a_change_before_any_integrator_sees_it(tmp_path, monkeypatch):
    Integrator.got = []
    up = ThreadingHTTPServer(("127.0.0.1", 0), Integrator)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", str(up.server_address[1]))
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    monkeypatch.setenv("LIMINAL_PLATFORM_URL", "none")
    front = Front(tmp_path / "liminal.db", log=lambda m: None, name="the charts")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        conn = HTTPConnection("127.0.0.1", port)  # one connection kept alive through all of them

        def post(form, path="/api/databases/instances"):
            conn.request("POST", path, body=json.dumps(form),
                         headers={"Content-Type": "application/json", "X-Liminal-Charts": "1"})
            r = conn.getresponse()
            return r.status, json.loads(r.read())
        test = {"plugin": "sqlite", "action": "test", "name": "main"}
        assert post(test) == (200, {"ok": True, "said": "test main"})
        for action in CHANGES:
            status, said = post({"plugin": "duckdb", "action": action, "name": "archive", "confirm": True})
            assert status == 409 and said["platform"] == "/platform/#duckdb"
            assert said["error"].startswith("Change this on the platform's Databases section: the charts changes no instance")
        # the body read here goes on as it came, and the connection's next request is read from the connection again
        assert post({**test, "name": "second"}) == (200, {"ok": True, "said": "test second"})
        assert Integrator.got == [("/api/databases/instances", test),
                                  ("/api/databases/instances", {**test, "name": "second"})]  # no change reached it
        # another write of the page is passed on as before
        assert post({"symbol": "AAPL"}, path="/api/requests")[0] == 200 and Integrator.got[-1][0] == "/api/requests"
        # a change still needs the app's own header first: refused 403, never 409, never passed on
        conn.request("POST", "/api/databases/instances", body=json.dumps({"action": "forget", "name": "x"}),
                     headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        assert (r.status, r.read()) == (403, b"missing request header") and len(Integrator.got) == 3
        conn.close()
    finally:
        srv.shutdown()
        srv.server_close()
        up.shutdown()
        up.server_close()
    assert front_mod.platform_page("sqlite") == "/platform/#sqlite"
    assert front_mod.platform_page("sqlite", "external") == "/platform/#sqlite/external"
    assert front_mod.platform_page("sqlite", "internal") == "/platform/#sqlite/internal"
    assert front_mod.platform_page() == "/platform/" and front_mod.platform_page("a b") == "/platform/#a%20b"


def test_the_databases_view_links_each_plugin_into_the_platforms_databases_section():
    d = _js("databases.js")
    # the list, the figures and the instances stay; the plugin picked shows its instances as before
    assert "engines = await api('/api/databases/plugins');" in d and ">Engines</h4>" in d and 'data-db="plugin:${esc(p.name)}"' in d
    assert "<h4>Instances</h4>" in d and "(p.instances || []).map((i) => instanceRow(p, i))" in d
    assert "api('/api/databases')" in d and "api('/api/databases/detail', { id })" in d
    # the links: the platform's page for the plugin, its Main, External API and Internal API tabs, under /platform/
    # (the front's PLATFORM_PREFIX), each with the words that say a change is made there
    assert front_mod.PLATFORM_PREFIX == "/platform"
    assert "return window.FETCHER_MODE ? null : '/platform/';" in d  # the integrator's own page links no platform
    assert "`${base}#${encodeURIComponent(p.name)}${tab ? `/${tab}` : ''}`" in d
    tabs = re.search(r"const PLATFORM_TABS = \[(.*?)\]\];", d, re.S).group(1)
    assert [t for t in re.findall(r"\['(\w*)', '([^']+)'", tabs)] == [("", "Main"), ("external", "External API"),
                                                                        ("internal", "Internal API")]
    assert "const ELSEWHERE = \"change this on the platform's Databases section\";" in d
    assert ELSEWHERE == "change this on the platform's Databases section"  # the library's own words
    link = d.split("function platformLink(", 1)[1].split("\n  }\n", 1)[0]
    assert 'href="${esc(href)}"' in link and 'data-platform="${esc(platformItem(p, tab))}"' in link and "ELSEWHERE" in link
    assert "${esc(ELSEWHERE)}" in d.split("function platformHtml(", 1)[1].split("\n  }\n", 1)[0]
    assert "${platformHtml(p)}${slot(saidHtml(p))}" in d  # under the instances, on the plugin's page
    assert "platformLink(p, '', 'Change…'," in d  # each instance's change: the platform's page for it
    # opened in the bound view when the platform's tray is on the rail (the icon bridge), else as a link
    assert "window.Views.bound?.('platform', link.dataset.platform)" in d
    panels = _js("panels.js")
    assert "bound: openBound }" in panels and "BOUND_ITEM[app] = String(item || '');" in panels
    assert "if (!BOUND.has(app)) return false;" in panels and "showPane(`bound-${app}`, { toggle: false });" in panels
    # its API card stays, as every plugin's page shows it
    assert "window.LiminalApis.html(p.apis || [], p.apiFacts || {}, { owner: p.name, fold: true })" in d
    assert ".db-links {" in _js("style.css") and ".db-link {" in _js("style.css")


def test_the_databases_view_posts_no_change_test_alone_a_read():
    d = _js("databases.js")
    # one request that writes, and its action is always "test"
    assert d.count("fetch(") == 1 and d.count("method: 'POST'") == 1
    assert ("fetch('/api/databases/instances', { method: 'POST', headers: WRITE_HEADERS,\n"
            "      body: JSON.stringify({ plugin: p.name, action: 'test', name }) });") in d.replace("\r\n", "\n")
    assert "sendTest(p, name)" in d and "test(p, act.dataset.inst || null);" in d
    assert "closest('[data-do=\"test\"]')" in d  # the one action the page answers
    # no form, no field, no question before a change, no change's words
    for gone in ("<form", "db-form", "<input", "<select", "type=\"password\"", "addEventListener('submit'", "confirm",
                 "pending", "FormData", "data-do=\"new\"", "data-do=\"connect\"", "'share'", "'port'", "'signin'",
                 "'forget'", "'credentials'", "action: kind", "question("):
        assert gone not in d, gone
    css = _js("style.css")
    for gone in (".db-form {", ".db-ask", ".db-field {"):
        assert gone not in css, gone
    assert ".db-said.failed" in css  # a test's answer is still said
