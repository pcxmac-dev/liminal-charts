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


def test_a_view_with_no_chart_puts_the_drawing_tool_back_to_the_cursor():
    """The user, 2026-11-26: the drawing tool goes back to the cursor when a view with no chart is shown, as the rules
    say (standard.json's {with: {view: chart}}), a drawing begun let go; between chart views it stays. Asked of the
    rules through side.js's twin (LiminalSide.standard.after) at every view shown (panels.js's liminal:view); the
    script itself is run in headless Edge on stand-ins."""
    assert standard.after("charts", {"drawing": ["segment"]}, "view", "markets")["drawing"] == ["cursor"]
    assert standard.after("charts", {"drawing": ["segment"]}, "view", "analysis")["drawing"] == ["segment"]
    body = APP.split("function toolForView(pane, view) {", 1)[1].split("\n}", 1)[0]
    assert "rules.after('charts', { drawing: [tool] }, 'view', pane).drawing[0] || 'cursor'" in body
    assert "return view === 'chart' ? tool : 'cursor';" in body  # no rules, or a view they do not name
    listen = APP.split("window.addEventListener('liminal:view', (e) => {", 1)[1].split("\n});", 1)[0]
    assert "toolForView(e.detail?.pane, e.detail?.view)" in listen and "startDrawing(tool)" in listen
    assert "window.dispatchEvent(new CustomEvent('liminal:view', { detail: { pane: name, view } }));" in PANELS


def test_an_exchanges_product_is_told_under_its_venue_in_words_its_buttons_with_it():
    """The user, 2026-11-26: on Exchanges a product picked is told under its venue as a pick of words alone (side.js's
    spec, every word escaped there), its buttons (back to the venue, Open chart) moved into that part, never into the
    panel's top, which keeps the venue; without side.js the product's insight takes the side as before. The scripts
    themselves are run in headless Edge on stand-ins (viewside-check)."""
    code = (STATIC / "venues.js").read_text(encoding="utf-8")
    side = code.split("function sideHtml() {", 1)[1].split("\n  }", 1)[0]
    assert "if (picked && !window.ViewSide?.on) return productInsight();" in side  # the top: the venue
    spec = code.split("function productSpec() {", 1)[1].split("\n  }", 1)[0]
    assert "<" not in spec.replace("=>", "").replace("? ", "")  # words alone: no markup of its own
    assert "return { key, title: sym.ticker, sub:" in spec and "facts.push(['Data'," in spec
    buttons = code.split("const productButtons = () => `", 1)[1].split("`;", 1)[0]
    assert 'data-back="1"' in buttons and 'data-open="${esc(picked)}"' in buttons
    assert "spec: { ...productSpec(), from: productRow() }, buttons: productButtons(), letGo: letProductGo" in code
    vs = (STATIC / "viewside.js").read_text(encoding="utf-8")
    tell = vs.split("function tellOwn(panel, aside, own) {", 1)[1].split("\n  }\n", 1)[0]
    assert "panel.pick(spec);" in tell and "bar.className = 'side-pick-actions'; slot.append(bar);" in tell
    assert "if (was != null && panel.picked && panel.picked.key === was) panel.unpick();" in tell
    assert "aside.addEventListener('liminal:pick'," in tell and "if (letGo) letGo();" in tell
    assert "tellOwn(panel, aside, own || null);" in vs
    assert "if (own && own.spec && String(own.spec.key) === key) return own.spec;" in vs  # told by the view, not gone


def test_codebase_is_mounted_with_the_panel():
    code = (STATIC / "codeview.js").read_text(encoding="utf-8")
    assert "window.LiminalSide ? window.LiminalSide.panel(sideEl, { app: window.FETCHER_MODE ? 'integrator' : 'charts' }) : null" in code
    assert "C.mount(dataEl, sideEl, { get, send, panel, scope: 'codebase' });" in code


def test_the_three_column_views_tell_a_row_of_their_middle_under_their_side():
    """Plugins, Databases and Exchanges (the integrator's page too): the side the panel's top, patched as before, and a
    plain row of the middle told in full under it, through viewside.js; a library without side.js: the side whole."""
    assert HTML.index('src="side.js"') < HTML.index('src="panels.js"') < HTML.index('src="viewside.js"')
    for view in ("plugins.js", "databases.js", "venues.js"):
        assert HTML.index('src="viewside.js"') < HTML.index(f'src="{view}"')
        code = (STATIC / view).read_text(encoding="utf-8")
        own = ", ownPick()" if view == "venues.js" else ""  # 0.2.11: an exchange's product, the view's own pick
        assert f"window.ViewSide.paint(c, side, render, b, SCOPE(){own})" in code, view
        assert "paintSide(c, side, b);" in code and "window.Views.patch(c, side, render);" not in code.replace(
            ": window.Views.patch(c, side, render));", ""), view
        assert "<aside class=\"ex-side\">${window.ViewSide?.on ? '' : side}</aside>" in code, view
        assert "if (window.ViewSide?.on) paintSide(box.children[2], side, box.children[1]);" in code, view
    side = (STATIC / "viewside.js").read_text(encoding="utf-8")
    assert "if (!S) { window.Views.patch(aside, html, again); return null; }" in side
    assert "const slot = panel.tool(scope, null);" in side and "window.Views.patch(slot, html, again);" in side
    assert "S.rows(middle, { selector: 'tr[data-pick]', panel," in side and "panel.refresh(" in side
    assert "[data-product],[data-db],[data-venue],[data-plugin],[data-open]" in side  # a view's own picks stay its own
