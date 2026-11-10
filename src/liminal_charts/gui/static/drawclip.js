'use strict';
/* Copy, cut and paste of drawings (the drawing tools' lines, channels, notes, highlights ...): Ctrl+C,
   Ctrl+X and Ctrl+V (Cmd on a Mac), and a menu on right-click: on a drawing Copy, Cut, Paste,
   Duplicate and Delete; on the chart elsewhere Paste. What is copied is kept in the browser
   (localStorage), so it pastes into the other chart views, another symbol or another tab. A paste
   lands at the pointer (the drawing's first point there, its shape kept in bars and price), or, with
   the pointer off the chart, PASTE_BARS bars to the right of where it was copied from.
   On a touch screen (the user, 2026-11-10: a drawing selected by a tap shows a small bar beside it on touch screens
   only, Delete, Copy, Cut, Paste, Duplicate; the right-click menu stays for the mouse): a finger has neither the menu
   (the chart makes its right click of a mouse's right button alone) nor the keys, so a drawing a finger's tap selects
   has those five beside it; a finger's long press on the chart is the crosshair's, and opens no menu. */
(() => {
  const KEY = 'liminal:drawings:clipboard';
  const PASTE_BARS = 5;
  const chartEl = $('chart');
  let pointer = null;  // the pointer over the chart, in the chart's own pixels
  let memory = null;  // the clipboard when the browser keeps no storage (a private window)
  let rightClicked = null;  // the drawing a right-click landed on (the chart's own event), and when

  chartEl.addEventListener('pointermove', (e) => {
    const b = chartEl.getBoundingClientRect();
    pointer = { x: e.clientX - b.left, y: e.clientY - b.top };
  });
  chartEl.addEventListener('pointerleave', () => { pointer = null; });

  const clip = () => {
    try { return JSON.parse(localStorage.getItem(KEY) || 'null') ?? memory; } catch { return memory; }
  };
  function keep(value) {
    memory = value;
    try { localStorage.setItem(KEY, JSON.stringify(value)); } catch { /* kept in memory only */ }
  }

  const selected = () => (state.selectedOverlay ? chart.getOverlays({ id: state.selectedOverlay })[0] ?? null : null);
  // `pane`: the indicator a drawing below the price was on (app.js paneIndicator): the pane it pastes
  // into, in this chart or another tab's (pane ids are each chart's own).
  const snapshot = (o) => ({
    name: o.name, paneId: o.paneId || 'candle_pane', pane: paneIndicator(o.paneId), extendData: o.extendData ?? null,
    styles: o.styles ?? null, points: o.points.map((p) => ({ timestamp: p.timestamp, value: p.value })),
  });
  // Where an item pastes: the price pane, its indicator's pane, or (copied before `pane` was kept) the
  // same pane if this chart has it; null when the chart shows no such pane.
  const paneFor = (it) => (!it.paneId || it.paneId === 'candle_pane' ? 'candle_pane'
    : (it.pane && indicatorPane(it.pane)) || (chart.getIndicators({ paneId: it.paneId }).length ? it.paneId : null));
  const NAMES = { measure: 'highlight', simpleAnnotation: 'note' };
  const label = (it) => NAMES[it.name] || it.name.replace(/([A-Z])/g, ' $1').toLowerCase().trim();

  // -- bars: a drawing keeps its shape in bars (and price) wherever it is pasted -------------------
  function stepMs(data) {
    const d = [];
    for (let i = Math.max(1, data.length - 50); i < data.length; i++) d.push(data[i].timestamp - data[i - 1].timestamp);
    d.sort((a, b) => a - b);
    return d[d.length >> 1] || 60_000;
  }
  function indexOf(data, ts, step) {  // beyond the bars loaded: counted on at the usual step
    const n = data.length;
    if (ts > data[n - 1].timestamp) return n - 1 + Math.round((ts - data[n - 1].timestamp) / step);
    if (ts < data[0].timestamp) return Math.round((ts - data[0].timestamp) / step);
    return barIndexAt(data, ts);
  }
  function timeAt(data, i, step) {
    const n = data.length;
    if (i < 0) return data[0].timestamp + i * step;
    if (i >= n) return data[n - 1].timestamp + (i - n + 1) * step;
    return data[i].timestamp;
  }

  // -- the actions -----------------------------------------------------------------------------
  function copy(o = selected()) {
    if (!o) return false;
    keep({ items: [snapshot(o)], ticker: state.ticker, at: Date.now() });
    toast(`Copied the ${label(o)}`);
    return true;
  }

  function cut(o = selected()) {
    if (!copy(o)) return false;
    chart.removeOverlay({ id: o.id });  // its onRemoved saves the drawings
    toast(`Cut the ${label(o)}`);
    return true;
  }

  // Draws `items` again with their first point at `at` (chart pixels) where it is on the price pane,
  // else `PASTE_BARS` bars on from where they were. An item whose pane this chart does not show is
  // left out (the chart would put it on the price pane, at an indicator's values). Whether any was drawn.
  function place(items, at) {
    const data = chart.getDataList();
    if (!items?.length || !data.length) return false;
    const step = stepMs(data);
    const first = items[0].points[0];
    let bars = PASTE_BARS, price = 0;
    if (at && items.every((it) => it.paneId === 'candle_pane')) {
      const [p] = chart.convertFromPixel([{ x: at.x, y: at.y }], { paneId: 'candle_pane' });
      if (Number.isFinite(p?.dataIndex) && Number.isFinite(p?.value)) {
        bars = p.dataIndex - indexOf(data, first.timestamp, step);
        price = p.value - first.value;
      }
    }
    let made = 0;
    for (const it of items) {
      const paneId = paneFor(it);
      if (!paneId) continue;
      if (it.name === 'measure') chart.removeOverlay({ groupId: 'user', name: 'measure' });  // one highlight at a time
      const id = chart.createOverlay({
        name: it.name, groupId: 'user', paneId, extendData: it.extendData ?? undefined, styles: it.styles ?? undefined,
        points: it.points.map((pt) => ({ timestamp: timeAt(data, indexOf(data, pt.timestamp, step) + bars, step),
                                         value: pt.value + price })),
        ...overlayEvents(),
      });
      if (id) made++;
    }
    if (!made) return false;
    saveDrawings();
    if (items.some((it) => it.name === 'measure')) scheduleHighlightPanel();
    return true;
  }

  function paste(at = pointer) {
    const c = clip();
    if (!c?.items?.length) return false;
    if (!place(c.items, at)) {
      const pane = c.items.find((it) => it.pane)?.pane;
      if (pane && chart.getDataList().length) toast(`Not pasted: the ${label(c.items[0])} was on ${pane}, not on this chart`);
      return false;
    }
    toast(`Pasted the ${label(c.items[0])}`);
    return true;
  }

  const duplicate = (o) => place([snapshot(o)], null);

  // -- the right-click menu ----------------------------------------------------------------------
  const menu = document.createElement('div');
  menu.className = 'dropdown draw-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'menu');
  chartEl.append(menu);
  let menuFor = null, menuAt = null;

  function openMenu(o, at) {
    menuFor = o; menuAt = at;
    const can = !!clip()?.items?.length;
    const item = (act, text, key, enabled = true) =>
      `<button type="button" class="menu-item" role="menuitem" data-act="${act}"${enabled ? '' : ' disabled'}>${text}<span class="key">${key}</span></button>`;
    menu.innerHTML = (o
      ? item('copy', 'Copy', 'Ctrl+C') + item('cut', 'Cut', 'Ctrl+X') + item('paste', 'Paste', 'Ctrl+V', can)
        + '<hr class="menu-sep">' + item('duplicate', 'Duplicate', '') + item('delete', 'Delete', 'Del')
      : item('paste', can ? `Paste the ${esc(label(clip().items[0]))}` : 'Paste (nothing copied)', 'Ctrl+V', can));
    menu.hidden = false;
    const w = chartEl.clientWidth, h = chartEl.clientHeight;
    menu.style.left = `${Math.min(at.x, w - menu.offsetWidth - 4)}px`;
    menu.style.top = `${Math.min(at.y, h - menu.offsetHeight - 4)}px`;
  }
  const closeMenu = () => { menu.hidden = true; menuFor = null; };

  menu.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-act]');
    if (!b || b.disabled) return;
    const o = menuFor, at = menuAt;
    closeMenu();
    ({ copy: () => copy(o), cut: () => cut(o), paste: () => paste(at), duplicate: () => duplicate(o),
       delete: () => chart.removeOverlay({ id: o.id }) })[b.dataset.act]?.();
  });
  // the chart's own event says which drawing it was; the browser's opens the menu (in either order). A finger's long
  // press (Chrome's and Edge's make one of it) is the crosshair's: no menu, nor the browser's own, there.
  chartEl.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    if (e.pointerType === 'touch' || e.pointerType === 'pen' || (e.pointerType == null && fingered())) return;
    const b = chartEl.getBoundingClientRect();
    const at = { x: e.clientX - b.left, y: e.clientY - b.top };
    setTimeout(() => {
      const o = rightClicked && Date.now() - rightClicked.at < 500 ? rightClicked.overlay : null;
      rightClicked = null;
      openMenu(o, at);
    }, 0);
  });
  document.addEventListener('pointerdown', (e) => { if (!menu.hidden && !menu.contains(e.target)) closeMenu(); }, true);
  chartEl.addEventListener('wheel', closeMenu, { passive: true });

  // -- the bar beside a drawing a finger selected (touch screens) -----------------------------------------------------
  // Above the drawing where there is room, else below it; it follows the drawing as the chart scrolls or zooms, or the
  // drawing is dragged, and goes once it is deselected, removed, or the chart is left. It stands by what is seen of the
  // drawing as the chart last drew it (app.js drawnOf: a horizontal line, a ray, an extended line or a channel runs on
  // past its points, which may be out of view); a long line with no room above it has the bar above where it crosses
  // the middle of what is seen. Paste puts what was copied PASTE_BARS bars to the right of the selected drawing's first
  // point (on the price pane), kept in view.
  const BAR = [['delete', 'Delete'], ['copy', 'Copy'], ['cut', 'Cut'], ['paste', 'Paste'], ['duplicate', 'Duplicate']];
  const bar = document.createElement('div');
  bar.className = 'draw-bar';
  bar.hidden = true;
  bar.setAttribute('role', 'toolbar');
  bar.setAttribute('aria-label', 'The drawing selected');
  bar.innerHTML = BAR.map(([act, text]) => `<button type="button" data-bar="${act}">${text}</button>`).join('');
  chartEl.append(bar);
  let barFor = null, barFrame = 0;  // the drawing's id; the placing waiting for a frame

  const barDrawing = () => (barFor ? chart.getOverlays({ id: barFor })[0] ?? null : null);
  function hideBar() {
    barFor = null;
    bar.hidden = true;
  }
  // the heights (px) at which the segments cross the upright line at x (an upright segment there: its two ends)
  function crossings(lines, x) {
    const out = [];
    for (const [a, b] of lines || []) {
      if (!a || !b || (a.x - x) * (b.x - x) > 0) continue;
      if (a.x === b.x) out.push(a.y, b.y);
      else out.push(a.y + ((b.y - a.y) * (x - a.x)) / (b.x - a.x));
    }
    return out.filter(Number.isFinite);
  }
  function placeBar() {
    barFrame = 0;
    const o = barDrawing();
    if (!o || document.body.dataset.view !== 'chart') { hideBar(); return; }
    const paneId = o.paneId || 'candle_pane';
    // what the chart drew of it (its pane's pixels: down by the pane's top), else its points (the chart's pixels)
    const drawn = drawnOf(o.id);
    const dy = drawn ? chart.getSize(paneId)?.top ?? 0 : 0;
    const at = drawn ? drawn.points
      : [].concat(chart.convertToPixel((o.points || []).map((p) => ({ timestamp: p.timestamp, value: p.value })),
                                       { paneId, absolute: true }));
    const xs = at.map((p) => p.x).filter(Number.isFinite), ys = at.map((p) => p.y + dy).filter(Number.isFinite);
    const plot = chart.getSize('candle_pane', 'main')?.width || chartEl.clientWidth;
    const floor = chart.getSize('x_axis_pane')?.top ?? chartEl.clientHeight;  // not over the time axis
    bar.querySelector('[data-bar="paste"]').disabled = !clip()?.items?.length;
    bar.hidden = false;
    // its drawing out of sight (scrolled off, hidden): the bar waits unseen, and comes back with it
    const seen = o.visible !== false && xs.length && ys.length && Math.max(...xs) >= 0 && Math.min(...xs) <= plot
      && Math.max(...ys) >= 0 && Math.min(...ys) <= floor;
    bar.style.visibility = seen ? '' : 'hidden';
    if (!seen) return;
    const w = bar.offsetWidth, h = bar.offsetHeight;
    const cx = (Math.max(0, Math.min(...xs)) + Math.min(plot, Math.max(...xs))) / 2;
    let top = Math.min(...ys) - h - 14;  // above it, clear of a fingertip on it
    if (top < 4) {
      // no room above it (a long line reaching the chart's top, say): above where its lines cross the middle of what
      // is seen of it, else below that (or below the drawing)
      const cross = drawn ? crossings(drawn.lines, cx).map((y) => y + dy).filter((y) => y >= 0 && y <= floor) : [];
      top = cross.length && Math.min(...cross) - h - 14 >= 4 ? Math.min(...cross) - h - 14
        : (cross.length ? Math.max(...cross) : Math.max(...ys)) + 14;
    }
    bar.style.left = `${Math.round(Math.max(4, Math.min(cx - w / 2, plot - w - 4)))}px`;
    bar.style.top = `${Math.round(Math.max(4, Math.min(top, floor - h - 4)))}px`;
  }
  const follow = () => { if (barFor && !barFrame) barFrame = requestAnimationFrame(placeBar); };
  // app.js overlayEvents: a drawing selected (by a finger's tap, on a touch screen: its bar); `selected` above is the
  // drawing the keys copy and cut
  function barSelected(o) {
    if (!o || !fingered() || !window.LiminalTouch?.touch) { hideBar(); return; }
    barFor = o.id;
    follow();
  }
  function unselected(id) { if (!id || id === barFor) hideBar(); }  // deselected or removed
  for (const action of ['onVisibleRangeChange', 'onScroll', 'onZoom']) chart.subscribeAction(action, follow);
  window.addEventListener('resize', follow);
  window.addEventListener('liminal:view', () => { if (document.body.dataset.view !== 'chart') hideBar(); else follow(); });
  // PASTE_BARS bars on from the selected drawing's first point (chart pixels), when it is on the price pane; kept in
  // view (a horizontal line's or a ray's first point may be far off it, and a paste there would not be seen)
  function besideOf(o) {
    if ((o.paneId || 'candle_pane') !== 'candle_pane' || !o.points?.length) return null;
    const p = chart.convertToPixel({ timestamp: o.points[0].timestamp, value: o.points[0].value }, { paneId: 'candle_pane' });
    if (!Number.isFinite(p?.x) || !Number.isFinite(p?.y)) return null;
    const size = chart.getSize('candle_pane', 'main');
    const width = size?.width || chartEl.clientWidth, height = size?.height || chartEl.clientHeight;
    const x = p.x + PASTE_BARS * chart.getBarSpace().bar;
    return { x: Math.max(8, Math.min(x, width - 8)), y: Math.max(8, Math.min(p.y, height - 8)) };
  }
  bar.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-bar]');
    if (!b || b.disabled) return;
    const o = barDrawing();
    if (!o) { hideBar(); return; }
    ({ delete: () => chart.removeOverlay({ id: o.id }), copy: () => copy(o), cut: () => cut(o),
       paste: () => paste(besideOf(o)), duplicate: () => duplicate(o) })[b.dataset.bar]?.();
    follow();  // still selected (a copy, a paste): Paste offered once something is copied
  });

  // -- keys --------------------------------------------------------------------------------------
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !menu.hidden) { closeMenu(); return; }
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
    if (e.target.closest?.('input, select, textarea, [contenteditable="true"]') || document.querySelector('dialog[open]')) return;
    if (document.body.dataset.view !== 'chart') return;
    const k = e.key.toLowerCase();
    const done = k === 'c' ? copy() : k === 'x' ? cut() : k === 'v' ? paste() : false;
    if (done) e.preventDefault();  // nothing selected or copied: the browser's own copy and paste
  });

  // -- a short note of what happened ---------------------------------------------------------------
  const note = document.createElement('div');
  note.className = 'draw-toast';
  note.hidden = true;
  chartEl.append(note);
  let noteTimer = 0;
  function toast(text) {
    note.textContent = text;
    note.hidden = false;
    clearTimeout(noteTimer);
    noteTimer = setTimeout(() => { note.hidden = true; }, 1600);
  }

  window.DrawingClipboard = {
    rightClick(overlay) { rightClicked = { overlay, at: Date.now() }; },  // app.js overlayEvents
    copy, cut, paste, duplicate,
    selected: barSelected, unselected, follow,  // app.js overlayEvents and noteDrawn: the bar a finger's selection has
  };
})();
