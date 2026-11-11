"""The chart app's front (gui/front.py): the chart page here, every request of its pages funnelled
to the integrator's data API at its port (the user, 2026-11-01: "the app needs to funnel requests
through the fetcher to the databases"; 2026-11-03: "for the webserver / ports, i just integrator, and
charts"). The integrator stands in as a small server here; no process starts."""
from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from liminal_charts.gui import front as front_mod, web
from liminal_charts.gui.front import Front, make_front_handler


class Upstream(BaseHTTPRequestHandler):
    """The integrator's data API, as far as the front needs one: it echoes what it got."""

    def log_message(self, *a):
        pass

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", "mc_profile=7; Path=/")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):  # the login app's status: signed in by its cookie
            cookie = self.headers.get("Cookie") or ""
            return self._json(200, {"signedIn": "liminal_login_charts=tok" in cookie, "name": "ada" if "tok" in cookie else None,
                                    "users": 1, "for": "charts", "path": self.path, "cookie": self.headers.get("Cookie")})
        if self.path == "/api/tray":  # a bound app's tray (the login app's)
            return self._json(200, {"app": "login", "icon": "lambda", "title": "Login",
                                    "items": [{"id": "login", "icon": "lambda", "title": "Login", "kind": "app", "app": "login"}]})
        if self.path.startswith("/api/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for i in range(2):
                self.wfile.write(f"data: {i}\n\n".encode())
                self.wfile.flush()
                time.sleep(0.05)
            return
        self._json(200, {"path": self.path, "origin": self.headers.get("Origin"), "site": self.headers.get("Sec-Fetch-Site"),
                         "cookie": self.headers.get("Cookie"), "host": self.headers.get("Host"),
                         "profile": self.headers.get("X-Liminal-Profile")})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        self._json(202, {"got": json.loads(data or b"{}"), "write": self.headers.get("X-Liminal-Charts")})


@pytest.fixture
def upstream():
    up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    yield up
    up.shutdown()
    up.server_close()


def _get(port, path, headers=None):
    conn = HTTPConnection("127.0.0.1", port)
    conn.request("GET", path, headers=headers or {})
    r = conn.getresponse()
    return r.status, r.read(), r


def _free_port() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    port = srv.server_address[1]
    srv.server_close()
    return port


def test_the_front_funnels_its_pages_requests_to_the_integrator(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", str(upstream.server_address[1]))  # the integrator's port: answering
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")  # no login app on this page
    monkeypatch.setattr(front_mod, "start_integrator", lambda db_path, port, log=print: pytest.fail("nothing to start"))
    lines: list[str] = []
    front = Front(tmp_path / "liminal.db", log=lines.append)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        assert not front.connected
        status, body, r = _get(port, "/api/echo?x=1", {"Cookie": "mc_profile=3", "Origin": f"http://127.0.0.1:{port}",
                                                         "Sec-Fetch-Site": "same-origin", "X-Liminal-Profile": "3"})
        j = json.loads(body)
        assert status == 200 and j["path"] == "/api/echo?x=1" and j["cookie"] == "mc_profile=3" and j["profile"] == "3"
        assert j["origin"] is None and j["site"] is None  # where it came from was checked here, not passed on
        # the login app's session cookie stays here: the integrator never sees it
        j = json.loads(_get(port, "/api/echo", {"Cookie": "liminal_login_charts=tok; mc_profile=3"})[1])
        assert j["cookie"] == "mc_profile=3"
        assert json.loads(_get(port, "/api/echo", {"Cookie": "liminal_login_charts=tok"})[1])["cookie"] is None
        # nor a login's of its own (a login on its own, at another port of this machine: cookies ignore ports)
        assert json.loads(_get(port, "/api/echo", {"Cookie": "liminal_login=own; mc_profile=3"})[1])["cookie"] == "mc_profile=3"
        assert j["host"] == f"127.0.0.1:{upstream.server_address[1]}"
        assert r.getheader("Set-Cookie") == "mc_profile=7; Path=/"  # the integrator's cookie passed back
        assert front.connected and any("connected to the integrator" in m for m in lines)
        # another site's page is refused here, before the integrator sees it
        assert _get(port, "/api/echo", {"Origin": "http://evil.example"})[0] == 403
        assert _get(port, "/api/echo", {"Host": "evil.example"})[0] == 403
        # a write needs the app's own header, and goes through with its body
        conn = HTTPConnection("127.0.0.1", port)
        conn.request("POST", "/api/requests", body=json.dumps({"symbol": "AAPL"}),
                     headers={"Content-Type": "application/json", "X-Liminal-Charts": "1"})
        r = conn.getresponse()
        assert r.status == 202 and json.loads(r.read()) == {"got": {"symbol": "AAPL"}, "write": "1"}
        conn.request("POST", "/api/requests", body=b"{}", headers={"Content-Type": "application/json"})
        assert conn.getresponse().status == 403
        # the event stream comes through as it is sent
        status, body, r = _get(port, "/api/stream?since=1")
        assert status == 200 and body == b"data: 0\n\ndata: 1\n\n" and r.getheader("Content-Type").startswith("text/event-stream")
        # the pages: the login page without a profile, the chart page with one, nothing outside
        status, body, _ = _get(port, "/")
        assert status == 200 and b"<title>Liminal Charts" in body and b'id="rail"' not in body
        status, body, _ = _get(port, "/", {"Cookie": "mc_profile=1"})
        assert status == 200 and b'id="rail"' in body and b'data-mode="fetcher"' not in body
        assert _get(port, "/style.css")[0] == 200 and _get(port, "/../pyproject.toml")[0] == 404
        # no page at all (the port off): the page is told, nothing started
        monkeypatch.setenv("LIMINAL_FETCHER_PORT", "none")
        status, body, _ = _get(port, "/api/echo")
        assert status == 503 and b"the integrator is not running" in body and any("has no page" in m for m in lines)
        # no login app on this page: no tray, its prefix is told, and the chart page opens on the profile alone
        assert json.loads(_get(port, "/api/trays")[1]) == {"trays": []}
        assert _get(port, "/login/api/status")[0] == 503 and front.signed_in("mc_profile=1") is None
        assert b'id="rail"' in _get(port, "/", {"Cookie": "mc_profile=1"})[1]
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_front_binds_the_login_app_under_its_prefix_and_draws_its_tray(tmp_path, monkeypatch, upstream):
    """The login app bound (LIMINAL_LOGIN_URL): its page and API served under /login/ with its cookies'
    path narrowed there, its tray in /api/trays; started for the front when nothing answers."""
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", "none")
    monkeypatch.setenv("LIMINAL_LOGIN_URL", f"http://127.0.0.1:{upstream.server_address[1]}/")
    real_start_login = front_mod.start_login
    monkeypatch.setattr(front_mod, "start_login", lambda port, log=print, url="": pytest.fail("nothing to start"))
    lines: list[str] = []
    front = Front(tmp_path / "liminal.db", log=lines.append)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        status, body, _ = _get(port, "/api/trays")
        trays = json.loads(body)["trays"]
        assert status == 200 and [t["app"] for t in trays] == ["login"] and trays[0]["items"][0]["icon"] == "lambda"
        assert any("connected to the login app" in m for m in lines)
        status, body, r = _get(port, "/login/api/status?x=1", {"Cookie": "liminal_login=tok", "X-Liminal-Session": "tok"})
        j = json.loads(body)
        assert status == 200 and j["path"] == "/api/status?x=1" and j["cookie"] == "liminal_login=tok"
        # the main page is the sign-in: without the login's session the chart page is not served, with it
        # and a profile it is; signed in without a profile yet, the sign-in page makes the profile
        assert front.signed_in("mc_profile=1") is False and front.signed_in("liminal_login_charts=tok") is True
        status, body, _ = _get(port, "/", {"Cookie": "mc_profile=1"})
        assert status == 200 and b'id="rail"' not in body and b'id="loginApp"' in body and b"Choose your profile" in body
        assert b'id="rail"' in _get(port, "/", {"Cookie": "mc_profile=1; liminal_login_charts=tok"})[1]
        assert b'id="loginApp"' in _get(port, "/", {"Cookie": "liminal_login_charts=tok"})[1]
        login_js = (front_mod.web.STATIC / "login.js").read_text(encoding="utf-8")
        # the login app's page held there in this page's theme (test_ui_preferences.py)
        assert "fetch('/login/api/status')" in login_js and "`/login/?embedded=1${theme ?" in login_js and "e.data.liminal !== 'login'" in login_js
        assert "post('/api/profiles', { name })" in login_js  # the profile under the account name
        assert "/login/api/sign-out" in (front_mod.web.STATIC / "panels.js").read_text(encoding="utf-8")
        assert r.getheader("Set-Cookie") == "mc_profile=7; Path=/"  # the bound app's cookie, its own path (the main page sends it)
        assert json.loads(_get(port, "/login/")[1])["path"] == "/" and json.loads(_get(port, "/login?embedded=1")[1])["path"] == "/?embedded=1"
        assert _get(port, "/login/api/status", {"Origin": "http://evil.example"})[0] == 403
        conn = HTTPConnection("127.0.0.1", port)
        conn.request("POST", "/login/api/sign-in", body=json.dumps({"name": "ada"}), headers={"Content-Type": "application/json"})
        assert conn.getresponse().status == 403  # a write needs the app's own header
        conn.request("POST", "/login/api/sign-in", body=json.dumps({"name": "ada"}),
                     headers={"Content-Type": "application/json", "X-Liminal-Charts": "1"})
        r = conn.getresponse()
        assert r.status == 202 and json.loads(r.read()) == {"got": {"name": "ada"}, "write": "1"}
        # the chart page draws the bound trays from /api/trays, after the profile's foot
        panels = (front_mod.web.STATIC / "panels.js").read_text(encoding="utf-8")
        assert "fetch('/api/trays')" in panels and "bound-${t.app}" in panels and "/login/api/status" in panels
        assert 'id="viewBound"' in (front_mod.web.STATIC / "index.html").read_text(encoding="utf-8")
    finally:
        srv.shutdown()
        srv.server_close()
    # nothing answers at the login's URL: started for the front (the library's start_app, waited for)
    free = _free_port()
    monkeypatch.setenv("LIMINAL_LOGIN_URL", f"http://127.0.0.1:{free}")
    monkeypatch.setattr(front_mod, "START_SECONDS", 0.3)
    started: list = []
    up: list = []

    def start_login(port, log=print, url=""):
        started.append(port)
        srv = ThreadingHTTPServer(("127.0.0.1", port), Upstream)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        up.append(srv)
        return True
    monkeypatch.setattr(front_mod, "start_login", start_login)
    front = Front(tmp_path / "liminal.db", log=lines.append)
    assert front.login().port == free and started == [free] and front.login().port == free and started == [free]
    up[0].shutdown()
    up[0].server_close()
    monkeypatch.setattr(front_mod, "start_login", lambda port, log=print, url="": False)
    assert front.login() is None and front.trays() == []
    # the start itself: the login's module, no window, until quiet (the module stood in for: CI has no login app)
    import sys
    import types

    from liminal_libs.apps import binding as binding_mod
    popen: list = []
    monkeypatch.setattr(binding_mod.subprocess, "Popen", lambda args, **kw: popen.append((args, kw)))
    monkeypatch.setitem(sys.modules, "liminal_login", types.ModuleType("liminal_login"))
    assert real_start_login(8795, lines.append, url="http://127.0.0.1:8765") is True
    assert popen[0][0][1:] == ["-m", "liminal_login", "serve", "--no-browser", "--port", "8795", "--until-quiet"]
    # the login on its own knows no app: the front says it inherits it, how it is named, where its plugins' pairs are
    env = popen[0][1]["env"]
    assert (env["LIMINAL_LOGIN_FOR"], env["LIMINAL_LOGIN_FOR_TITLE"], env["LIMINAL_LOGIN_FOR_URL"]) == (
        "charts", "Liminal Charts", "http://127.0.0.1:8765")
    # the login's file, when there is one, goes with it: what the login is tied to (the user, 2026-11-06)
    from liminal_libs.apps import Scope

    monkeypatch.setattr(front_mod.Scope, "find", classmethod(lambda cls, start=None, env=None: None))
    assert real_start_login(8795, lines.append) is True and "LIMINAL_LOGIN_CFG" not in popen[1][1]["env"]
    found = Scope.local(tmp_path, title="Here", path=tmp_path / "liminal-login.cfg")
    monkeypatch.setattr(front_mod.Scope, "find", classmethod(lambda cls, start=None, env=None: found))
    assert real_start_login(8795, lines.append) is True and popen[2][1]["env"]["LIMINAL_LOGIN_CFG"] == str(tmp_path / "liminal-login.cfg")
    assert front_mod.login_url() == f"http://127.0.0.1:{free}"
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "off")
    assert front_mod.login_url() is None
    monkeypatch.delenv("LIMINAL_LOGIN_URL")
    assert front_mod.login_url() == "http://127.0.0.1:8795"


def test_the_front_starts_the_integrator_when_nothing_answers_at_its_port(tmp_path, monkeypatch, upstream):
    """Nothing answers at the integrator's port: the front starts the integrator's server for itself
    (`liminal-integrator serve --no-browser --until-quiet`) and waits for it; none could be started,
    or it never answers: the page is told."""
    free = _free_port()
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", str(free))
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    monkeypatch.setattr(front_mod, "START_SECONDS", 0.3)
    real_start = front_mod.start_integrator
    started: list = []
    up: list = []

    def start_integrator(db_path, port, log=print):
        started.append((db_path, port))
        srv = ThreadingHTTPServer(("127.0.0.1", port), Upstream)  # the integrator's server comes up at its port
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        up.append(srv)
        return True
    monkeypatch.setattr(front_mod, "start_integrator", start_integrator)
    lines: list[str] = []
    front = Front(tmp_path / "liminal.db", log=lines.append)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    try:
        status, body, _ = _get(port, "/api/echo")
        assert status == 200 and json.loads(body)["host"] == f"127.0.0.1:{free}" and started == [(tmp_path / "liminal.db", free)]
        assert front.connected and any("connected to the integrator" in m for m in lines)
        assert _get(port, "/api/echo")[0] == 200 and len(started) == 1  # answering: not started again
        up[0].shutdown()
        up[0].server_close()
        # the integrator is not installed: nothing could be started
        monkeypatch.setattr(front_mod, "start_integrator", lambda db_path, port, log=print: False)
        status, body, _ = _get(port, "/api/echo")
        assert status == 503 and b"none could be started" in body
        # started, but it never answers
        monkeypatch.setattr(front_mod, "start_integrator", lambda db_path, port, log=print: True)
        status, body, _ = _get(port, "/api/echo")
        assert status == 503 and any("never answered" in m for m in lines)
    finally:
        srv.shutdown()
        srv.server_close()
    # the start itself (the library's start_app): the integrator's module, no window, on this database, until quiet
    from liminal_libs.apps import binding as binding_mod
    popen: list = []
    monkeypatch.setattr(binding_mod.subprocess, "Popen", lambda args, **kw: popen.append((args, kw)))
    assert real_start(tmp_path / "liminal.db", 8791, lines.append) is True
    args, kw = popen[0]
    assert args[1:] == ["-m", "liminal_integrator", "serve", "--no-browser", "--port", "8791", "--until-quiet"]
    assert kw["env"]["LIMINAL_DB"] == str(tmp_path / "liminal.db") and kw["close_fds"] is True
    assert front_mod.integrator_port() == free
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", "0")
    assert front_mod.integrator_port() is None  # a free one: this front cannot know it
    monkeypatch.delenv("LIMINAL_FETCHER_PORT")
    assert front_mod.integrator_port() == 8785


def test_serve_front_takes_a_free_port_when_its_own_is_taken_and_stops_when_told(tmp_path, upstream, monkeypatch):
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    taken = upstream.server_address[1]
    assert web.listening(taken)
    lines: list[str] = []
    stop = threading.Event()
    done = threading.Event()

    def run():
        front_mod.serve_front(tmp_path / "liminal.db", port=taken, open_browser=False, log=lines.append, stop=stop)
        done.set()
    threading.Thread(target=run, daemon=True).start()
    end = time.monotonic() + 5
    while not (urls := [m for m in lines if m.startswith("front: the chart's page at")]) and time.monotonic() < end:
        time.sleep(0.02)
    served = int(urls[0].split("http://127.0.0.1:", 1)[1].split("/", 1)[0])
    assert served != taken and any(f"port {taken} is taken" in m for m in lines)
    assert b"<title>Liminal Charts" in _get(served, "/")[1]
    stop.set()
    assert done.wait(5)
    assert not web.listening(served)


def test_the_gui_command_runs_the_front_unless_alone(tmp_path, monkeypatch):
    from liminal_charts import cli
    from liminal_charts.gui import front, server

    ran: list = []
    monkeypatch.setattr(front, "serve_front", lambda db_path, port=8765, open_browser=True, log=None: ran.append(("front", port, open_browser)))
    monkeypatch.setattr(server, "serve", lambda *a, **kw: ran.append(("alone", kw.get("port"))))
    settings = cli.load_settings(env={}, env_file=None)
    monkeypatch.delenv("LIMINAL_NO_RESCAN", raising=False)
    args = cli.build_parser().parse_args(["gui", "--no-browser", "--port", "8770", "--no-rescan"])
    assert cli.cmd_gui(args, settings) == 0 and ran == [("front", 8770, False)]
    assert front_mod.config.setting("LIMINAL_NO_RESCAN") or __import__("os").environ.get("LIMINAL_NO_RESCAN") == "1"
    args = cli.build_parser().parse_args(["gui", "--alone", "--no-browser"])
    assert cli.cmd_gui(args, settings) == 0 and ran[-1] == ("alone", 8765)


def test_the_front_tells_the_login_it_inherits_the_pairs_its_plugins_ask_for(tmp_path, monkeypatch):
    """The login on its own knows no plugin (the user, 2026-11-10: "the log-in app should not point to platform,
    integrator or charts"; "when its inherited it can point to it's superclass"): the front, which inherits it, answers
    GET /api/login/pairs with the pairs the charts app's plugins ask for, named by their variables (the library's
    pairs_wanted), and nothing of it goes to the integrator."""
    from types import SimpleNamespace

    from liminal_libs.sources import registry
    from liminal_libs.sources.keys import KeyVar

    acme = SimpleNamespace(name="acme", title="Acme", part_of=None, SYSTEM="integrator", keys=(KeyVar("ACME_API_KEY"),))
    sage = SimpleNamespace(name="sage", title="Sage", part_of=None, SYSTEM="platform",
                           keys=(KeyVar("SAGE_API_KEY"), KeyVar("SAGE_ACCOUNT", "account (email or name)", secret=False)))
    monkeypatch.setattr(registry, "providers", lambda: {"acme": acme})
    monkeypatch.setattr(registry, "publishers", lambda: {})
    monkeypatch.setattr(registry, "assistants", lambda: {"sage": sage})
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", "none")  # nothing passed on: the front answers it itself
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    front = Front(tmp_path / "liminal.db", log=lambda m: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, body, _ = _get(srv.server_address[1], "/api/login/pairs")
        pairs = json.loads(body)["pairs"]
        assert status == 200 and [p["name"] for p in pairs] == ["acme-api-key", "sage-account", "sage-api-key"]
        assert pairs[1] == {"name": "sage-account", "variable": "SAGE_ACCOUNT", "plugin": "sage", "title": "Sage",
                            "label": "account (email or name)", "secret": False, "kind": "detail", "system": "platform"}
        assert _get(srv.server_address[1], "/api/login/pairs", {"Origin": "http://evil.example"})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()
