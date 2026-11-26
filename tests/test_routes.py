"""The charts' own routes as their Internal API lists them (gui/routes.py, 0.2.10; Monday list item 17; liminal-libs
1.63.0's internal.py): the front's table and the data API's, each held here to the code that answers them. The chains
are read from their sources with ast: nothing is started, served or reached."""
from __future__ import annotations

import ast
import inspect
import textwrap

from liminal_libs.apps.app import APIS_PATH, PAIRS_PATH
from liminal_libs.internal import FRAME_ROUTES, ROUTE_KINDS, ROUTE_METHODS, ROUTE_WHO, literals_in, route_problems

from liminal_charts.frame import ChartsApp
from liminal_charts.gui import front, routes, server, web

CATCH_ALLS = {"/api/{rest}", "/v1/{rest}", "/platform/{rest}"}  # what the front passes on whole
ASKED_OF_THE_INTEGRATOR = {"/api/profiles"}  # the front's own ask of the integrator (Front.profiles), not a route of it


def _keys(rows) -> list[tuple[str, str]]:
    return [(r[0], r[1]) for r in rows]


def test_every_row_is_one_with_its_method_class_and_caller():
    for table in (routes.FRONT_ROUTES, routes.DATA_ROUTES, routes.PLATFORM_CORE, routes.NOT_ANSWERED,
                  routes.passed_on()):
        assert len(set(_keys(table))) == len(table)
        for method, path, about, kind, who in table:
            assert method in ROUTE_METHODS and kind in ROUTE_KINDS and who in ROUTE_WHO, (method, path)
            assert path.startswith("/") and about.strip(), (method, path)
            assert kind in (("read", "stream") if method == "GET" else ("change", "ask", "stream")), (method, path)


def test_the_data_apis_table_is_what_its_chain_answers():
    literals = literals_in(inspect.getsource(server.make_handler)) | {web.UI_PATH, APIS_PATH}
    assert route_problems((*routes.DATA_ROUTES, *routes.LEFT_TO_FRAME), literals) == []


def _get_paths() -> set[str]:
    """Every path the data API's _get compares url.path with (`url.path == "..."`, `== web.UI_PATH`), and those it
    takes a part after (`url.path.startswith("/api/layouts/")` as /api/layouts/{id})."""
    fn = next(n for n in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(server.make_handler))))
              if isinstance(n, ast.FunctionDef) and n.name == "_get")
    out = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute) and node.left.attr == "path" \
                and isinstance(node.ops[0], ast.Eq):
            right = node.comparators[0]
            if isinstance(right, ast.Constant):
                out.add(right.value)
            elif isinstance(right, ast.Attribute) and right.attr == "UI_PATH":
                out.add(web.UI_PATH)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "startswith" and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value not in ("/api/", "/v1/"):
            out.add(node.args[0].value + "{id}")
    return out


def test_every_get_of_the_data_api_is_a_get_row_and_no_other():
    rows = {p for m, p, *_ in routes.DATA_ROUTES if m == "GET"}
    assert rows == _get_paths() | {APIS_PATH}  # GET /api/app/apis: ?plugin= its own, else its frame's


def test_the_fronts_table_is_what_it_answers_itself():
    literals = literals_in(inspect.getsource(front), inspect.getsource(web)) - ASKED_OF_THE_INTEGRATOR
    own = [r for r in routes.FRONT_ROUTES if r[1] not in CATCH_ALLS]
    listed = set(_keys(own))
    frames = [r for r in FRAME_ROUTES if (r[0], r[1]) in routes.FRONT_OWN - listed]  # GET /api/codebase: the frame's
    assert route_problems((*own, *frames), literals | {PAIRS_PATH, APIS_PATH}) == []
    assert {r[1] for r in routes.FRONT_ROUTES} >= CATCH_ALLS


def test_every_frame_route_is_the_fronts_own_or_passed_on_once():
    passed = _keys(routes.passed_on())
    for method, path, *_ in FRAME_ROUTES:
        assert ((method, path) in routes.FRONT_OWN) != ((method, path) in passed), (method, path)
    assert len(set(passed)) == len(passed)
    words = {(m, p): about for m, p, about, *_ in routes.passed_on()}
    assert words[("GET", "/api/plugins")].endswith(dict(((r[1], r[2]) for r in routes.DATA_ROUTES if r[0] == "GET"))
                                                   ["/api/plugins"])  # the Plugins view: the data API's own words
    assert "does not answer it (404)" in words[("POST", "/api/app/stop")]


def test_the_frames_writes_the_data_api_does_not_answer_are_said_so():
    fn = next(n for n in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(server.make_handler))))
              if isinstance(n, ast.FunctionDef) and n.name == "_write")
    writes = ast.unparse(fn)
    assert not any(lit == "/api/app" or lit.startswith("/api/app/") for lit in literals_in(writes))
    assert "'app'" not in writes and "'signal'" not in writes  # no part of a path naming them in its writes
    assert _keys(routes.NOT_ANSWERED) == list(routes.NOT_ANSWERED_PATHS)
    for row in routes.NOT_ANSWERED:
        assert row[2].startswith(routes.UNANSWERED)


def test_the_charts_app_lists_the_fronts_routes_behind_the_front_and_the_data_apis_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("LIMINAL_CHARTS_LOGIN", "none")
    alone = ChartsApp(log=lambda m: None)
    assert alone.routes() == (*routes.DATA_ROUTES, *routes.PLATFORM_CORE, *routes.NOT_ANSWERED)
    held = front.Front(tmp_path / "liminal.db", log=lambda m: None).charts
    assert held.fronting and held.routes() == (*routes.FRONT_ROUTES, *routes.passed_on())
    inner = held.internal()
    mine = {(r["method"], r["path"]) for r in inner["routes"] if r["own"]}
    theirs = {(r["method"], r["path"]) for r in inner["routes"] if not r["own"]}
    assert theirs == {("GET", "/api/codebase"), ("POST", "/api/codebase/fetch")}  # the frame's, answered here
    assert ("GET", "/api/app") in mine and ("GET", "/api/{rest}") in mine
    assert [x["name"] for x in inner["linked"]] == ["integrator", "platform"]
    assert [x["variable"] for x in inner["linked"]] == ["LIMINAL_FETCHER_PORT", "LIMINAL_PLATFORM_URL"]
    assert inner["notes"] and all(len(n) <= 400 for n in ChartsApp.INTERNAL_NOTES)
    alone_rows = {(r["method"], r["path"]): r for r in alone.internal()["routes"]}
    assert alone_rows[("POST", "/api/app/stop")]["own"] and alone_rows[("GET", "/api/bars")]["own"]
