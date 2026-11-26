"""The charts' page held to the library's page standard (0.2.11; Monday list item 31; liminal-libs docs/PAGES.md and
ui/static/standard.json): the rules of which tools can be picked at the same time name the charts' own drawing tools
and views, as app.js and panels.js have them, and the right-hand menu's top names the drawing tool picked and what it
reports. Text assertions on the page's files; the scripts themselves are run in headless Edge on stand-ins."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "src" / "liminal_charts" / "gui" / "static"
APP = (STATIC / "app.js").read_text(encoding="utf-8")
PANELS = (STATIC / "panels.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")

standard = pytest.importorskip("liminal_libs.ui.standard", reason="a library before 1.105.0 has no page standard")


def _groups(app: str) -> dict:
    return {g["id"]: g for g in standard.groups(app)}


def _views() -> dict:
    body = PANELS.split("const VIEWS = {", 1)[1].split("};", 1)[0]
    return dict(re.findall(r"(\w+): '(\w+)'", body))


def test_the_rules_name_the_charts_drawing_tools_as_app_js_has_them():
    body = APP.split("const TOOLS = [", 1)[1].split("\n];", 1)[0]
    tools = re.findall(r"\['(\w+)', '", body)
    g = _groups("charts")["drawing"]
    assert g["tools"] == tools and g["default"] == "cursor" == tools[0]


def test_the_rules_name_the_views_as_panels_js_has_them():
    views = _views()
    v = _groups("charts")["view"]
    assert set(v["tools"]) == set(views) and v["default"] == "stock"
    assert set(v["chart"]) == {k for k, kind in views.items() if kind == "chart"}  # a drawing tool only on these
    # the integrator's page is the charts' code without a chart: every view of it that is not a chart's
    assert set(_groups("integrator")["view"]["tools"]) == {k for k, kind in views.items() if kind != "chart"}


def test_the_overhead_toggles_are_the_pages_own():
    o = _groups("charts")["overhead"]
    assert set(o["tools"]) == {"replay", "unfiltered"} and o["only"] == {"replay": {"view": ["analysis"]}}
    assert 'id="replayBtn" class="toggle analysis-only"' in HTML and 'id="unfilteredBtn" class="toggle"' in HTML


def test_the_right_hand_menus_top_names_the_drawing_tool_and_the_cursor():
    assert HTML.index('src="icons.js"') < HTML.index('src="side.js"') < HTML.index('src="app.js"')
    assert '<section id="pTool" class="side-tool chart-tool"' in HTML
    assert HTML.index('id="pTool"') < HTML.index('id="paneStock"')  # on top of every chart view's own panel
    assert "window.activeDrawingTool = name;" in APP and "window.renderTool?.();" in APP
    assert "window.drawingTools = TOOLS.filter(Boolean).map(" in APP
    tool = PANELS.split("function renderTool(s) {", 1)[1].split("\n  }", 1)[0]
    assert "e(tool.title)" in tool and "['Time'," in tool and "['Price', priceText(b.close)]" in tool
    assert "if (html !== toolDrawn)" in tool  # drawn again only when it changed (the cursor moves at every frame)
    assert "renderTool(s);" in PANELS.split("function renderCursor() {", 1)[1].split("\n  }", 1)[0]


def test_codebase_is_mounted_with_the_panel():
    code = (STATIC / "codeview.js").read_text(encoding="utf-8")
    assert "window.LiminalSide ? window.LiminalSide.panel(sideEl, { app: window.FETCHER_MODE ? 'integrator' : 'charts' }) : null" in code
    assert "C.mount(dataEl, sideEl, { get, send, panel, scope: 'codebase' });" in code
