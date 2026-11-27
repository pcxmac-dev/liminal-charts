from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from liminal_charts.gui.server import ChartApp, make_handler
from liminal_libs.storage import LayoutLimit, Store


def test_store_layout_lifecycle(store):
    a = store.create_layout("Morning", {"symbol": "SPCX", "drawings": {"SPCX": [{"name": "segment"}]}})
    assert a["name"] == "Morning" and a["data"]["drawings"]["SPCX"][0]["name"] == "segment"
    b = store.update_layout(a["id"], name="Morning v2", data={"symbol": "NQ1!"})
    assert b["name"] == "Morning v2" and b["data"] == {"symbol": "NQ1!"}
    assert store.list_layouts()[0]["id"] == a["id"]
    assert store.delete_layout(a["id"]) and store.get_layout(a["id"]) is None
    assert store.update_layout(999, name="x") is None


def test_at_most_five_layouts(store):
    for i in range(5):
        store.create_layout(f"L{i}", {})
    with pytest.raises(LayoutLimit):
        store.create_layout("one too many", {})
    store.delete_layout(store.list_layouts()[0]["id"])
    store.create_layout("fits again", {})


@pytest.mark.parametrize("name", ["", "   ", "x" * 61])
def test_layout_names_are_validated(store, name):
    with pytest.raises(ValueError):
        store.create_layout(name, {})


def test_most_recently_saved_first(store):
    a = store.create_layout("A", {})
    store.create_layout("B", {})
    store.update_layout(a["id"], data={"touched": True})
    assert [l["name"] for l in store.list_layouts()] == ["A", "B"]


@pytest.fixture
def server(tmp_path):
    store = Store(tmp_path / "m.db", check_same_thread=False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ChartApp(store), [0]))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    store.close()


def _req(port, method, path, body=None, header=True, host="127.0.0.1"):
    c = HTTPConnection("127.0.0.1", port)
    headers = {"Host": f"{host}:{port}", "Content-Type": "application/json"}
    if header:
        headers["X-Liminal-Charts"] = "1"
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    r = c.getresponse()
    raw = r.read()
    return r.status, (json.loads(raw) if raw and r.getheader("Content-Type", "").startswith("application/json") else raw)


def test_layout_api(server):
    status, made = _req(server, "POST", "/api/layouts", {"name": "Futures", "data": {"symbol": "NQ1!"}})
    assert status == 201 and made["name"] == "Futures"
    status, got = _req(server, "GET", f"/api/layouts/{made['id']}", header=False)
    assert status == 200 and got["data"] == {"symbol": "NQ1!"}
    status, renamed = _req(server, "PUT", f"/api/layouts/{made['id']}", {"name": "Futures desk"})
    assert status == 200 and renamed["name"] == "Futures desk" and renamed["data"] == {"symbol": "NQ1!"}
    status, listing = _req(server, "GET", "/api/layouts", header=False)
    assert status == 200 and listing["max"] == 5 and len(listing["layouts"]) == 1
    for i in range(4):
        _req(server, "POST", "/api/layouts", {"name": f"L{i}", "data": {}})
    assert _req(server, "POST", "/api/layouts", {"name": "sixth", "data": {}})[0] == 409
    assert _req(server, "DELETE", f"/api/layouts/{made['id']}")[0] == 200
    assert _req(server, "GET", f"/api/layouts/{made['id']}", header=False)[0] == 404


def test_a_pages_older_save_is_not_written_over_its_newer(server):
    """Item 14's review (A4): the save leaving a page goes at once, beside one still on its way, so the two may land in
    either order. Each names its page and carries its generation (the changes the page counted): the server writes none
    older than a save of the same page it has written, and answers it with the layout as it is (`older`); another
    page's saves and a save naming no page are written as they come."""
    lid = _req(server, "POST", "/api/layouts", {"name": "Desk", "data": {"drawings": {}}})[1]["id"]

    def put(body):
        status, got = _req(server, "PUT", f"/api/layouts/{lid}", body)
        assert status == 200
        return got

    newer = put({"data": {"v": "newest"}, "page": "tab-a", "generation": 7})  # the one leaving the page, landed first
    assert newer["data"] == {"v": "newest"} and "older" not in newer
    late = put({"data": {"v": "older"}, "page": "tab-a", "generation": 5})  # the one that was on its way, after it
    assert late["older"] is True and late["data"] == {"v": "newest"} and late["name"] == "Desk"
    assert _req(server, "GET", f"/api/layouts/{lid}", header=False)[1]["data"] == {"v": "newest"}
    assert put({"data": {"v": "again"}, "page": "tab-a", "generation": 7})["data"] == {"v": "again"}  # sent twice
    assert put({"data": {"v": "tab b"}, "page": "tab-b", "generation": 1})["data"] == {"v": "tab b"}
    assert put({"data": {"v": "a client"}})["data"] == {"v": "a client"}
    assert put({"data": {"v": "older still"}, "page": "tab-a", "generation": 6})["older"] is True
    assert put({"name": "Desk 2", "page": "tab-a", "generation": 1})["name"] == "Desk 2"  # a rename: never held back
    # the page sends the save leaving it beside another only to a server that says it writes them so
    assert _req(server, "GET", "/api/layouts", header=False)[1]["ordered"] is True


def test_the_pages_generations_kept_are_bounded(store):
    app = ChartApp(store)
    lid = store.create_layout("Desk", {})["id"]
    for i in range(app.LAYOUT_SAVES_KEPT + 40):
        app.update_layout(lid, {"data": {"i": i}, "page": f"page-{i}", "generation": 3})
    assert len(app._layout_saves) == app.LAYOUT_SAVES_KEPT
    assert (1, lid, "page-0") not in app._layout_saves and (1, lid, f"page-{app.LAYOUT_SAVES_KEPT + 39}") in app._layout_saves


def test_layout_writes_need_header_and_local_host(server):
    assert _req(server, "POST", "/api/layouts", {"name": "x", "data": {}}, header=False)[0] == 403
    assert _req(server, "POST", "/api/layouts", {"name": "x", "data": {}}, host="evil.example")[0] == 403
    assert _req(server, "POST", "/api/layouts", {"name": "", "data": {}})[0] == 400
    assert _req(server, "POST", "/api/layouts", {"name": "x", "data": []})[0] == 400
    assert _req(server, "PUT", "/api/layouts/999", {"name": "x"})[0] == 404
    assert _req(server, "POST", "/api/other", {})[0] == 404
