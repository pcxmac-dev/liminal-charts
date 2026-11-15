"""The chart app's front (gui/front.py): the chart page here, every request of its pages funnelled
to the integrator's data API at its port (the user, 2026-11-01: "the app needs to funnel requests
through the fetcher to the databases"; 2026-11-03: "for the webserver / ports, i just integrator, and
charts"), the integrator linked and never started from here, the login the charts app inherits held
here as an object (2026-11-14: "don't start any of the applications in the background"; "the platform
and integrator inherit the login function, they are separate login instances, class vs object"). The
integrator stands in as a small server and the login class as a small App; no process starts."""
from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from liminal_libs.apps import App
from liminal_charts.frame import ChartsApp
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


class StandInLogin(App):
    """The login class as far as the front needs it: made with the charts app as its superclass, a session by its
    cookie, its page, a sign-in that sets its cookie."""

    NAME, ICON = "login", "lambda"

    def __init__(self, log=print, superclass=None):
        super().__init__(log)
        self.superclass, self.TITLE = superclass, f"Login: {superclass.TITLE}"

    def session(self, handler):
        return "a session" if "liminal_login_charts=tok" in (handler.headers.get("Cookie") or "") else None

    def api(self, handler, method, path, query, body):
        if method == "GET" and path == "/api/status":
            return 200, {"signedIn": self.session(handler) is not None, "path": path, "query": query,
                         "cookie": handler.headers.get("Cookie"), "for": self.superclass.NAME}
        if method == "POST" and path == "/api/sign-in":
            return 202, {"got": body, "write": handler.headers.get("X-Liminal-Charts")}, \
                [("Set-Cookie", "liminal_login_charts=tok; Path=/; HttpOnly")]
        return super().api(handler, method, path, query, body)


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


def _req(cookie: str):
    """A request as far as the front's sign-in check reads one: its cookies."""
    return SimpleNamespace(headers={"Cookie": cookie})


def _free_port() -> int:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    port = srv.server_address[1]
    srv.server_close()
    return port


def _serve(front):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_the_front_funnels_its_pages_requests_to_the_integrator(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", str(upstream.server_address[1]))  # the integrator's port: answering
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")  # no login on this page
    lines: list[str] = []
    front = Front(tmp_path / "liminal.db", log=lines.append)
    srv, port = _serve(front)
    try:
        assert not front.connected
        status, body, r = _get(port, "/api/echo?x=1", {"Cookie": "mc_profile=3", "Origin": f"http://127.0.0.1:{port}",
                                                         "Sec-Fetch-Site": "same-origin", "X-Liminal-Profile": "3"})
        j = json.loads(body)
        assert status == 200 and j["path"] == "/api/echo?x=1" and j["cookie"] == "mc_profile=3" and j["profile"] == "3"
        assert j["origin"] is None and j["site"] is None  # where it came from was checked here, not passed on
        # the login's session cookie stays here: the integrator never sees it
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
        # no login on this page: no tray, its prefix is told, and the chart page opens on the profile alone
        assert json.loads(_get(port, "/api/trays")[1]) == {"trays": []}
        assert _get(port, "/login/api/status")[0] == 503 and front.signed_in(_req("mc_profile=1")) is None
        assert b'id="rail"' in _get(port, "/", {"Cookie": "mc_profile=1"})[1]
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_front_holds_the_login_it_inherits_and_draws_its_tray(tmp_path, monkeypatch):
    """The login the charts app inherits: an object of the login class the front holds (its superclass the charts
    app, the one thing it points to), answered under /login/ at the front's own port with its cookies, its tray in
    /api/trays, the main page the sign-in until the browser is signed in there. No login process, no port of its own."""
    pages = tmp_path / "login-pages"
    pages.mkdir()
    (pages / "index.html").write_text("<html>the login page</html>", encoding="utf-8")
    StandInLogin.STATIC = pages
    monkeypatch.setattr(ChartsApp, "LOGIN_CLASS", StandInLogin)
    for var in ("LIMINAL_CHARTS_LOGIN", "LIMINAL_LOGIN_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", "none")
    front = Front(tmp_path / "liminal.db", log=lambda m: None)
    srv, port = _serve(front)
    try:
        trays = json.loads(_get(port, "/api/trays")[1])["trays"]
        assert [t["app"] for t in trays] == ["login"] and trays[0]["icon"] == "lambda" and trays[0]["title"] == "Login: Liminal Charts"
        st = json.loads(_get(port, "/login/api/status?x=1", {"Cookie": "liminal_login_charts=tok"})[1])
        assert st == {"signedIn": True, "path": "/api/status", "query": {"x": "1"}, "cookie": "liminal_login_charts=tok",
                      "for": "charts"}
        # the main page is the sign-in: without the login's session the chart page is not served, with it and a
        # profile it is; signed in without a profile yet, the sign-in page makes the profile
        assert front.signed_in(_req("mc_profile=1")) is False and front.signed_in(_req("liminal_login_charts=tok")) is True
        status, body, _ = _get(port, "/", {"Cookie": "mc_profile=1"})
        assert status == 200 and b'id="rail"' not in body and b'id="loginApp"' in body and b"Choose your profile" in body
        assert b'id="rail"' in _get(port, "/", {"Cookie": "mc_profile=1; liminal_login_charts=tok"})[1]
        assert b'id="loginApp"' in _get(port, "/", {"Cookie": "liminal_login_charts=tok"})[1]
        login_js = (front_mod.web.STATIC / "login.js").read_text(encoding="utf-8")
        # the login's page held there in this page's theme (test_ui_preferences.py)
        assert "fetch('/login/api/status')" in login_js and "`/login/?embedded=1${theme ?" in login_js and "e.data.liminal !== 'login'" in login_js
        assert "post('/api/profiles', { name })" in login_js  # the profile under the account name
        assert "/login/api/sign-out" in (front_mod.web.STATIC / "panels.js").read_text(encoding="utf-8")
        assert _get(port, "/login/")[1] == b"<html>the login page</html>" == _get(port, "/login?embedded=1")[1]
        assert _get(port, "/login/api/status", {"Origin": "http://evil.example"})[0] == 403
        conn = HTTPConnection("127.0.0.1", port)
        conn.request("POST", "/login/api/sign-in", body=json.dumps({"name": "ada"}), headers={"Content-Type": "application/json"})
        assert conn.getresponse().status == 403  # a write needs the app's own header
        conn.request("POST", "/login/api/sign-in", body=json.dumps({"name": "ada"}),
                     headers={"Content-Type": "application/json", "X-Liminal-Charts": "1"})
        r = conn.getresponse()
        assert r.status == 202 and json.loads(r.read()) == {"got": {"name": "ada"}, "write": "1"}
        assert r.getheader("Set-Cookie") == "liminal_login_charts=tok; Path=/; HttpOnly"  # at /: the main page sends it
        # the chart page draws the trays from /api/trays, after the profile's foot
        panels = (front_mod.web.STATIC / "panels.js").read_text(encoding="utf-8")
        assert "fetch('/api/trays')" in panels and "bound-${t.app}" in panels and "/login/api/status" in panels
        assert 'id="viewBound"' in (front_mod.web.STATIC / "index.html").read_text(encoding="utf-8")
        # one object, its superclass the charts app the front holds
        assert front.login_object() is front.login_object() and front.login_object().superclass is front.charts
        assert not hasattr(front_mod, "start_login") and not hasattr(front_mod, "start_integrator")
    finally:
        srv.shutdown()
        srv.server_close()
        front.close()
    # turned off: no login, the profile alone; an address given is not reached (the login is held here)
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "off")
    assert Front(tmp_path / "liminal.db", log=lambda m: None).login_object() is None
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "http://127.0.0.1:8795")
    assert Front(tmp_path / "liminal.db", log=lambda m: None).login_object() is not None
    monkeypatch.delenv("LIMINAL_LOGIN_URL")
    monkeypatch.setenv("LIMINAL_CHARTS_LOGIN", "none")
    assert Front(tmp_path / "liminal.db", log=lambda m: None).login_object() is None


def test_the_front_never_starts_the_integrator_it_links(tmp_path, monkeypatch):
    """The integrator is linked, reached at its port (the user, 2026-11-14: "don't start any of the applications in
    the background"; "the idea is for an app like charts to be able to 'link' to an app like platform or an app like
    integrator or both"): nothing answering there, the page is told it is not running (said once in the log) and
    nothing is started; once it answers (the user started it), the front is connected."""
    free = _free_port()
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", str(free))
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    lines: list[str] = []
    front = Front(tmp_path / "liminal.db", log=lines.append)
    srv, port = _serve(front)
    try:
        for _ in range(2):
            status, body, _ = _get(port, "/api/echo")
            assert status == 503 and b"the integrator is not running" in body and b"nothing is started from here" in body
        assert sum("the integrator is not running" in m for m in lines) == 1 and not front.connected
        assert not web.listening(free)  # nothing came up at its port
        up = ThreadingHTTPServer(("127.0.0.1", free), Upstream)  # the user starts it
        threading.Thread(target=up.serve_forever, daemon=True).start()
        try:
            status, body, _ = _get(port, "/api/echo")
            assert status == 200 and json.loads(body)["host"] == f"127.0.0.1:{free}" and front.connected
            assert any("connected to the integrator" in m for m in lines)
        finally:
            up.shutdown()
            up.server_close()
    finally:
        srv.shutdown()
        srv.server_close()
    assert front_mod.integrator_port() == free
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", "0")
    assert front_mod.integrator_port() is None  # a free one: this front cannot know it
    monkeypatch.delenv("LIMINAL_FETCHER_PORT")
    assert front_mod.integrator_port() == 8785


def test_serve_front_refuses_a_taken_port_and_stops_when_told(tmp_path, upstream, monkeypatch):
    """The library's 1.52.0 (the user, 2026-11-14: "i only want ports for liminal-login ; liminal-platform ;
    liminal-charts ; liminal-integrator"; "never provide ports for anything else"): a front whose port something else
    answers on is not started, nor on another port; on a port of its own it serves until told to stop."""
    from liminal_libs.apps import PortTaken

    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    taken = upstream.server_address[1]
    assert web.listening(taken)
    lines: list[str] = []
    with pytest.raises(PortTaken) as e:
        front_mod.serve_front(tmp_path / "liminal.db", port=taken, open_browser=False, log=lines.append)
    assert e.value.port == taken and any(f"port {taken} is taken" in m for m in lines)
    assert not any(m.startswith("front: the chart's page at") for m in lines)
    stop = threading.Event()
    done = threading.Event()

    def run():
        front_mod.serve_front(tmp_path / "liminal.db", port=0, open_browser=False, log=lines.append, stop=stop)
        done.set()
    threading.Thread(target=run, daemon=True).start()
    end = time.monotonic() + 5
    while not (urls := [m for m in lines if m.startswith("front: the chart's page at")]) and time.monotonic() < end:
        time.sleep(0.02)
    served = int(urls[0].split("http://127.0.0.1:", 1)[1].split("/", 1)[0])
    assert served != taken and b"<title>Liminal Charts" in _get(served, "/")[1]
    stop.set()
    assert done.wait(5)
    assert not web.listening(served)


def test_the_gui_command_runs_the_front_unless_alone(tmp_path, monkeypatch):
    from liminal_libs.apps import PortTaken

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

    def taken(db_path, port=8765, open_browser=True, log=None):  # its port held by something else: said, 1
        raise PortTaken(ChartsApp(log=lambda m: None), port)
    monkeypatch.setattr(front, "serve_front", taken)
    assert cli.cmd_gui(cli.build_parser().parse_args(["gui", "--no-browser", "--port", "8770"]), settings) == 1


def test_the_front_tells_the_login_it_inherits_the_pairs_its_plugins_ask_for(tmp_path, monkeypatch):
    """The login knows no plugin (the user, 2026-11-10: "the log-in app should not point to platform, integrator or
    charts"; "when its inherited it can point to it's superclass"): the charts app the front holds answers the login
    with the pairs the charts app's plugins ask for, named by their variables (the library's pairs_wanted), and so does
    GET /api/login/pairs (a login run on its own asks it there); nothing of it goes to the integrator."""
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
    srv, port = _serve(front)
    try:
        status, body, _ = _get(port, "/api/login/pairs")
        pairs = json.loads(body)["pairs"]
        assert status == 200 and [p["name"] for p in pairs] == ["acme-api-key", "sage-account", "sage-api-key"]
        assert pairs[1] == {"name": "sage-account", "variable": "SAGE_ACCOUNT", "plugin": "sage", "title": "Sage",
                            "label": "account (email or name)", "secret": False, "kind": "detail", "system": "platform"}
        assert front.charts.login_pairs() == pairs  # what the login held here asks of the charts app, directly
        assert _get(port, "/api/login/pairs", {"Origin": "http://evil.example"})[0] == 403
    finally:
        srv.shutdown()
        srv.server_close()
