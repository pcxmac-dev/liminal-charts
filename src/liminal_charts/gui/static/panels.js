'use strict';
/* The right-hand icon rail, like TradingView's. Each icon switches the main window:

     Stock     the chart, with the symbol (its own logo), key statistics and business beside it
     Analysis  the same symbol's chart with content of its own (interval, chart type, model,
               indicators and their panes, zoom, drawings), the highlight's measurements and the
               bar under the cursor beside it; schemes and replay (schemes.js, replay.js)
     Trading   the same symbol's chart with content of its own, the paper account and the brokers beside it
               (trading.js); live trading is off
     Exchanges every tracked exchange (NYSE, Nasdaq, CME Group's four, indices, crypto, forex, Blue
               Ocean overnight), its products, and insight into them on the right (venues.js)
     Markets   the world's exchanges on a globe turned to where it is noon now (globe.js)
     News      every announcement the globe's ticker shows (news.js)
     Federal   the federal agencies and regulators (the Federal plugin): current regulatory parameters, the Fed's, the SEC's,
               BLS's and the Federal Register's newest (gov.js)
     Datasets  research series drawn on one time axis: factors, CAPE, COT positioning (datasets.js)
     Fetcher   historical data (sessions before the current one); coloured while a run is active
     Live      current-session feeds; a red "recording" dot while live data is being recorded
     Plugins   every data provider plugin (a feed or source): its plan and where that came from, its
               limits and capabilities, its last capability scan with a Rescan, its part in the
               delivery scheme (plugins.js)
     Databases every database the integrator keeps (SQLite, DuckDB, MongoDB): its size on disk, growth an
               hour, last write, its tables' rows; the one picked, its specifics beside it (databases.js)
     Resources the app's processes (CPU, memory, disk, connections), the machine's bandwidth, the
               disks and the data files (resources.js)

   Clicking Stock or Analysis again while it is showing hides or shows its side panel. A right click on a view's
   icon, or a long press of a finger (the library's menu.js and touch.js, on every device), offers to disable it:
   its icon leaves the rail until its switch in Preferences enables it again (prefs.js), kept per profile.
   Also: the cursor statistics box in the price window's upper right (on-screen max/min and the bar under the
   cursor), and the views' redraws that neither break a tap nor interrupt typing (Views.redraw, Views.patch). */
(() => {
  const { symbolIcon, icon, esc } = window.LiminalIcons;
  // The watchlist and the preferences sit beside whichever chart was showing (no chart content of their own).
  const VIEWS = { stock: 'chart', analysis: 'chart', trading: 'chart', watchlist: 'chart', prefs: 'chart', markets: 'markets',
                  news: 'news', gov: 'gov', datasets: 'datasets', fetcher: 'fetcher', live: 'live', resources: 'resources',
                  exchanges: 'exchanges', plugins: 'plugins', databases: 'databases' };
  const BESIDE = new Set(['watchlist', 'prefs']);

  // -- the views turned off ------------------------------------------------------------------
  // The user, 2026-11-07, of the platform's tray: "allow me to right click on the icons to disable them on the right
  // icon menu, also allow for enable disable switch (left to right)": here a right click on a view's rail icon offers
  // Disable, and the icon stays out of the rail (the view with it: nothing opens it) until its switch in Preferences
  // enables it again. Kept per profile. Stock (the app's own icon, the symbol's, at the top) and Preferences (where the
  // switches are) cannot be turned off; the fetcher's own page (nobody's, no Preferences) shows every view it has.
  const RAIL_VIEWS = [  // as on the rail: the view, its name, its icon (icons.js; Stock's is the symbol's own)
    ['stock', 'Stock', 'analysis'], ['analysis', 'Analysis', 'integral'], ['trading', 'Trading', 'trading'],
    ['watchlist', 'Watchlist', 'watchlist'], ['prefs', 'Preferences', 'sliders'], ['exchanges', 'Exchanges', 'scales'],
    ['markets', 'Markets', 'globe'], ['news', 'News', 'thermometer'], ['gov', 'Federal', 'statehouse'],
    ['datasets', 'Datasets', 'scroll'], ['fetcher', 'Fetcher', 'disk'], ['live', 'Live', 'record'],
    ['plugins', 'Plugins', 'plug'], ['databases', 'Databases', 'database'], ['resources', 'Resources', 'chip']];
  const FIXED = new Set(['stock', 'prefs']);
  const viewsOff = new Set(window.FETCHER_MODE ? [] : [].concat(prefs.get('viewsOff', []) || [])
    .filter((v) => typeof v === 'string' && v in VIEWS && !FIXED.has(v)));
  const viewOn = (name) => !viewsOff.has(name);

  function drawViewsOff() {  // the icons of the views turned off out of the rail; a divider left with no icon after it too
    if (window.FETCHER_MODE) return;
    const rail = document.getElementById('rail');
    for (const b of rail.querySelectorAll('button[data-pane]')) if (b.dataset.pane in VIEWS) b.hidden = viewsOff.has(b.dataset.pane);
    for (const hr of rail.querySelectorAll(':scope > hr')) {
      let next = hr.nextElementSibling;
      while (next && next.tagName !== 'HR' && next.hidden) next = next.nextElementSibling;
      hr.hidden = !next || next.tagName === 'HR';
    }
  }

  function setViewOn(name, on) {
    if (window.FETCHER_MODE || FIXED.has(name) || !(name in VIEWS) || viewOn(name) === on) return;
    if (on) viewsOff.delete(name); else viewsOff.add(name);
    prefs.set('viewsOff', [...viewsOff]);
    drawViewsOff();
    if (name === 'live') window.LiveIcon?.update((window.Activity?.data?.liveFeeds?.liveTickers || []).length > 0);  // its trace: only on the rail
    if (!on && document.body.dataset.pane === name) showPane('stock', { toggle: false });  // the view showing went: the chart
    window.dispatchEvent(new CustomEvent('liminal:views', { detail: { off: [...viewsOff] } }));
  }
  // -- views drawn again without breaking a tap or a word being typed ---------------------------------------
  // The user, 2026-11-10: "I want to support touch as well as mouse clicking on the interface ... We'll make sure it
  // works across all apps"; "Support for virtual keyboard on tablets and phones". The views are drawn again on timers
  // (Live every 1.5 s, Resources every 2 s, a plugin's page every 5 s): what a finger was on was replaced under it (its
  // tap then came to nothing, what it pressed being gone), a fling stopped, and the field being typed in was replaced
  // (the on-screen keyboard closed, a word being composed was cut).
  //   Views.redraw(root, fn)       fn now, or once nothing presses on root (a finger, a pen or the mouse's button) and
  //                                its tap's click has come, and no finger's scroll (its fling included) moves in it;
  //                                the latest fn for a root wins
  //   Views.patch(el, html, again) el's children drawn from html, each one only when its markup changed (a tap on one
  //                                left as it was lands); the one holding the field typed in stays until it is left
  //                                (again() then draws it); what is replaced keeps its scroll
  //   Views.letGo(root)            a field still focused in root let go, before a redraw for the user's own pick (Safari
  //                                keeps a field focused when a button is tapped: its part would be kept, Views.patch)
  //   Views.ownScroll(e)           the scroll event a redraw caused (a part given back its place), not the user's
  //   Views.ownPlace(el)           el's place, just given back by the view's own code, known as the page's (ownScroll)
  //   Views.softKeyboard()         an on-screen keyboard is up now (a hardware keyboard's Enter does what it did)
  //   Views.reveal(el)             on a phone (the columns stacked, below 700 px) el scrolled into view: what a pick changed
  const presses = new Map();  // pointerId -> what it went down on
  let lift = null;  // {target, timer}: a press just ended, its click still to come
  let fling = null;  // {target, timer}: a finger's scroll still moving
  const heldDraws = new Map();  // root -> the redraw waiting for it
  let recheck = 0;  // while a redraw waits, a look every half second (a menu closed by Escape tells nobody)
  const pressedIn = (root, t) => t instanceof Node && (root === document || root.contains(t));
  const scrolledIn = (root, t) => t === document || !(t instanceof Node) || root === document || root.contains(t) || t.contains(root);
  function busy(root) {
    // a menu open (menu.js: a rail icon's, by a right click or a long press): nothing drawn again until it goes, for a
    // part drawn again is given back its scroll, and a scroll closes the menu before its item can be picked
    if (window.LiminalMenu?.shown) return true;
    for (const t of presses.values()) if (pressedIn(root, t)) return true;
    return (!!lift && pressedIn(root, lift.target)) || (!!fling && scrolledIn(root, fling.target));
  }
  function releaseDraws() {
    for (const [root, fn] of [...heldDraws]) {
      if (busy(root)) continue;
      heldDraws.delete(root);
      if (root === document || root.isConnected) {
        try { fn(); } catch (err) { setTimeout(() => { throw err; }); }  // the other views' redraws go on
      }
    }
    if (!heldDraws.size && recheck) { clearInterval(recheck); recheck = 0; }
  }
  function redraw(root, fn) {
    const r = root || document;
    if (busy(r)) {
      heldDraws.set(r, fn);
      if (!recheck) recheck = setInterval(releaseDraws, 500);
      return false;
    }
    heldDraws.delete(r);
    fn();
    return true;
  }
  document.addEventListener('pointerdown', (e) => {
    if (e.isTrusted && (e.pointerType !== 'mouse' || e.button === 0)) presses.set(e.pointerId, e.target);
  }, { capture: true, passive: true });
  // A choice made in a picker's own popup (a select's list: Chrome's takes the mouse button's release, and the page
  // never hears it): its press is over, and the redraw the choice asks for is not held until the mouse moves again
  document.addEventListener('change', (e) => {
    if (!e.isTrusted) return;  // a script's (Preferences passing a pick to the toolbar's hidden picker)
    let ended = false;
    for (const [id, t] of [...presses]) {
      if (t instanceof Node && e.target instanceof Node && e.target.contains(t)) { presses.delete(id); ended = true; }
    }
    if (ended) setTimeout(releaseDraws, 0);
  }, true);
  function pressEnded(e) {
    if (!presses.has(e.pointerId)) return;
    const target = presses.get(e.pointerId);
    presses.delete(e.pointerId);
    clearTimeout(lift?.timer);
    // its click comes after the lift (on a phone a moment later); a press a scroll took has none, but its scroll's
    // first moves come a frame or two later (then the fling holds what it moves)
    lift = { target, timer: setTimeout(() => { lift = null; releaseDraws(); }, e.type === 'pointerup' ? 350 : 150) };
  }
  document.addEventListener('pointerup', pressEnded, true);
  document.addEventListener('pointercancel', pressEnded, true);
  document.addEventListener('pointermove', (e) => {  // a mouse's button let go where the page did not hear it
    if (e.pointerType === 'mouse' && e.buttons === 0 && presses.has(e.pointerId)) pressEnded({ type: 'pointercancel', pointerId: e.pointerId });
  }, { capture: true, passive: true });
  window.addEventListener('click', () => {  // the tap's click: what waited is drawn once the click's own handlers ran
    if (!lift) return;
    clearTimeout(lift.timer);
    lift = null;
    setTimeout(releaseDraws, 0);
  }, true);
  window.addEventListener('blur', () => { presses.clear(); releaseDraws(); });
  document.addEventListener('scroll', (e) => {  // a finger's scroll, and its fling: nothing in it drawn again until it stops
    const by = window.LiminalTouch?.lastInput;
    if ((by !== 'touch' && by !== 'pen') || ownScroll(e)) return;
    clearTimeout(fling?.timer);
    fling = { target: e.target, timer: setTimeout(() => { fling = null; releaseDraws(); }, 250) };
  }, { capture: true, passive: true });

  const drawnAs = new WeakMap();  // an element -> its markup when drawn: drawn again alike, it is left as it is
  const SCROLLERS = '.table-wrap, .act-log, .pl-log, .ds-list, .w-wrap, .w-addlist, .l2-wrap';
  const markupOf = (n) => (n.nodeType === 1 ? n.outerHTML : n.textContent);
  const shown = (n) => n.nodeType === 1 || (n.nodeType === 3 && n.data.trim() !== '');
  const sameKind = (a, b) => a.nodeType === 1 && b.nodeType === 1 && a.localName === b.localName && a.className === b.className;
  const scrollsOf = (el) => [el, ...el.querySelectorAll(SCROLLERS)].map((x) => [x.scrollLeft, x.scrollTop]);
  // The places redraws gave back (a scroller -> [left, top]): the scroll event that follows is the page's own, which
  // hides no reading (resources.js) and holds no redraw as a finger's fling would
  const gaveBack = new WeakMap();
  function ownPlace(el) { gaveBack.set(el, [el.scrollLeft, el.scrollTop]); }
  function scrollBack(el, left, top) {
    const was = [el.scrollLeft, el.scrollTop];
    if (left != null && el.scrollLeft !== left) el.scrollLeft = left;
    if (top != null && el.scrollTop !== top) el.scrollTop = top;
    if (el.scrollLeft !== was[0] || el.scrollTop !== was[1]) ownPlace(el);
  }
  function ownScroll(e) {  // the scroll event of a place a redraw gave back: its scroller still there (a user's moves it)
    const t = e?.target, at = t instanceof Element ? gaveBack.get(t) : null;
    return !!at && Math.abs(t.scrollLeft - at[0]) < 1 && Math.abs(t.scrollTop - at[1]) < 1;
  }
  function putScrolls(el, at) {  // by their place in it: the element, then its scrollers in order
    [el, ...el.querySelectorAll(SCROLLERS)].forEach((x, i) => {
      const [left, top] = at[i] || [0, 0];
      scrollBack(x, left || null, top || null);
    });
  }
  function patch(el, html, again) {
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    const nodes = [...tpl.content.childNodes];
    const next = nodes.filter(shown);
    const old = [...el.childNodes].filter(shown);
    const touch = window.LiminalTouch;
    const typed = touch?.typing(el) ? old.find((n) => n.nodeType === 1 && touch.typing(n)) || null : null;
    const later = () => { if (typed && again) touch.whenIdle(typed, again); };
    if (old.length === next.length && old.every((n, i) => n.nodeType === next[i].nodeType)) {
      next.forEach((n, i) => {
        const o = old[i], markup = markupOf(n);
        if (drawnAs.get(o) === markup) return;  // as it was: left as it is
        if (o === typed) { later(); return; }  // the field typed in stays; drawn again once it is left
        if (o.nodeType === 3) { o.textContent = n.textContent; drawnAs.set(o, markup); return; }
        const at = sameKind(o, n) ? scrollsOf(o) : null;
        o.replaceWith(n);
        drawnAs.set(n, markup);
        if (at) putScrolls(n, at);
      });
      return;
    }
    if (typed) {  // drawn otherwise: around the element typed in, which stays where it is (moved, it loses the focus)
      const j = nodes.findIndex((n) => sameKind(n, typed));
      later();
      if (j < 0) return;  // no place for it in the new: drawn whole once it is left
      for (const n of [...el.childNodes]) if (n !== typed) n.remove();
      typed.before(...nodes.slice(0, j));
      typed.after(...nodes.slice(j + 1));
      for (const n of [...nodes.slice(0, j), ...nodes.slice(j + 1)]) if (shown(n)) drawnAs.set(n, markupOf(n));
      return;
    }
    const at = scrollsOf(el);
    const marks = next.map(markupOf);
    el.replaceChildren(tpl.content);
    next.forEach((n, i) => drawnAs.set(n, marks[i]));
    putScrolls(el, at);
  }

  // The user's own pick (another plugin, venue or product): the field still focused in root is let go first, so the
  // page is drawn for what was picked (the part holding a field typed in is kept as it was, Views.patch: it would show
  // the plugin left under the one picked, and a key typed there go to the plugin left)
  function letGo(root) {
    const f = document.activeElement;
    if (f && f !== document.body && root?.contains(f) && window.LiminalTouch?.isTextField?.(f)) f.blur();
  }

  // An on-screen keyboard up now (Enter in a filter puts it away, so what it found shows; a field saved from it is not
  // focused again, which would call it up unasked): touch.js's keyboardOpen (Safari lays it over the page), or the
  // window that much shorter than before a field took the focus (Chrome makes room above it). A hardware keyboard, a
  // 2-in-1's cover's included, shows none on the screen: its Enter does what it did.
  let winTall = innerHeight, winWide = innerWidth;  // the window while no field is typed in
  const fieldFocused = () => !!window.LiminalTouch?.isTextField?.(document.activeElement);
  const measureWindow = () => { if (!fieldFocused() || innerWidth !== winWide) { winTall = innerHeight; winWide = innerWidth; } };
  window.addEventListener('resize', measureWindow);
  document.addEventListener('focusout', () => setTimeout(measureWindow, 0), true);
  function softKeyboard() {
    const t = window.LiminalTouch;
    if (!t || (t.lastInput !== 'touch' && t.lastInput !== 'pen')) return false;
    return !!t.keyboardOpen || (innerWidth === winWide && innerHeight < winTall - 120);
  }

  // Phones (below 700 px a view's columns stack, views-touch.css): what a pick changed lies below what was tapped, out
  // of sight; it is brought into view (the plugin, venue or database picked, a product's insight). Larger screens: as is.
  const stacked = window.matchMedia ? window.matchMedia('(max-width: 700px)') : null;
  function reveal(el) {
    if (!el || !stacked?.matches) return false;
    el.scrollIntoView({ block: 'start', inline: 'nearest' });
    return true;
  }

  window.Views = { list: () => RAIL_VIEWS.map(([name, title, ic]) => ({ name, title, icon: ic, on: viewOn(name), fixed: FIXED.has(name) })),
                   on: viewOn, set: setViewOn, redraw, patch, letGo, ownScroll, ownPlace, softKeyboard, reveal };

  // -- rail --------------------------------------------------------------------------------
  function showPane(name, { toggle = true } = {}) {
    // a bound app's page (bound-login: the login app's), whole, in the bound view
    const app = name.startsWith('bound-') && BOUND.has(name.slice(6)) ? name.slice(6) : null;
    // the fetcher's own page has no chart views (window.FETCHER_MODE, app.js): the Fetcher instead; a view turned off: the chart
    if (!app && (!(name in VIEWS) || !viewOn(name) || (window.FETCHER_MODE && VIEWS[name] === 'chart'))) name = window.FETCHER_MODE ? 'fetcher' : 'stock';
    const view = app ? 'bound' : VIEWS[name];
    if (app) showBound(app);
    const same = document.body.dataset.pane === name && document.body.dataset.view === view;
    if (view === 'chart' && same && toggle) {
      setPanelHidden(!document.body.classList.contains('panel-hidden'));
      updateRail();
      return;
    }
    document.body.dataset.pane = name;
    document.body.dataset.view = view;
    if (view !== 'chart') state.selectedOverlay = null;  // Delete on another view deleted the hidden chart's drawing
    prefs.set('pane', name);
    if (view === 'chart' && !BESIDE.has(name)) window.switchWorkspace?.(name);  // each chart view keeps its own content
    for (const v of document.querySelectorAll('main > .view')) v.hidden = v.dataset.view !== view;
    if (view === 'chart') {
      for (const p of document.querySelectorAll('#sidePanel .pane')) p.hidden = p.dataset.pane !== name;
      if (document.body.classList.contains('panel-hidden')) setPanelHidden(false);
      requestAnimationFrame(() => { chart.resize(); checkLod(); });
      if (name === 'stock') loadInfo();
      if (name === 'analysis') { renderHighlightPanel(); renderCursor(); }
    }
    updateRail();
    window.dispatchEvent(new CustomEvent('liminal:view', { detail: { pane: name, view } }));
  }
  window.showPane = showPane;

  function updateRail() {
    const pane = document.body.dataset.pane;
    const chartPanelHidden = document.body.dataset.view === 'chart' && document.body.classList.contains('panel-hidden');
    for (const b of document.querySelectorAll('#rail button')) {
      const on = b.dataset.pane === pane;
      b.classList.toggle('active', on);
      b.classList.toggle('dim', on && chartPanelHidden);
      b.setAttribute('aria-pressed', String(on));
    }
    window.LiveIcon?.redraw();  // the selected one is drawn blue
    window.GlobeIcon?.update();
  }

  const RAIL_ICON = 32;  // px; the buttons (style.css #rail) leave room around it
  // the words a tooltip says to act by: a finger's beside the mouse's where the device has a touch screen
  const pressWord = () => (window.LiminalTouch?.touch ? 'Click or tap' : 'Click');

  function renderRailIcons(activity) {
    // the symbol's logo, the front of the card the throbber turns while it updates (throbber.js)
    const stockIcon = state.ticker ? symbolIcon(symbolInfo(state.ticker), RAIL_ICON + 4) : icon('analysis', RAIL_ICON);
    window.Throbber.face($('railStock'), stockIcon, RAIL_ICON + 4);
    $('railAnalysis').innerHTML = icon('integral', RAIL_ICON);
    $('railWatch').innerHTML = icon('watchlist', RAIL_ICON);
    $('railTrading').innerHTML = icon('trading', RAIL_ICON);
    if (!window.GlobeIcon?.update()) $('railMarkets').innerHTML = icon('globe', RAIL_ICON);  // the Earth, once its images are here
    $('railNews').innerHTML = icon('thermometer', RAIL_ICON);
    $('railGov').innerHTML = icon('statehouse', RAIL_ICON);
    $('railDatasets').innerHTML = icon('scroll', RAIL_ICON);
    $('railResources').innerHTML = icon('chip', RAIL_ICON);
    $('railExchanges').innerHTML = icon('scales', RAIL_ICON);
    $('railPlugins').innerHTML = icon('plug', RAIL_ICON);
    $('railDatabases').innerHTML = icon('database', RAIL_ICON);
    $('railPrefs').innerHTML = icon('sliders', RAIL_ICON);
    const running = !!activity?.running;
    const fresh = activity?.newData || 0;
    // its platter turns while a run fetches (.busy): redrawn only when it changes, or each poll would
    // restart the turn from the top
    const fetcherIcon = icon('disk', RAIL_ICON)
      + (!running && fresh ? `<span class="rail-count" title="${esc(fresh)} symbols should have new data">${esc(fresh)}</span>` : '');
    if ($('railFetcher').dataset.drawn !== fetcherIcon) {
      $('railFetcher').innerHTML = fetcherIcon;
      $('railFetcher').dataset.drawn = fetcherIcon;
    }
    $('railFetcher').classList.toggle('busy', running);
    $('railFetcher').title = running
      ? `Fetcher: running${activity.live?.current ? `, fetching ${activity.live.current}` : ''}`
      : 'Fetcher: historical data (sessions before the current one)';
    const live = activity?.liveFeeds;
    if (!$('railLive').querySelector('svg')) $('railLive').insertAdjacentHTML('afterbegin', icon('record', RAIL_ICON));
    const streaming = live?.liveTickers || [];
    window.LiveIcon?.update(streaming.length > 0);  // live data arriving: the icon's effect runs
    $('railLive').classList.toggle('recording', !!live?.recording);
    $('railLive').classList.toggle('available', !!live?.available && !live?.recording);
    $('railLive').title = isLive() ? `Live feeds: ${state.ticker} is streaming now`
      : streaming.length ? `Live feeds: ${streaming.length} symbol${streaming.length > 1 ? 's' : ''} streaming now`
      : live?.recording ? 'Live feeds: recording the current session'
        : live?.available ? 'Live feeds: delayed data available, not recording' : 'Live feeds: none on your plan';
  }

  document.getElementById('rail').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-pane]');
    if (b) showPane(b.dataset.pane);
  });
  // a right click on a view's icon: Disable (Stock's, Preferences', the profile's and a bound app's keep the browser's menu).
  // A finger's long press comes here too, as a right click (menu.js on '#rail button': a contextmenu at the finger), on
  // every device, Safari's included; the menu's head then says the icon's own words (its title: the Fetcher's run, the
  // Live feeds' streams), which no finger can hover, and where no menu opens the icon's words show in a bubble (touch.js).
  document.getElementById('rail').addEventListener('contextmenu', (e) => {
    const b = e.target.closest('button[data-pane]');
    const view = b && RAIL_VIEWS.find(([name]) => name === b.dataset.pane);
    if (!view || window.FETCHER_MODE || FIXED.has(view[0]) || !window.LiminalMenu) return;
    e.preventDefault();
    const r = b.getBoundingClientRect();
    const keyboard = e.clientX === 0 && e.clientY === 0;  // the menu key, or Shift+F10: beside the icon
    window.LiminalMenu.open({ x: keyboard ? r.left - 170 : e.clientX, y: keyboard ? r.top : e.clientY, title: view[1],
      items: [{ label: 'Disable', run: () => setViewOn(view[0], false) }], from: b });
  });
  // The apps bound to this front (the login app), each its own tray at the foot of the rail after a
  // divider (the user, 2026-11-04: "Each divider represents a separate tray. From a separate object
  // like the login"); an icon opens the app's page, whole, in the bound view (?embedded=1 hides its
  // own rail). The front answers /api/trays itself; the integrator's own page has no bound apps.
  const BOUND = new Set();
  function showBound(app) {
    const f = document.getElementById('boundFrame');
    // in this page's theme (the UI's: theme.js; the bound app's page honours a `theme` in its address)
    if (f.dataset.app !== app) { f.src = `/${encodeURIComponent(app)}/?embedded=1&theme=${encodeURIComponent(state.theme)}`; f.dataset.app = app; }
  }
  window.addEventListener('liminal:theme', () => {  // the bound page shown since: painted again in place (same origin)
    try { document.getElementById('boundFrame').contentWindow?.LiminalTheme?.apply(state.theme); } catch (e) { /* not loaded */ }
  });
  async function drawBoundTrays() {
    let trays = [];
    try { const r = await fetch('/api/trays'); if (r.ok) trays = (await r.json()).trays || []; } catch (e) { trays = []; }
    const rail = document.getElementById('rail');
    for (const t of trays) {
      if (!t || !t.app || BOUND.has(t.app)) continue;
      BOUND.add(t.app);
      rail.appendChild(document.createElement('hr'));
      for (const item of t.items || []) {
        const b = document.createElement('button');
        b.type = 'button';
        b.dataset.pane = `bound-${t.app}`;
        b.id = `railBound-${t.app}-${item.id}`;
        if (item.kind === 'app') b.classList.add('master');
        b.title = item.title || t.title || t.app;
        b.innerHTML = icon(item.icon, RAIL_ICON);
        rail.appendChild(b);
      }
      if (t.app === 'login') watchLogin();
    }
  }
  function watchLogin() {
    // the sign-in state on the icon; asked every minute, which keeps the login app in touch
    const refresh = async () => {
      try {
        const r = await fetch('/login/api/status');
        const st = r.ok ? await r.json() : null;
        const b = document.getElementById('railBound-login-login');
        if (b && st) b.title = st.signedIn ? `Login: signed in as ${st.name}` : `Login: not signed in. ${pressWord()} to sign in or create an account.`;
      } catch (e) { /* the login app is away */ }
    };
    refresh();
    setInterval(refresh, 60000);
  }
  if (!window.FETCHER_MODE) drawBoundTrays();
  // The rail's foot: the signed-in profile (its initial); a click signs out to the login page, where
  // another is picked or made. A finger's tap asks first (the user, 2026-11-10: on touch screens a tap on the profile
  // initial asks before signing out): a finger brushing the rail's foot while scrolling it signed out at once. A
  // mouse's click and the keyboard's Enter sign out as they did.
  let profileName = '';
  fetch('/api/profiles').then((r) => r.json()).then(({ profiles = [], current }) => {
    const me = profiles.find((p) => p.id === current);
    if (!me) return;
    profileName = me.name;
    $('railProfile').textContent = me.name.slice(0, 1).toUpperCase();
    $('railProfile').title = `Profile: ${me.name}. ${pressWord()} to switch to another profile.`;
  }).catch(() => {});
  $('railProfile').addEventListener('click', async () => {
    const by = window.LiminalTouch?.lastInput;
    if ((by === 'touch' || by === 'pen')
      && !window.confirm(`Sign out${profileName ? ` of ${profileName}` : ''} and go to the sign-in page to pick another profile?`)) return;
    try {
      // markups not saved yet go first, as when the page closes: signed out, they went as the first
      // profile's and were refused (no such layout), lost
      if (layout.dirty && layout.current) await saveLayout({ leaving: true }).catch(() => {});
      await fetch('/login/api/sign-out', { method: 'POST', headers: WRITE_HEADERS, body: '{}' }).catch(() => {});  // the login app's session too
      await fetch('/api/profiles/sign-out', { method: 'POST', headers: WRITE_HEADERS, body: '{}' });
    } finally { location.replace('/'); }  // the login page
  });
  // The status line spans the open view's own area, in every view and at every size: not under the
  // rail on its right (a scrollbar widens the rail on a short window), nor under the chart's drawing
  // tools on its left (a strip at the view's left edge: it starts where the chart does), nor under
  // the chart's side panel on its right (an aside at the view's right edge: it ends where the chart does).
  function placeStatus() {
    const view = document.querySelector('main > .view:not([hidden])');
    if (!view) return;
    const v = view.getBoundingClientRect();
    const strip = [...view.children].find((c) => c.tagName === 'NAV' && c.offsetWidth && c.getBoundingClientRect().left - v.left < 2);
    const first = strip && [...view.children].find((c) => c !== strip && c.offsetWidth);
    const left = first ? first.getBoundingClientRect().left : v.left;
    const side = [...view.children].find((c) => c.tagName === 'ASIDE' && c.offsetWidth && v.right - c.getBoundingClientRect().right < 2);
    let last = side?.previousElementSibling;  // the chart, past the panel's resizer
    while (last && (last.getAttribute('role') === 'separator' || !last.offsetWidth)) last = last.previousElementSibling;
    const right = last ? last.getBoundingClientRect().right : v.right;
    $('status').style.marginLeft = `${Math.round(left)}px`;
    $('status').style.marginRight = `${Math.round(document.documentElement.clientWidth - right)}px`;
  }
  // a view shown or hidden, the window, rail or side panel resized, the panel shown or hidden
  const statusPlace = new ResizeObserver(placeStatus);
  for (const el of [$('rail'), $('tools'), $('sidePanel'), ...document.querySelectorAll('main > .view')]) if (el) statusPlace.observe(el);
  window.addEventListener('liminal:view', placeStatus);
  window.addEventListener('liminal:activity', (e) => renderRailIcons(e.detail));
  window.addEventListener('liminal:symbol', () => renderRailIcons(window.Activity?.data));
  window.addEventListener('liminal:run-finished', () => {
    // New data may have arrived: refresh symbols, the panel and the chart's newest bars.
    loadSymbols().then(renderSymbolButton).catch(() => {});
    if (typeof state.pollNow === 'function') state.pollNow();
    loadInfo();
  });
  window.addEventListener('liminal:symbol-updated', (e) => {
    if (e.detail === state.ticker) chart.resetData();  // filled gaps are anywhere, not only after the newest bar
  });
  window.addEventListener('keydown', (e) => {  // Escape returns to the chart from the other views (not one that closed a menu: menu.js's)
    if (e.key === 'Escape' && !e.defaultPrevented && document.body.dataset.view !== 'chart' && !document.querySelector('dialog[open]')) showPane('stock', { toggle: false });
  });

  // -- cursor statistics ---------------------------------------------------------------
  // The bar under the crosshair (or the newest bar) and the high/low of what is on screen:
  // shown in a box in the price window's upper right and in the Analysis panel.
  const box = document.createElement('div');
  box.id = 'cursorBox';
  box.className = 'cursor-box';
  document.getElementById('chart').append(box);
  let cursorIndex = null;

  function visibleHighLow(list) {
    const { from, to } = chart.getVisibleRange();
    let hi = null, lo = null;
    for (let i = Math.max(0, from); i < Math.min(list.length, to); i++) {
      const b = list[i];
      if (!hi || b.high > hi.high) hi = b;
      if (!lo || b.low < lo.low) lo = b;
    }
    return { hi, lo };
  }

  function cursorStats() {
    const list = chart.getDataList();
    if (!list.length) return null;
    const i = cursorIndex != null && cursorIndex >= 0 && cursorIndex < list.length ? cursorIndex : list.length - 1;
    let b = list[i];
    // a wide bar (a minute drawn across its slots, app.js drawWide): the minute's prices from its slot,
    // its volume from its middle slot, and its change from the bar before its first
    let k = i;
    if (b.wide) {
      const key = Math.floor(b.timestamp / b.wide);
      const same = (x) => x?.wide && Math.floor(x.timestamp / b.wide) === key;
      let z = i;
      while (k > 0 && same(list[k - 1])) k--;
      while (z + 1 < list.length && same(list[z + 1])) z++;
      const full = list.slice(k, z + 1).reduce((m, x) => (x.volume > m.volume ? x : m), list[k]);
      b = { ...b, open: b.wo, high: b.wh, low: b.wl, close: b.wc, volume: full.volume, turnover: full.turnover,
            trades: full.trades };
    }
    const prev = k > 0 ? list[k - 1] : null;
    const change = prev ? b.close - prev.close : b.close - b.open;
    const base = prev ? prev.close : b.open;
    const next = i + 1 < list.length ? list[i + 1].timestamp : b.timestamp + 60000;
    return { bar: b, change, changePct: base ? (change / base) * 100 : null, range: b.high - b.low,
             vwap: b.volume ? (b.turnover || 0) / b.volume : null, ...visibleHighLow(list), live: cursorIndex == null,
             feeds: feedsIn(b.timestamp, next) };
  }

  // Which feed(s) the bar came from: the coverage spans (one feed per span) overlapping it.
  function feedsIn(a, b) {
    const att = state.meta?.attribution || {};
    const daily = ['day', 'week', 'month', 'quarter', 'year'].includes(state.period?.type) && att.day?.length;
    const spans = (daily ? att.day : att[state.meta?.base]) || [];
    return [...new Set(spans.filter(([s, e]) => s < b && e > a).map((x) => x[2]))];
  }

  const t = (ms) => fmtTime(ms);
  // a bar's time: a wide bar's is its minute's (its slots are the seconds it is drawn across)
  const barTime = (b) => (b.wide ? Math.floor(b.timestamp / b.wide) * b.wide : b.timestamp);
  const wideTag = (b) => (b.wide ? ` <span class="derived-tag" title="No ${state.period?.span || 1}-${state.period?.type || 'second'} data here: `
    + `the ${b.wide % 60000 ? `${b.wide / 1000}-second` : `${b.wide / 60000}-minute`} bar, drawn as wide as its time">`
    + `${b.wide % 60000 ? `${b.wide / 1000} s` : `${b.wide / 60000} min`} bar</span>` : '');
  const isPrice = () => state.model.kind === 'price';
  const e = window.LiminalIcons.esc;
  // What the bar's tags mean (the box's derived*, interpolated* ...), under the Analysis panel's bar on a touch screen:
  // their tooltips are a mouse's, and the box on the chart takes no pointer at all (the user, 2026-11-10: what a tag
  // means becomes visible text on touch screens)
  function tagNote(b) {
    const words = [];
    const wide = b.wide % 60000 ? `${b.wide / 1000}-second` : `${b.wide / 60000}-minute`;
    if (b.wide) words.push(`${b.wide % 60000 ? `${b.wide / 1000} s` : `${b.wide / 60000} min`} bar: no ${state.period?.span || 1}-${state.period?.type || 'second'} data here, the ${wide} bar drawn as wide as its time`);
    if (b.interp) words.push("interpolated*: no data at this interval here; a line between the 1-minute bars' volume-weighted prices");
    else if (b.hist) words.push('monthly avg*: monthly history from a dataset (the chip beside the interval says which, and how it agrees with the feed), a monthly average of daily closes, flat at its value');
    else if (b.derived) words.push('derived*: built here from finer data; no feed sent this candle at this interval');
    if (b.q1 != null && state.approximate) words.push(`Q1*, Med*, Q3*: ${state.approximate}`);
    return words.length ? `<div class="p-muted small touch-only cursor-tags">${words.map((w) => `<div>${e(w)}</div>`).join('')}</div>` : '';
  }
  // The box sits in the price window's upper right corner, below the roll labels of continuous futures.
  function renderCursor() {
    const s = cursorStats();
    const pane = chart.getSize('candle_pane', 'main');
    if (!s) { box.hidden = true; $('pCursor').innerHTML = ''; return; }
    const b = s.bar;
    const chg = `<span class="${cls(s.change)}">${signedPx(s.change)}${isPrice() && s.changePct != null ? ` (${pct(s.changePct)})` : ''}</span>`;
    if (pane) {
      box.hidden = false;
      box.style.top = `${pane.top + (state.meta?.rolls?.length ? 26 : 6)}px`;
      box.style.right = `${(chart.getSize('candle_pane', 'yAxis')?.width || 92) + 6}px`;
      box.innerHTML = `
        <div><span class="p-muted">Max</span> ${priceText(s.hi?.high)} <span class="p-muted">${s.hi ? e(t(barTime(s.hi))) : ''}</span>
          · <span class="p-muted">Min</span> ${priceText(s.lo?.low)} <span class="p-muted">${s.lo ? e(t(barTime(s.lo))) : ''}</span></div>
        <div><span class="p-muted">${s.live ? 'Last' : e(t(barTime(b)))}</span>${wideTag(b)}${b.interp
          ? ' <span class="derived-tag interp" title="Interpolated: no data at this interval here; a line between the 1-minute bars\' volume-weighted prices">interpolated*</span>'
          : b.hist ? ' <span class="derived-tag hist" title="Monthly history from a dataset (the chip beside the interval says which, and how it agrees with the feed): a monthly average of daily closes, flat at its value">monthly avg*</span>'
          : b.derived ? ' <span class="derived-tag" title="Derived: built here from finer data; no feed sent this candle at this interval">derived*</span>' : ''}
          O ${priceText(b.open)} H ${priceText(b.high)} L ${priceText(b.low)} C ${priceText(b.close)} ${chg}
          ${b.q1 != null ? `· <span class="p-muted" title="${e(state.approximate || '')}">Q1*</span> ${priceText(b.q1)} <span class="p-muted">Med*</span> ${priceText(b.med)} <span class="p-muted">Q3*</span> ${priceText(b.q3)} ` : ''}· <span class="p-muted">Rng</span> ${priceText(s.range)} · <span class="p-muted">Vol</span> ${big(b.volume)}${s.vwap && isPrice() ? ` · <span class="p-muted">VWAP</span> ${priceText(s.vwap)}` : ''}${s.feeds.length ? ` · <span class="p-muted">Src</span> ${e(s.feeds.join('+'))}` : ''}</div>`;
    } else {
      box.hidden = true;
    }
    if (document.body.dataset.pane === 'analysis') {
      $('pCursor').innerHTML = `
        <h3>${s.live ? 'Last bar' : 'Bar under cursor'}</h3>
        ${kv([
          ['Time', `${e(t(barTime(b)))}${wideTag(b)}`],
          ['Open', priceText(b.open)], ['High', priceText(b.high)], ['Low', priceText(b.low)], ['Close', priceText(b.close)],
          ['Change', chg], ['Range', priceText(s.range)], ['Volume', big(b.volume)],
          isPrice() && s.vwap != null && ['VWAP', priceText(s.vwap)],
          ['Trades', big(b.trades)],
          s.feeds.length && ['Source', e(s.feeds.join(' + '))],
        ])}
        <div class="p-sub">On screen</div>
        ${kv([
          ['Max', `${priceText(s.hi?.high)} <small>${s.hi ? e(t(barTime(s.hi))) : ''}</small>`],
          ['Min', `${priceText(s.lo?.low)} <small>${s.lo ? e(t(barTime(s.lo))) : ''}</small>`],
          ['Max − min', priceText(s.hi && s.lo ? s.hi.high - s.lo.low : null)],
        ])}${tagNote(b)}`;
    }
  }
  window.renderCursor = renderCursor;

  let cursorFrame = 0;
  function scheduleCursor() {
    if (cursorFrame) return;
    cursorFrame = requestAnimationFrame(() => { cursorFrame = 0; renderCursor(); });
  }
  chart.subscribeAction('onCrosshairChange', (c) => {
    cursorIndex = crosshairBar(c)?.index ?? null;  // the bar under the cursor (app.js)
    scheduleCursor();
  });
  chart.subscribeAction('onVisibleRangeChange', scheduleCursor);
  document.getElementById('chart').addEventListener('mouseleave', () => { cursorIndex = null; scheduleCursor(); });
  // A finger's crosshair (the library's long press) stays when the finger lifts, and the library takes it away again on
  // a tap elsewhere on the chart without a word (it says onCrosshairChange only of a crosshair shown): the box and the
  // Analysis panel then go back to the newest bar, as when a mouse leaves the chart. Its crosshair still shown (a tap
  // outside the chart leaves it), they keep its bar.
  function crossGone() {
    let c = null;
    try { c = chart.getChartStore?.()?.getCrosshair?.(); } catch (err) { return false; }
    return !!c && !(c.paneId != null && Number.isFinite(c.x));  // not known (another version of the library): as it is
  }
  const afterFinger = (ev) => {
    if (ev.pointerType !== 'touch' && ev.pointerType !== 'pen') return;
    setTimeout(() => { if (cursorIndex != null && crossGone()) { cursorIndex = null; scheduleCursor(); } }, 80);
  };
  for (const type of ['pointerdown', 'pointerup', 'pointercancel']) {
    document.getElementById('chart').addEventListener(type, afterFinger, { passive: true });
  }

  // -- start --------------------------------------------------------------------------
  renderRailIcons(null);
  drawViewsOff();
  // The view a link names (/#markets: signing in opens the globe, login.js), else the one you were on.
  const linked = location.hash.slice(1);
  if (linked) history.replaceState(null, '', location.pathname + location.search);
  showPane(Object.hasOwn(VIEWS, linked) ? linked : prefs.get('pane', 'markets'), { toggle: false });
})();
