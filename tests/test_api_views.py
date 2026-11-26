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

from liminal_libs.apps import App, EnvironmentLogin
from liminal_libs.storage import Store

from liminal_charts.gui import plugins as plugins_view, web
from liminal_charts.gui.front import Front, make_front_handler
from liminal_charts.gui.server import ChartApp, make_handler

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


class Integrator(App):
    """The integrator as the data API's frame (liminal-integrator's app.py), attaching nothing."""

    NAME = "integrator"

    def __init__(self):
        super().__init__(log=lambda m: None, login=EnvironmentLogin())


class StandIn:
    """A plugin object as far as its Internal API reads one: never called (`asked` stays empty)."""

    KIND = "feed"

    def __init__(self, name, title, kind="feed"):
        self.name, self.title, self.KIND, self.asked, self.apis = name, title, kind, [], ()

    def status(self):  # a hook it answers: listed, never called here
        self.asked.append("status")
        return {}


def _stand_ins(monkeypatch):
    """A feed plugin of two feeds and a publisher (acme: acme, acme-night; acme-news), a publisher of two parts (gov:
    gov, gov-sec) and an assistant, in the view's registry lookups."""
    feeds = {"acme": StandIn("acme", "Acme"), "acme-night": StandIn("acme-night", "Night")}
    pubs = {"acme-news": StandIn("acme-news", "Acme headlines", "publisher"), "gov": StandIn("gov", "Gov", "publisher"),
            "gov-sec": StandIn("gov-sec", "The SEC", "publisher")}
    helpers = {"helper": StandIn("helper", "Helper", "assistant")}
    parts = {"acme": ["acme-news"], "gov": ["gov-sec"]}
    monkeypatch.setattr(plugins_view, "providers", lambda: feeds)
    monkeypatch.setattr(plugins_view, "publishers", lambda: pubs)
    monkeypatch.setattr(plugins_view, "assistants", lambda: helpers)
    monkeypatch.setattr(plugins_view, "auditors", lambda: {})
    monkeypatch.setattr(plugins_view, "plugin_of", lambda n: "acme" if n == "acme-night" else n)
    monkeypatch.setattr(plugins_view, "plugin_feeds", lambda n: ["acme", "acme-night"] if n == "acme" else [n])
    monkeypatch.setattr(plugins_view, "publisher_parts", lambda n: parts.get(n, []))
    monkeypatch.setattr(plugins_view, "label", lambda n: "acme (night)" if n == "acme-night" else n)
    return [*feeds.values(), *pubs.values(), *helpers.values()]


def test_one_entrys_internal_api_is_every_plugin_object_behind_it_none_of_them_called(monkeypatch):
    objects = _stand_ins(monkeypatch)
    frame = Integrator()
    got = plugins_view.internal_view("acme", frame)
    assert got["parts"] == ["acme", "acme-night", "acme-news"] and isinstance(got["internal"], list)
    assert [x["label"] for x in got["internal"]] == ["acme", "acme (night)", "acme-news"]
    assert [x["name"] for x in got["internal"]] == ["acme", "acme-night", "acme-news"]
    assert "label" not in frame.plugin_internal(objects[1])  # the frame's own dict is never added to
    assert plugins_view.internal_view("gov", frame, "publisher")["parts"] == ["gov", "gov-sec"]
    one = plugins_view.internal_view("helper", frame)  # an assistant, not attached to this frame
    assert one["app"] == frame.TITLE
    assert one["parts"] == ["helper"] and one["internal"]["name"] == "helper" and one["apis"] == []
    assert plugins_view.internal_view("acme-night", frame) is None  # a feed part of another plugin: no entry
    assert plugins_view.internal_view("gov", frame) is None  # a publisher, asked without of=publisher
    assert plugins_view.internal_view("nobody", frame) is None
    assert not any(o.asked for o in objects)


def test_the_data_api_answers_an_entrys_two_views_and_leaves_an_unknown_name_to_its_frame(tmp_path, monkeypatch):
    _stand_ins(monkeypatch)
    store = Store(tmp_path / "liminal.db", check_same_thread=False)
    try:
        app = ChartApp(store, frame=Integrator())
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, [0]))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        try:
            status, got = _call(port, "/api/app/apis?plugin=acme")
            assert status == 200 and got["parts"] == ["acme", "acme-night", "acme-news"]
            status, got = _call(port, "/api/app/apis?plugin=gov&of=publisher")
            assert status == 200 and got["of"] == "publisher" and len(got["internal"]) == 2
            status, got = _call(port, "/api/app/apis?plugin=nobody")  # the frame's: no plugin of that name attached
            assert status == 404 and "nobody" in got["error"]
            status, got = _call(port, "/api/app/apis")  # without ?plugin=: its frame's own two views
            assert status == 200 and got["app"]["app"] == "integrator"
        finally:
            httpd.shutdown()
            httpd.server_close()
    finally:
        store.close()


def test_the_page_draws_this_app_first_and_three_tabs_beside_each_plugins_title():
    page = (web.STATIC / "index.html").read_text(encoding="utf-8")
    at = [page.index(f'<script src="{s}"></script>') for s in ("icons.js", "apis.js", "internal.js", "plugins.js")]
    assert at == sorted(at)
    assert not (web.STATIC / "internal.js").exists()  # the library's file, never shadowed by one of the page's own
    js = (web.STATIC / "plugins.js").read_text(encoding="utf-8")
    assert "const I = window.LiminalInternal || null;" in js and "const OWN = 'app:';" in js  # without it, as before
    assert '<h4 class="ex-group">This app</h4>' in js and 'data-plugin="${OWN}"' in js and "return mine + groups" in js
    assert "I.tabs([{ id: 'main', title: 'Main' }, { id: 'external', title: 'External API' }," in js
    assert "I.tabs([{ id: 'external', title: 'External API' }, { id: 'internal', title: 'Internal API' }]" in js
    assert "if (I && !folding) return '';" in js and "fold: folding" in js  # the cards: the External API tab's
    # asked only while shown: its own views, a plugin's by its name (a publisher's told apart)
    assert "await getJson('/api/app/apis')" in js and "?plugin=${encodeURIComponent(name)}${pub ? '&of=publisher' : ''}" in js
    assert "if (sel === OWN) loadOwn();" in js and "else if (tabShown(sel) === 'internal') loadInner(sel);" in js
    assert "own.at && Date.now() - own.at < AGAIN_MS" in js and "was.at && Date.now() - was.at < AGAIN_MS" in js
    # a server from before, in words: a front that passed the question on, an integrator with no route of its own, a
    # data API without ?plugin= (404)
    assert "was started before it could show its own APIs: restart it to see them." in js
    assert "(d.app?.app || d.internal?.name) !== (FETCHER ? 'integrator' : 'charts')" in js
    assert "FETCHER && !(d.internal?.counts?.own > 0) && !(d.apis || []).length" in js
    assert "was started before it could show this plugin's Internal API: restart ${server()} to see it." in js
    assert "if (!(sel === OWN && I) && (!sel" in js  # This app stays picked through the view's refreshes


def test_the_charts_cards_are_sound_and_their_links_people_only():
    from liminal_libs import apis as cards

    from liminal_charts import apis
    from liminal_charts.frame import ChartsApp

    assert cards.problems(apis.APIS) == []
    assert ChartsApp(log=lambda m: None).apis is apis.APIS
    assert [a.name for a in apis.APIS] == ["git", "SQLite, through Python's sqlite3", "Python's signal module"]
    assert all(a.verified in ("2026-11-16", "2026-11-17", "2026-11-18") for a in apis.APIS)  # the landed cards' days


def _named_on(fn_names, source: str, var: str) -> tuple[set, set]:
    """What the functions named call and read on `var` (a plugin object): `var.x(...)` a call, `var.x` an attribute,
    facts_of(var) and getattr(var, "x") as the library's helpers read them."""
    import ast

    calls, attrs = set(), set()
    for fn in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name in fn_names):
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name) and node.func.value.id == var:
                    calls.add(node.func.attr)
                elif node.func.attr == "facts_of":
                    calls.add("api_facts")
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "getattr" and len(node.args) >= 2 \
                    and getattr(node.args[0], "id", "") == var and isinstance(node.args[1], ast.Constant):
                attrs.add(node.args[1].value)
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == var:
                attrs.add(node.attr)
    return calls, attrs - calls


def test_what_the_charts_say_they_call_and_read_on_a_plugin_is_what_the_plugins_view_does():
    import inspect

    from liminal_charts.frame import ChartsApp
    from liminal_charts.gui import server

    source = inspect.getsource(plugins_view)
    kinds = {"feed": ("view", "_plan", "_options", "_scanning"),
             "assistant": ("_assistant_entry", "_scanning", "_options", "_ai_limits"),
             "auditor": ("_auditor_entry", "_options")}
    for kind, names in kinds.items():
        calls, attrs = _named_on(names, source, "p")
        assert set(ChartsApp.CALLS[kind]) == calls, kind
        assert set(ChartsApp.ATTRIBUTES[kind]) == attrs, kind
    calls, attrs = _named_on(("publishers",), inspect.getsource(server.ChartApp), "obj")
    assert set(ChartsApp.CALLS["publisher"]) == calls and set(ChartsApp.ATTRIBUTES["publisher"]) == attrs | {"name"}
    inner = ChartsApp(log=lambda m: None).internal()
    assert set(inner["calls"]) == set(ChartsApp.CALLS) and set(inner["attributes"]) == set(ChartsApp.ATTRIBUTES)
