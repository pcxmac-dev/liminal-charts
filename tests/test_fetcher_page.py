"""The integrator's page and the data API, served in the integrator's own process (gui/server.py
serve_integrator; the user, 2026-11-03: "for the webserver / ports, i just integrator, and charts"):
the chart app's page without the chart, the tools and what is the user's (the user, 2026-11-01), with
the request stack; the fetcher is reached over a link (liminal-integrator's FetcherLink stands in)."""
from __future__ import annotations

import http.client
import json
import threading
import time
from dataclasses import replace

from liminal_libs.config import load_settings
from liminal_charts.gui import server


class FakeLink:
    """What serve_integrator needs of the integrator's link to the fetcher: a touch, its status, a
    wake, a pass, the quiet watch, letting go."""

    def __init__(self):
        self.touched, self.woken, self.passes, self.let, self.quiet_args = 0, 0, 0, 0, None

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
        self.quiet_args = (stop, on_quiet)
        stop.wait()


def test_serve_integrator_serves_the_integrators_page_and_the_data_api_in_its_process(tmp_path):
    settings = replace(load_settings(env={}, env_file=None), db_path=tmp_path / "liminal.db")
    link, lines, stop, served = FakeLink(), [], threading.Event(), []
    threading.Thread(target=lambda: served.append(server.serve_integrator(settings, 0, link=link, log=lines.append, stop=stop,
                                                                        until_quiet=True)), daemon=True).start()
    end = time.monotonic() + 5
    while not (urls := [m for m in lines if m.startswith("integrator: its page and the data API at")]) and time.monotonic() < end:
        time.sleep(0.02)
    port = int(urls[0].split("http://127.0.0.1:", 1)[1].split("/", 1)[0])
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/")
        page = conn.getresponse().read()
        assert b'<body data-mode="fetcher">' in page and b"<title>Liminal Fetcher</title>" in page
        assert b'id="viewFetcher"' in page and b'id="rqForm"' in page
        conn.request("GET", "/api/requests")
        out = json.loads(conn.getresponse().read())
        assert out["rows"] == [] and out["status"]["pid"] == 4242 and out["status"]["clients"] == [4242]
        assert out["kinds"] == ["bars", "live"]
        conn.request("POST", "/api/requests", body=json.dumps({"symbol": "aapl", "kinds": ["bars", "live", "nope"]}),
                     headers={"Content-Type": "application/json", "X-Liminal-Charts": "1"})
        r = conn.getresponse()
        row = json.loads(r.read())
        assert r.status == 202 and row["symbol"] == "AAPL" and row["kinds"] == ["bars", "live"] and row["by"] == "page"
        assert link.woken == 1  # the fetcher woken over the link: it takes the request at once
        conn.request("DELETE", f"/api/requests/{row['id']}", headers={"X-Liminal-Charts": "1"})
        assert json.loads(conn.getresponse().read())["cancelled"] is True and link.woken == 2
        conn.request("POST", "/api/fetching", body=json.dumps({"paused": False}),
                     headers={"Content-Type": "application/json", "X-Liminal-Charts": "1"})
        assert json.loads(conn.getresponse().read())["started"] is True and link.passes == 1  # resuming asks for a pass
        assert link.touched >= 5  # every request touched the link: a page in touch keeps the fetcher attached
        assert link.quiet_args[0] is stop and link.quiet_args[1] == stop.set  # until quiet: the server ends then
    finally:
        stop.set()
        end = time.monotonic() + 5
        while not served and time.monotonic() < end:
            time.sleep(0.02)
    assert served == [f"http://127.0.0.1:{port}/"] and link.let == 1  # the fetcher let go with the server


def test_the_fetchers_page_is_the_chart_page_without_the_chart_and_what_is_the_users():
    page = server.fetcher_page().decode("utf-8")
    assert page.count('data-mode="fetcher"') == 1 and "<title>Liminal Fetcher</title>" in page
    css = (server.STATIC / "style.css").read_text(encoding="utf-8")
    hidden = css.split('body[data-mode="fetcher"] #topbar', 1)[1].split("{ display: none !important; }", 1)[0]
    for part in ("#tools", "#sidePanel", "#viewChart", "#symbolDialog", "#railProfile", 'button[data-pane="stock"]',
                 'button[data-pane="analysis"]', 'button[data-pane="trading"]', 'button[data-pane="watchlist"]',
                 'button[data-pane="prefs"]', "hr:first-of-type", "hr:last-of-type"):
        assert part in hidden, part
    for kept in ('data-pane="exchanges"', 'data-pane="markets"', 'data-pane="news"', 'data-pane="gov"',
                 'data-pane="datasets"', 'data-pane="fetcher"', 'data-pane="live"', 'data-pane="plugins"',
                 'data-pane="databases"', 'data-pane="resources"'):
        assert kept not in hidden and kept in page, kept
    app_js = (server.STATIC / "app.js").read_text(encoding="utf-8")
    assert "const FETCHER_MODE = document.body.dataset.mode === 'fetcher';" in app_js
    start = app_js.split("(async function start() {", 1)[1]
    assert start.lstrip().startswith("if (FETCHER_MODE) {")  # no chart, no layout, no symbol on the fetcher's page
    panels = (server.STATIC / "panels.js").read_text(encoding="utf-8")
    assert "window.FETCHER_MODE && VIEWS[name] === 'chart'" in panels
