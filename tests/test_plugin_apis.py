"""The Plugins view shows what each plugin attaches to (liminal-libs 1.49.0, plugin API 2.2; the user, 2026-11-11:
"each plugin should reflect the the API its attached to, and present links that provide documentation ... it should also
expose more of what the API is about"): its APIs and its parts', each once, after what its page already shows."""
from __future__ import annotations

from pathlib import Path

from liminal_charts.gui import plugins as view

STATIC = Path(view.__file__).parent / "static"


def entry(name: str, apis: list[dict], facts: dict) -> dict:
    return {"name": name, "title": name.title(), "label": name, "about": "", "state": "ready", "serves": [], "archive": False,
            "assets": [], "capabilities": [], "capabilitiesWithKey": False, "streams": [], "notes": [], "options": [],
            "delivery": [], "watched": 0, "load": 0.0, "usage": {"today": 0, "perDay": 0, "perRun": None},
            "scan": {"has": False, "at": None, "results": [], "running": False}, "apis": apis, "apiFacts": facts}


def test_a_plugins_feeds_apis_are_folded_into_its_page_each_once():
    rest = {"name": "Vendor REST API", "maker": "Vendor"}
    fx = {"name": "Vendor forex API", "maker": "Vendor"}
    out = view._fold(entry("vendor", [rest], {"Vendor REST API": {"Requests": "3"}}),
                     [entry("vendorfx", [rest, fx], {"Vendor forex API": {"Requests": "1"}})])
    assert [a["name"] for a in out["apis"]] == ["Vendor REST API", "Vendor forex API"]
    assert out["apiFacts"] == {"Vendor REST API": {"Requests": "3"}, "Vendor forex API": {"Requests": "1"}}


def test_the_page_draws_the_cards_after_what_it_already_shows():
    js = (STATIC / "plugins.js").read_text(encoding="utf-8")
    assert "function apisHtml(p, parts = [])" in js and "window.LiminalApis.html([...seen.values()], facts" in js
    assert "${databasesHtml(p)}${apisHtml(p, pubPartsOf(p))}`" in js  # a feed's page: its headlines part's too
    assert "${aiTryHtml(p)}${databasesHtml(p)}${apisHtml(p)}`" in js  # an AI assistant's page
    assert "return pubBodyHtml(p) + apisHtml(p, pubPartsOf(p));" in js  # a publisher's: its agencies' too
    assert js.count("From ${signupHtml(p.signup)}") == 2 and "${esc(p.signup)}" not in js  # where a key is made: a link
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert page.index('src="icons.js"') < page.index('src="apis.js"') < page.index('src="plugins.js"')
