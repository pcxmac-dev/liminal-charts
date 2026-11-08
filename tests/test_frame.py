"""The charts app as an app of the frame (frame.py): its login, the plugins it attaches, /api/interface, and the
routes the data API leaves to its frame (the integrator's databases)."""
from __future__ import annotations

import json
import threading
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

    def api(self, handler, method, path, query, body):
        self.asked.append((method, path, query))
        if path == "/api/databases":
            return 200, {"entries": [{"id": "liminal.db"}]}
        if path == "/api/databases/detail":
            raise Refused(HTTPStatus.NOT_FOUND, f"no database {query.get('id')!r}")
        return super().api(handler, method, path, query, body)


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
