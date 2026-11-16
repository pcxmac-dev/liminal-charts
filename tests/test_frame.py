"""The charts app as an app of the frame (frame.py): its login, the plugins it attaches, /api/interface, and the
routes the data API leaves to its frame (the integrator's databases)."""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from http import HTTPStatus
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from liminal_libs.apps import App, EnvironmentLogin, Refused
from liminal_libs.sources.registry import assistants, providers, publishers
from liminal_libs.storage import Store

from liminal_charts.frame import ChartsApp
from liminal_charts.gui.server import ChartApp, make_handler


def _all():
    return [*providers().items(), *publishers().items(), *assistants().items()]


def test_the_charts_app_is_an_app_of_the_frame_holding_its_plugins_and_login(tmp_path):
    frame = ChartsApp(log=lambda m: None)
    assert isinstance(frame, App) and isinstance(frame.login, EnvironmentLogin) and (frame.NAME, frame.ICON, frame.PORT) == ("charts", "disk", 8765)
    tray = frame.tray().as_dict()
    assert tray["items"][0] == {"id": "charts", "icon": "disk", "title": "Liminal Charts", "kind": "app", "app": "charts"}
    store = Store(tmp_path / "liminal.db", check_same_thread=False)
    try:
        app = ChartApp(store, frame=frame)
        assert app.frame is frame and app.login is frame.login and app.frame.plugins() == {}  # the server attaches at start
        assert isinstance(ChartApp(store).frame, ChartsApp)  # alone: its own
        free = {n for n, p in _all() if p.app is None or p.app is frame}
        names = frame.attach_plugins()
        assert set(names) == free and all(frame.plugin(n).login is frame.login and frame.plugin(n).app is frame for n in names)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, [0]))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            c = HTTPConnection("127.0.0.1", port)
            c.request("GET", "/api/interface", headers={"Host": f"127.0.0.1:{port}"})
            r = c.getresponse()
            data = json.loads(r.read())
            assert r.status == 200 and (data["name"], data["kind"]) == ("charts", "app") and data["login"]["namespace"] == "environment"
            assert data["plugins"] == sorted(names) and set(data["attached"]) == set(names)
            assert all({"name", "kind", "system", "hooks"} <= set(v) for v in data["attached"].values())
        finally:
            httpd.shutdown()
        for n in names:
            frame.detach(n)
        assert frame.plugins() == {} and all(p.app is None for n, p in _all() if n in names)
    finally:
        store.close()


class DatabasesFrame(App):
    """An app of the frame with a route of its own, as the integrator's (liminal-integrator's app.py: the databases)."""

    NAME = "integrator"

    def __init__(self):
        super().__init__(log=lambda m: None, login=EnvironmentLogin())
        self.asked = []
        self.forms = []

    def api(self, handler, method, path, query, body):
        self.asked.append((method, path, query))
        if path == "/api/databases":
            return 200, {"entries": [{"id": "liminal.db"}]}
        if path == "/api/databases/detail":
            raise Refused(HTTPStatus.NOT_FOUND, f"no database {query.get('id')!r}")
        if method == "POST" and path == "/api/databases/instances":  # one of its page's forms (liminal-integrator 1.12.0)
            self.forms.append(body)
            if body.get("where") == "far" and not body.get("accept_remote"):
                raise Asked(HTTPStatus.BAD_REQUEST, "far is not this machine", "accept_remote")
            if body.get("name") == "nope":
                raise Refused(HTTPStatus.NOT_FOUND, "no instance 'nope'")
            return 200, {"ok": True, "said": f"{body.get('action')} {body.get('name')}"}
        return super().api(handler, method, path, query, body)


class Asked(Refused):
    """A refusal that names what accepts it, as the integrator's (its instances.Asked)."""

    def __init__(self, status, reason, accept):
        super().__init__(status, reason)
        self.accept = accept

    def answer(self):
        return {"error": self.reason, "accept": self.accept}


def test_the_data_api_leaves_the_routes_it_does_not_answer_to_its_frame(tmp_path):
    frame = DatabasesFrame()
    store = Store(tmp_path / "liminal.db", check_same_thread=False)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ChartApp(store, frame=frame), [0]))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]

            def get(path):
                c = HTTPConnection("127.0.0.1", port)
                c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
                r = c.getresponse()
                return r.status, json.loads(r.read())
            assert get("/api/databases") == (200, {"entries": [{"id": "liminal.db"}]})
            assert get("/api/databases/detail?id=nope.db") == (404, {"error": "no database 'nope.db'"})  # its refusal, said
            assert get("/api/app")[1]["app"] == "integrator"  # the frame's own routes
            assert get("/api/nothing") == (404, {"error": "not found"})
            assert get("/api/ui")[0] == 200 and all(p != "/api/ui" for _, p, _ in frame.asked)  # its own routes first
            assert frame.asked[1] == ("GET", "/api/databases/detail", {"id": "nope.db"})
        finally:
            httpd.shutdown()
    finally:
        store.close()


def test_the_data_api_leaves_its_frames_own_writes_to_it_with_the_frames_own_refusals(tmp_path, monkeypatch):
    """The integrator's database plugins' instances (the user, 2026-11-15: "in each plugin can setup either new
    databases or connections to existing databases"): the page's forms are the frame's to answer; a write is still the
    app's own page's alone (its header), and a copy that reads only changes nothing."""
    frame = DatabasesFrame()
    store = Store(tmp_path / "liminal.db", check_same_thread=False)
    try:
        app = ChartApp(store, frame=frame)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, [0]))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]

            def post(form, path="/api/databases/instances", header=True):
                c = HTTPConnection("127.0.0.1", port)
                headers = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
                if header:
                    headers["X-Liminal-Charts"] = "1"
                c.request("POST", path, body=json.dumps(form), headers=headers)
                r = c.getresponse()
                raw = r.read()
                return r.status, (json.loads(raw) if raw[:1] == b"{" else raw.decode())
            form = {"plugin": "sqlite", "action": "new", "name": "archive", "confirm": True}
            assert post(form) == (200, {"ok": True, "said": "new archive"}) and frame.forms == [form]
            # the frame's own refusal: what accepts it goes back with it, for the page's second question
            assert post({"action": "connect", "name": "x", "where": "far"}) == (
                400, {"error": "far is not this machine", "accept": "accept_remote"})
            assert post({"action": "connect", "name": "x", "where": "far", "accept_remote": True})[0] == 200
            assert post({"action": "test", "name": "nope"}) == (404, {"error": "no instance 'nope'"})
            assert post({}, path="/api/databases/nothing") == (404, {"error": "not found"})  # the frame's own word for it
            # a write is the app's own page's alone, here as everywhere
            assert post(form, header=False) == (403, "missing request header") and len(frame.forms) == 4
            # a copy that reads only changes nothing: the frame is not asked
            monkeypatch.setattr(app, "_settings", replace(app.settings, read_only=True))
            status, said = post(form)
            assert status == 409 and "reads only" in said["error"] and len(frame.forms) == 4
        finally:
            httpd.shutdown()
    finally:
        store.close()
