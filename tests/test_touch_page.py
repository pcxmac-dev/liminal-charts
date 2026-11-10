"""The chart page on touch screens and with an on-screen keyboard (the user, 2026-11-10: "let's do an update. I want to
support touch as well as mouse clicking on the interface ... We'll make sure it works across all apps"; "Support for
virtual keyboard on tablets and phones"), on the library's touch.js (liminal-libs 1.42.0). There is no JavaScript
runtime in the suite: these read the page's files, as test_frontend_guards.py does. A mouse and a hardware keyboard
are to do what they did: what is checked here is a finger's, a pen's or an on-screen keyboard's."""
from __future__ import annotations

import re

from .test_frontend_guards import STATIC, _function, _js


def _between(src: str, start: str, end: str) -> str:
    return src.split(start, 1)[1].split(end, 1)[0]


def test_every_page_loads_touch_js_right_after_theme_js_and_makes_room_for_the_keyboard():
    for page, later in (("index.html", ("icons.js", "menu.js", "app.js", "drawclip.js")), ("login.html", ("icons.js", "login.js"))):
        html = (STATIC / page).read_text(encoding="utf-8")
        body = html[html.index("<body"):]
        tag = lambda name: body.index(f'<script src="{name}"></script>')  # noqa: E731
        assert tag("theme.js") < tag("touch.js"), page
        for name in later:
            assert tag("touch.js") < tag(name), (page, name)
        head = html[:html.index("</head>")]
        assert 'content="width=device-width, initial-scale=1, interactive-widget=resizes-content"' in head, page


def test_the_fields_call_up_the_keyboard_they_need():
    html = _js("index.html")
    fetch = _between(html, '<input id="rqSymbol"', ">")
    for part in ('data-keyboard="ticker"', 'autocapitalize="characters"', 'autocorrect="off"', 'spellcheck="false"',
                 'enterkeyhint="go"'):
        assert part in fetch, part
    search = _between(html, '<input id="symbolSearch"', ">")
    for part in ('data-keyboard="search"', 'autocapitalize="off"', 'autocorrect="off"', 'enterkeyhint="go"'):  # Go opens the best match
        assert part in search, part
    name = _between(_js("login.html"), '<input id="newName"', ">")
    for part in ('data-keyboard="name"', 'autocapitalize="words"', 'enterkeyhint="go"'):
        assert part in name, part
    app = _js("app.js")
    assert "window.LiminalTouch?.setKeyboard(i, step === '1' ? 'number' : 'decimal');" in _function(app, "drawTypeParams")
    # typing is never interrupted by a redraw: the type's settings are drawn again once their field is left
    assert "window.LiminalTouch.whenIdle($('typeParams'), drawTypeParams);" in _function(app, "renderTypeParams")


def test_no_keyboard_comes_up_unasked_after_a_fingers_tap():
    app = _js("app.js")
    opening = _function(app, "openSymbolDialog")
    assert "const typeNow = !window.LiminalTouch || window.LiminalTouch.mayFocus();" in opening
    # a modal dialog focuses its first field by itself: its close button holds the focus instead
    assert "dlg.querySelector('.dialog-close')?.toggleAttribute('autofocus', !typeNow);" in opening
    assert opening.index("toggleAttribute('autofocus'") < opening.index("showModal()")
    assert "if (typeNow) $('symbolSearch').focus();" in opening
    assert 'class="dialog-close"' in _js("index.html")
    tabs = _between(app, "$('scopeTabs').addEventListener('click'", "\n});\n")
    assert "if (searchHeld || !window.LiminalTouch || window.LiminalTouch.mayFocus()) $('symbolSearch').focus();" in tabs
    # opened by a tap with a keyboard at hand (a tablet's): a letter typed goes into the search all the same
    keys = _between(app, "$('symbolDialog').addEventListener('keydown', (e) => {", "\n});\n")
    redirect = keys.split("if (moves[e.key]", 1)[0]
    assert "e.key.length === 1 && e.key !== ' '" in redirect and "$('symbolSearch').focus();" in redirect
    assert "!e.target.closest?.('input, select, textarea, [contenteditable=\"true\"]')" in redirect


def test_the_chart_keeps_its_own_gestures_and_a_finger_reaches_a_drawing():
    """A long press on the chart is the library's crosshair: no tooltip bubble there, and no right-click menu (Chrome
    makes a contextmenu of a long press). A drawing's lines are hit within FINGER_LINE px and its anchors are
    FINGER_ANCHOR px round while the pointer is a finger, through the library's own registerFigure; a mouse keeps the
    library's sizes."""
    app = _js("app.js")
    assert "chart.getDom()?.setAttribute('data-touch-own', '');" in app
    reach = _between(app, "kc.registerFigure({\n  name: 'reach',", "\n});\n")
    assert "checkEventOn: (p, attrs) => fingered() &&" in reach and "nearLine(p, a?.coordinates || [], FINGER_LINE)" in reach
    assert "draw: () => {}" in reach  # nothing drawn: only hit
    line = int(re.search(r"const FINGER_LINE = (\d+);", app).group(1))
    anchor = int(re.search(r"const FINGER_ANCHOR = (\d+);", app).group(1))
    assert 12 <= line <= 20 and 14 <= anchor <= 20  # a fingertip's, against the library's 2 px and 5 px
    wrapped = _function(app, "withAnchorMarks")
    assert "const reach = touchUi() && !overlay.isDrawing()" in wrapped and "type: 'reach', attrs: f.attrs" in wrapped
    assert "f.ignoreEvent !== true" in wrapped  # the marks and labels stay out of reach, as they are out of the mouse's
    # a tap in the reach selects; a press in it drags only the drawing selected: a finger's pan begun beside a line is
    # the chart's (the library's _figureMouseDownEvent takes no press its figure ignores), never a drag of the line
    assert "const REACH_NO_DRAG = ['onPressedMoveStart', 'onPressedMoving', 'onPressedMoveEnd'];" in app
    assert "...(selected ? [] : REACH_NO_DRAG)" in wrapped and "const selected = overlay.id === state.selectedOverlay;" in wrapped
    applied = _function(app, "applyChartType")
    assert applied.index("chart.setStyles(ANCHOR_DOTS)") < applied.index("if (anchorFinger) chart.setStyles(anchorReach());")
    assert "radius: p.radius ?? 5, activeRadius: p.activeRadius ?? 5" in app  # the mouse's: the library's own, read
    menu = _between(_js("drawclip.js"), "chartEl.addEventListener('contextmenu', (e) => {", "\n  });")
    assert menu.index("e.preventDefault();") < menu.index(
        "if (e.pointerType === 'touch' || e.pointerType === 'pen' || (e.pointerType == null && fingered())) return;")


def test_a_drawing_a_fingers_tap_selects_has_a_bar_of_its_five_actions():
    """The user, 2026-11-10: a drawing selected by a tap shows a small bar beside it on touch screens only: Delete,
    Copy, Cut, Paste, Duplicate; the right-click menu stays for the mouse."""
    clip = _js("drawclip.js")
    order = re.findall(r"\['(\w+)', '(\w+)'\]", _between(clip, "const BAR = [", "];"))
    assert order == [("delete", "Delete"), ("copy", "Copy"), ("cut", "Cut"), ("paste", "Paste"), ("duplicate", "Duplicate")]
    selected = _between(clip, "function barSelected(o) {", "\n  }\n")
    assert "!fingered() || !window.LiminalTouch?.touch" in selected  # a mouse's selection: no bar
    assert "selected: barSelected, unselected, follow," in clip  # `selected` itself: the drawing the keys copy and cut
    # The bar stands by what the chart drew of the drawing (app.js noteDrawn), not by its points alone: a horizontal
    # line, a ray or an extended line is seen, and selected, while its points are out of view.
    placed = _between(clip, "function placeBar() {", "\n  }\n")
    assert "const drawn = drawnOf(o.id);" in placed and "crossings(drawn.lines, cx)" in placed
    wrapped = _function(_js("app.js"), "withAnchorMarks")
    assert "if (selected && !overlay.isDrawing()) noteDrawn(overlay, coordinates, own);" in wrapped
    assert "window.DrawingClipboard?.follow();" in _function(_js("app.js"), "noteDrawn")  # placed once drawn
    beside = _between(clip, "function besideOf(o) {", "\n  }\n")
    assert "return { x: Math.max(8, Math.min(x, width - 8)), y: Math.max(8, Math.min(p.y, height - 8)) };" in beside
    acts = _between(clip, "bar.addEventListener('click', (e) => {", "\n  });")
    for part in ("chart.removeOverlay({ id: o.id })", "copy(o)", "cut(o)", "paste(besideOf(o))", "duplicate(o)"):
        assert part in acts, part
    for act in ("copy", "cut", "paste", "duplicate", "delete"):  # the mouse's menu as it was
        assert f"item('{act}'" in clip, act
    events = _function(_js("app.js"), "overlayEvents")
    assert "window.DrawingClipboard?.selected(e.overlay);" in events
    assert "window.DrawingClipboard?.unselected(e?.overlay?.id);" in events.split("onDeselected:", 1)[1]
    assert "window.DrawingClipboard?.unselected(e?.overlay?.id);" in events.split("onRemoved:", 1)[1].split("},", 1)[0]
    assert "onPressedMoving: () => { window.DrawingClipboard?.follow(); }" in events
    css = _js("style.css")
    assert ".draw-bar { position: absolute;" in css and ".draw-bar[hidden] { display: none; }" in css


def test_drawing_by_finger_says_what_to_tap_and_a_held_finger_aims_the_next_point():
    app = _js("app.js")
    start = _function(app, "startDrawing")
    assert "hintFor = fingered() ? state.drawingId : null;" in start and "renderDrawHint();" in start
    assert "renderDrawHint();" in _function(app, "cancelDrawing")
    events = _function(app, "overlayEvents")
    assert "saveDrawings(); renderDrawHint(); }" in events.split("onDrawEnd:", 1)[1].split("\n", 1)[0]
    assert "onDrawing: () => { if (hintFor) requestAnimationFrame(renderDrawHint); }" in events
    hint = _function(app, "renderDrawHint")
    assert "Tap the highlight\\'s centre" in hint and "(hold, then slide, to see it first)" in hint
    aim = _between(app, "chart.subscribeAction('onCrosshairChange', (c) => {  // a finger's crosshair", "\n});\n")
    assert "!fingered()" in aim and "(o.currentStep || 1) < 2" in aim
    # aimed where the tap will place it: the magnet's snap as the library's (_coordinateToPoint) on the tap
    assert "const value = magnetValue(o, paneId, p.dataIndex, p.value, c.y);" in aim
    assert "o.stepDrawingModeEventMoveForDrawing({ timestamp: p.timestamp, dataIndex: p.dataIndex, value });" in aim
    snap = _function(app, "magnetValue")
    assert "mode !== 'normal' && paneId === 'candle_pane' ? chart.getDataList()[dataIndex] : null" in snap
    assert "(o.modeSensitivity ?? 8)" in snap and "mode === 'weak_magnet' && !near(bar.high) ? value : bar.high" in snap
    assert 'class="draw-hint-cancel">Cancel</button>' in app  # Escape's way out, for a finger
    assert "drawHint.querySelector('.draw-hint-cancel').addEventListener('click', () => { cancelDrawing(); setActiveTool('cursor'); });" in app


def test_the_newest_bar_is_a_tap_away_on_a_touch_screen_only():
    """The user, 2026-11-10: a small » at the right end of the chart's time axis, on touch screens only, shown only
    when the chart is not at the newest bar; no zoom buttons (pinch zooms)."""
    app = _js("app.js")
    made = _between(app, "const newestBtn = Object.assign(document.createElement('button'), {", "});")
    assert "className: 'newest-btn touch-only'" in made and "textContent: '»'" in made and "hidden: true" in made
    assert "newestBtn.addEventListener('click', () => goLatest());" in app
    place = _function(app, "placeNewest")
    assert "touchUi() &&" in place and "(!atNewest() || !!state.hasNewer)" in place
    assert "placeNewest(top);" in _function(app, "placeTimezone")
    for gone in ("zoomIn", "zoomOut", "latestBtn"):  # the toolbar's buttons stay gone (the user, 2026-10-31)
        assert f"id=\"{gone}\"" not in _js("index.html") and f"$('{gone}')" not in app, gone


def test_the_time_zone_list_turns_by_finger_and_says_which_zones_keep_daylight_saving():
    app = _js("app.js")
    render = _function(app, "renderTimezone")
    assert 'class="tz-step touch-only" data-tz-step="${step}"' in render
    assert "arrow(-1, '▲ West'" in render and "arrow(1, '▼ East'" in render and "length: tzRows()" in render
    assert "<small class=\"tz-dst touch-only\">${dst(z) ? 'keeps daylight saving' : 'no daylight saving'}</small>" in render
    assert "tzBtn.dataset.tip = " in render and "the mouse wheel turns the list." in render  # the mouse's words kept
    down = _between(app, "tzMenu.addEventListener('pointerdown', (e) => {", "\n});\n")
    assert "if (e.pointerType === 'mouse' || !e.isPrimary) return;" in down  # the mouse has its wheel
    assert "const top = wrapZone(d.top - Math.round(dy / d.row));" in _between(app, "tzMenu.addEventListener('pointermove'", "\n});\n")
    pick = _between(app, "tzMenu.addEventListener('click'", "\n});\n")
    assert pick.index("if (performance.now() < tzSwallow) return;") < pick.index("setChartTimezone(b.dataset.tz);")
    assert "if (!touchUi()) return TZ_ROWS;" in _function(app, "tzRows")
    css = _js("style.css")
    coarse = _between(css, "@media (any-pointer: coarse) {\n  button:where(", "\n}\n")
    assert ".tz-menu { touch-action: none; }" in coarse and ".tz-menu button, .sess-menu button { min-height: 40px;" in coarse


def test_the_panes_and_the_side_panel_have_a_fingers_grips():
    app = _js("app.js")
    grip = _function(app, "makePaneGrip")
    assert "chart.setPaneOptions({ id: below.id, height: heightBelow });" in grip
    assert "if (above.id !== 'candle_pane') chart.setPaneOptions({ id: above.id" in grip  # the price pane takes the rest
    assert "shrinking.minHeight ?? 30" in grip and "const ENDS = ['pointerup', 'pointercancel', 'lostpointercapture'];" in grip
    assert "const panes = touchUi() ? chartPanes() : [];" in _function(app, "placePaneGrips")
    assert "placePaneGrips();" in _function(app, "placeAxisGap")
    panel = _between(app, "(function initPanel() {", "\n})();\n")
    assert "const ENDS = ['pointerup', 'pointercancel', 'lostpointercapture'];" in panel
    assert "for (const ev of ENDS) grip.removeEventListener(ev, up);" in panel
    css = _js("style.css")
    assert "#panelResizer { touch-action: none; }" in css
    # a finger's 40 px: the panes' grips and the side panel's (a 6 px look kept)
    assert "const GRIP = { width: 72, height: 40 };" in app
    assert ".pane-grip { position: absolute; z-index: 5; width: 72px; height: 40px;" in css
    assert "#panelResizer::before { content: \"\"; position: absolute; top: 0; bottom: 0; left: -17px; right: -17px; }" in css
    # the now handle: a finger's reach, and its double tap as a double-click
    assert ".now-handle::after { content: \"\"; position: absolute;" in css
    drag = _between(app, "nowHandle.addEventListener('pointerdown'", "\n});\n")
    assert "if (t - nowTapAt < DOUBLE_TAP_MS) { nowTapAt = 0; resetNowPosition(); } else nowTapAt = t;" in drag
    assert "e.pointerType === 'mouse'" in drag and "nowHandle.addEventListener('dblclick', resetNowPosition);" in app


def test_hover_highlights_are_a_mouses():
    """Every :hover of the page is inside @media (hover: hover): a tap leaves none stuck on a touch screen. The one rule
    left as it is shows the watchlist's − on a row's hover (with a mouse it is hidden until then; on a touch screen it
    is always shown)."""
    reveal = ".w-table tr:hover .w-x button, .w-table .w-x button:focus-visible { visibility: visible; }"
    css = re.sub(r"/\*.*?\*/", "", _js("style.css"), flags=re.S)
    for line in css.splitlines():
        if ":hover" in line:
            assert line.startswith("@media (hover: hover) { ") or line.strip() == reveal, line
    assert reveal in css


def test_a_fingers_targets_and_its_fields_words():
    css = _js("style.css")
    coarse = _between(css, "@media (any-pointer: coarse) {\n  button:where(", "\n}\n")
    assert ":not(.switch, .axis-btn, .newest-btn, .pl-link, .pref-swatch)), select { min-height: 40px; }" in coarse
    assert "{ font-size: 16px; font-size: max(16px, 1em); }" in coarse and ":root textarea, :root select" in coarse
    assert "#tools button { width: 40px; height: 40px; padding: 8px; }" in coarse
    assert 'input[type="checkbox"], input[type="radio"] { width: 22px; height: 22px; }' in coarse
    assert "button, select, input, textarea, summary, label, a[href]," in css  # no double-tap zoom on a control
    # nor on the divs a tap opens (the switcher's and the map's tiles) and what a tap explains
    assert '[role="menuitem"], [role="option"], #statusText, #gpuNote, #modelWindow, .derived-chip, .params .approx {' in css
    assert ".derived-chip { display: inline-flex; align-items: center; min-height: 40px;" in coarse
    assert ".auto-level { display: inline-flex; align-items: center; min-height: 40px;" in coarse
    assert "bottom: 0; font-size: 10px;" in _between(css, ".tool-badge {", "}")  # the magnet's mode, readable
    assert "#sidePanel, #symbolList, .dropdown, .w-wrap, .sch-trades { overscroll-behavior: contain; }" in coarse
    assert "html:not(.touch) .touch-only, html.touch .mouse-only { display: none !important; }" in css
    # the page fitted above a keyboard laid over it (Safari), as liminal.css has it
    assert "@media not all and (max-width: 700px) { html.keyboard body { height: var(--vvh, 100%); } }" in css


def test_heights_follow_the_part_of_the_window_seen():
    """dvh beside every vh: on a phone 100vh reaches under the browser's bars."""
    css = _js("style.css")
    found = re.findall(r"[a-z-]+: [^;{}]*\dvh[^;{}]*;", css)
    assert len(found) >= 15
    for decl in found:
        twin = re.sub(r"(\d)vh", r"\1dvh", decl)
        assert decl + " " + twin in css, decl


def test_on_a_phone_the_side_panel_lies_over_the_chart():
    css = _js("style.css")
    phone = _between(css, "@media (max-width: 700px) {\n  body[data-view=\"chart\"]", "\n}\n")
    for part in ("#sidePanel { position: absolute; z-index: 40;", "#tools { flex-direction: row;", "#panelResizer { display: none; }",
                 ".panel-close { display: grid;", "#chart { flex: 1; width: 100%;"):
        assert part in phone, part
    assert "#tools { display: none; }" not in css  # the tools were hidden below 640 px: a phone could not draw
    # the × in a row of its own at the panel's top: over none of the panel's controls as it scrolls
    assert ".panel-close-row { display: flex; justify-content: flex-end; position: sticky; top: 0;" in phone
    assert "-40px" not in _between(phone, ".panel-close {", "}")
    html = _js("index.html")
    assert '<div class="panel-close-row"><button type="button" id="panelClose" class="panel-close"' in html
    app = _js("app.js")
    # the panel the phone's layout hides (the page opened on it, a symbol picked in it) is not kept hidden: the panel as
    # you left it is, and a window made wider shows it again
    hidden = _function(app, "setPanelHidden")
    assert "const byPhone = narrow() && (hidden ? panelByPhone" in hidden
    assert ": !!navigator.userActivation && !navigator.userActivation.isActive);" in hidden
    assert "panelForced = byPhone;" in hidden and "if (!byPhone) prefs.set('panelHidden', hidden);" in hidden
    opening = _function(app, "openSymbol")
    assert "if (narrow() && !document.body.classList.contains('panel-hidden')) {" in opening
    assert "panelByPhone = true;" in opening and "finally { panelByPhone = false; }" in opening
    wider = _between(app, "NARROW?.addEventListener?.('change', () => {", "\n  });\n")
    assert "if (narrow() || !panelForced) return;" in wider and "if (prefs.get('panelHidden', false)) return;" in wider
    assert "window.showPane(document.body.dataset.pane, { toggle: false });" in wider


def test_what_a_tooltip_tells_to_act_is_words_on_a_touch_screen():
    app = _js("app.js")
    for spot in ("$('periods').addEventListener('click', (e) => tipOnTap(e.target.closest('.derived-chip')));",
                 "$('typeParams').addEventListener('click', (e) => tipOnTap(e.target.closest('.approx')));",
                 "$('modelWindow').addEventListener('click', (e) => tipOnTap(e.currentTarget));",
                 "$('statusText').addEventListener('click', (e) => tipOnTap(e.currentTarget));"):
        assert spot in app, spot
    assert "if (!el || !fingered() || !window.LiminalTouch) return false;" in _function(app, "tipOnTap")  # a mouse hovers
    assert "not collected yet: + Watch has the fetcher collect it" in _function(app, "renderMapPick")
    assert "s.className = 'type-about touch-only';" in _function(app, "drawTypeParams")  # the chart type's description
    assert "note: gpuNote()" in app and 'id="gpuNote" class="gpu-note touch-only"' in _js("index.html")  # the GPU's state
    assert "badge.textContent = { weak_magnet: 'weak', strong_magnet: 'strong' }[state.magnet] || '';" in app
    # what the * chips and the approx. tag mean, in a few words beside them (a tap shows the whole)
    assert "chipGist(chip, interp ? 'drawn between bars' : some === total ? 'built here' : '◦ built here');" in app
    assert "chipGist(chip, off ? 'apart from the feed' : 'history before the feed');" in app
    assert "className: 'chip-gist touch-only'" in _function(app, "chipGist")
    assert "const gist = String(state.approximate).split(':')[0].trim();" in _function(app, "drawTypeParams")
    # a finger's long press reads a finger's words, not the mouse's or a key's
    assert "sessBtn.dataset.tip = `Sessions shown: ${what}. Tap to change.`;" in app
    assert "'tap to switch symbol'" in app and 'data-tip="Tap to switch symbol"' in _js("index.html")
    assert "tag.dataset.tip = tag.title.replace(/\\bClick to\\b/g, 'Tap to');" in app
    assert "$('sortBtn').dataset.tip = `${sort.title} (tap to change)`;" in app
    # a touch screen's words added to the mouse's or the key's (a touch screen may have a mouse and a keyboard too)
    html = _js("index.html")
    assert 'class="touch-only">Drag to turn · pinch or scroll to zoom · tap or click the sun to recentre</span>' in html
    assert 'class="mouse-only">Drag to turn · scroll to zoom · click the sun to recentre</span>' in html
    assert "Draw a highlight (Alt+H<span" in _function(app, "renderHighlightPanel")
    assert "mouse-only" not in _function(app, "renderHighlightPanel")


def test_no_script_of_the_page_declares_a_name_twice_in_its_own_scope():
    """A name declared twice where one is a const, let or class is an early SyntaxError: the whole script never runs
    (drawclip.js's `selected`, 2026-11-10: copy, cut, paste and the drawing's bar were all gone, and nothing said so).
    Each script of the app's own on the page is one function's body (declared two spaces in) or, app.js, the page's
    top level (declared at the line's start, shared by every script)."""
    decl = re.compile(r"^(?P<indent> *)(?:async +)?(?P<kind>const|let|var|class|function\*?) +(?P<name>[A-Za-z_$][\w$]*)"
                      r" *(?:[=;,(]|extends\b|\{)", re.M)
    pages = [_js("index.html"), _js("login.html")]
    names = sorted({n for page in pages for n in re.findall(r'<script src="([^"]+)"></script>', page)
                    if not n.startswith("vendor/") and (STATIC / n).is_file()})
    assert {"app.js", "drawclip.js", "timeaxis.js", "login.js"} <= set(names)
    for name in names:
        src = (STATIC / name).read_text(encoding="utf-8")
        indent = "  " if re.search(r"^\((?:\(\) =>|function *\(\)) *\{", src, re.M) else ""
        seen: dict[str, list[str]] = {}
        for m in decl.finditer(src):
            if m.group("indent") == indent:
                seen.setdefault(m.group("name"), []).append(m.group("kind"))
        twice = {k: v for k, v in seen.items() if len(v) > 1 and set(v) & {"const", "let", "class"}}
        assert not twice, (name, twice)
