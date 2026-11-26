"""The charts' own External and Internal API, and a plugin's in the Plugins view (0.2.10; Monday list item 17; the user,
2026-11-16: "for all apps, and all plugins, there should be an external api tab, and an internal api tab"): GET
/api/app/apis answered on the charts page by the charts app the front holds, ?plugin=NAME passed on to the integrator,
whose data API answers it for every plugin object behind one entry of the view. The integrator stands in as a small
echoing server; no process starts, nothing is reached."""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

from liminal_charts.gui.front import Front, make_front_handler

from .test_front import upstream  # noqa: F401 - the integrator's data API as a small echoing server


def _call(port, path):
    c = HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
    r = c.getresponse()
    return r.status, json.loads(r.read())


def _serve_front(front):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_front_handler(front))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_the_charts_page_asks_the_charts_app_for_its_own_views_and_the_integrator_for_a_plugins(
        tmp_path, monkeypatch, upstream):  # noqa: F811
    monkeypatch.setenv("LIMINAL_FETCHER_PORT", str(upstream.server_address[1]))  # an integrator answering
    monkeypatch.setenv("LIMINAL_LOGIN_URL", "none")
    front = Front(tmp_path / "liminal.db", log=lambda m: None)
    srv, port = _serve_front(front)
    try:
        status, got = _call(port, "/api/app/apis")
        assert status == 200 and "path" not in got  # (the echo has a path): never passed on
        assert got["app"]["app"] == "charts" and got["internal"]["name"] == "charts"
        rows = {(r["method"], r["path"]): r for r in got["internal"]["routes"]}
        assert rows[("GET", "/api/app/apis")]["own"] and rows[("GET", "/api/{rest}")]["own"]
        assert rows[("GET", "/api/tray")]["about"].startswith("passed on to the integrator")
        assert [x["name"] for x in got["internal"]["linked"]] == ["integrator", "platform"]
        assert isinstance(got["apis"], list) and isinstance(got["apiFacts"], dict)
        status, empty = _call(port, "/api/app/apis?plugin=")  # an empty name: its own, as the frame reads it
        assert status == 200 and empty["app"]["app"] == "charts"
        # one plugin's: the integrator holds them, so it is passed on with its query as it came
        status, echoed = _call(port, "/api/app/apis?plugin=massive&of=publisher")
        assert status == 200 and echoed["path"] == "/api/app/apis?plugin=massive&of=publisher"
    finally:
        srv.shutdown()
        srv.server_close()
