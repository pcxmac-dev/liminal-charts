"""The integrator's page holds the integrator's own login (liminal-integrator 1.9.0; the user, 2026-11-14: "the platform
and integrator inherit the login function, they are separate login instances, class vs object"): an object of the login
class its frame holds, answered under /login/ at the integrator's port before the app wakes, the page its sign-in until
signed in there, its tray at /api/trays; the data API is not behind it (the chart app's front reads it). A stand-in
plays the login class, another the integrator's frame; no process starts."""
from __future__ import annotations

import http.client
import json
import threading
from dataclasses import replace

from liminal_libs.apps import App, EnvironmentLogin
from liminal_libs.config import load_settings
from liminal_charts.gui import server


class FakeLink:
    """What serve_integrator needs of the integrator's link to the fetcher (as tests/test_fetcher_page.py's)."""

    def __init__(self):
        self.touched, self.woken, self.passes, self.let = 0, 0, 0, 0

    def touch(self):
        self.touched += 1

    def status(self):
        return {"pid": 4242, "busy": False, "clients": [4242], "feeds": []}

    def wake_up(self):
        self.woken += 1

    def ask_pass(self):
        self.passes += 1

    def let_go(self):
        self.let += 1
        return True

    def watch_quiet(self, stop, on_quiet=None):
        stop.wait()


class StandInLogin(App):
    NAME, ICON = "login", "lambda"

    def __init__(self, log=print, superclass=None):
        super().__init__(log)
        self.superclass, self.TITLE = superclass, f"Login: {superclass.TITLE}"

    def session(self, handler):
        return "a session" if "liminal_login_integrator=tok" in (handler.headers.get("Cookie") or "") else None

    def api(self, handler, method, path, query, body):
        if method == "GET" and path == "/api/status":
            return 200, {"signedIn": self.session(handler) is not None, "for": self.superclass.NAME}
        if method == "POST" and path == "/api/sign-in":
            return 200, {"got": body}, [("Set-Cookie", "liminal_login_integrator=tok; Path=/; HttpOnly")]
        return super().api(handler, method, path, query, body)


class Integratorish(App):
    """The integrator's frame as its page needs it (liminal_integrator.app.IntegratorApp): it inherits the login."""

    NAME, TITLE, ICON = "integrator", "Liminal Integrator", "scroll"
    LOGIN, LOGIN_VAR = True, "LIMINAL_INTEGRATOR_LOGIN"
    LOGIN_CLASS = StandInLogin

    def __init__(self, log=print):
        super().__init__(log=log, login=EnvironmentLogin(env={}))

    def attach_plugins(self, only=None):
        return []


def _serve(tmp_path, frame, said):
    """The integrator's server in a thread, its ready line waited for (conftest's Said: an event, not a 5 s deadline,
    which ran out on CI windows-3.14 on 2026-11-24 and failed on an IndexError)."""
    settings = replace(load_settings(env={}, env_file=None), db_path=tmp_path / "liminal.db")
    link, log, stop = FakeLink(), said("integrator: its page and the data API at"), threading.Event()

    def run():
        try:
            server.serve_integrator(settings, 0, link=link, log=log, stop=stop, frame=frame)
        finally:
            log.ended()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return log.port(), link, stop, thread


def _ended(stop, thread, join):
    stop.set()
    join(thread, "the integrator's server")


def _call(port, method, path, body=None, cookie=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    h = dict(headers or {})
    if body is not None:
        h.update({"Content-Type": "application/json", "X-Liminal-Charts": "1"})
    if cookie:
        h["Cookie"] = cookie
    conn.request(method, path, body=None if body is None else json.dumps(body).encode(), headers=h)
    r = conn.getresponse()
    return r.status, r.read(), r


def test_the_integrators_page_is_its_logins_sign_in_until_signed_in_there(tmp_path, monkeypatch, said, join):
    monkeypatch.delenv("LIMINAL_INTEGRATOR_LOGIN", raising=False)
    frame = Integratorish(log=lambda m: None)
    port, link, stop, thread = _serve(tmp_path, frame, said)
    try:
        status, body, _ = _call(port, "GET", "/")
        assert status == 200 and b'id="signinFrame"' in body and b'data-mode="fetcher"' not in body  # its sign-in first
        assert b'id="signinFrame"' in _call(port, "GET", "/signin.html")[1]
        body = _call(port, "GET", "/", cookie="liminal_login_integrator=tok")[1]
        assert b'<body data-mode="fetcher">' in body  # signed in there: the integrator's page
        assert b'<body data-mode="fetcher">' in _call(port, "GET", "/signin.html", cookie="liminal_login_integrator=tok")[1]
        touched = link.touched
        st = json.loads(_call(port, "GET", "/login/api/status", cookie="liminal_login_integrator=tok")[1])
        assert st == {"signedIn": True, "for": "integrator"}
        status, body, r = _call(port, "POST", "/login/api/sign-in", {"name": "secret"})
        assert status == 200 and json.loads(body) == {"got": {"name": "secret"}}
        assert r.getheader("Set-Cookie") == "liminal_login_integrator=tok; Path=/; HttpOnly"
        assert link.touched == touched  # the login answered without waking the app (the fetcher's link not touched)
        assert _call(port, "GET", "/login/api/status", headers={"Origin": "http://evil.example"})[0] == 403
        trays = json.loads(_call(port, "GET", "/api/trays")[1])["trays"]
        assert [t["app"] for t in trays] == ["login"] and trays[0]["title"] == "Login: Liminal Integrator"
        # the data API is not behind it: the chart app's front reads it without the integrator's session
        assert _call(port, "GET", "/api/requests")[0] == 200
        assert frame.login_object().superclass is frame and frame.served == f"http://127.0.0.1:{port}/"
    finally:
        _ended(stop, thread, join)
    assert frame._login_object is None  # closed with the page's end


def test_without_the_integrators_login_its_page_opens_as_before(tmp_path, monkeypatch, said, join):
    monkeypatch.setenv("LIMINAL_INTEGRATOR_LOGIN", "none")
    frame = Integratorish(log=lambda m: None)
    port, link, stop, thread = _serve(tmp_path, frame, said)
    try:
        assert b'<body data-mode="fetcher">' in _call(port, "GET", "/")[1]
        assert json.loads(_call(port, "GET", "/api/trays")[1]) == {"trays": []}
        assert _call(port, "GET", "/login/api/status")[0] == 404 and frame.login_object() is None
    finally:
        _ended(stop, thread, join)
    # the chart app's own page held by the integrator's server (a frame not the integrator's) holds no login here
    from liminal_charts.frame import ChartsApp

    monkeypatch.delenv("LIMINAL_CHARTS_LOGIN", raising=False)
    monkeypatch.setattr(ChartsApp, "LOGIN_CLASS", StandInLogin)
    charts = ChartsApp(log=lambda m: None)
    charts.attach_plugins = lambda only=None: []
    port, link, stop, thread = _serve(tmp_path, charts, said)
    try:
        assert b'<body data-mode="fetcher">' in _call(port, "GET", "/")[1] and charts._login_object is None
    finally:
        _ended(stop, thread, join)
