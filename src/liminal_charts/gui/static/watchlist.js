// Watchlist (right rail, beside the chart): quick access to symbols, as TradingView's. Only your
// watchlists, nothing else (the user, 2026-10-31: "those are only watchlists no other kinds of lists"):
// each keeps the symbols you add in your order (saved in the database, /api/watchlists, each profile
// its own), with its exchange, last price, change and volume. A profile without one starts with an
// empty "Watchlist", saved with its first symbol. A click opens the symbol on the chart.
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const { symbolIcon, esc } = window.LiminalIcons;
  const FIRST = 'Watchlist';  // a profile's list before it has one of its own
  let lists = [];  // [{ name, tickers: [] }]
  let current = prefs.get('watchlist', FIRST);
  let query = '';
  let sort = prefs.get('watchSort', { key: 'symbol', dir: 1 });
  // The list as a table or as a heat map (the user, 2026-10-28: "heat maps for all the exchanges ... as
  // well as watchlist(s) so when i open the symbol picker or watchlist picker, its easier to figure").
  let view = prefs.get('watchView', 'list');
  const MAP_EQUAL = 400;  // a list up to this many: equal tiles; longer: by dollar volume
  const MAP_REDRAW_MS = 15000;  // the map drawn again with the activity polls at most this often
  let mapDrawn = 0;
  // Touch screens and the on-screen keyboard (the user, 2026-11-10: "I want to support touch as well as mouse clicking
  // on the interface"; "Support for virtual keyboard on tablets and phones"): each field's keyboard (the library's
  // touch.js), and the add box focused by the page only for a mouse or a keyboard (a finger's keyboard comes up when
  // the finger taps the box).
  const kb = (kind, more) => window.LiminalTouch?.keyboard(kind, more) || '';
  const focusBox = (el) => (window.LiminalTouch ? window.LiminalTouch.autofocus(el) : el.focus());

  const shown = () => document.body.dataset.pane === 'watchlist';

  // The list shown: the one picked, or the first; with none, an empty FIRST (saved with its first symbol).
  function shownList() {
    if (!lists.length) lists.push({ name: FIRST, tickers: [] });
    const mine = lists.find((l) => l.name === current) || lists[0];
    current = mine.name;
    return mine;
  }

  async function load() {
    try {
      lists = (await api('/api/watchlists')).lists || [];
    } catch { lists = []; }
    shownList();
    render();
  }

  // One save at a time, each sending the lists as they are when it goes: two at once could reach the
  // server in either order, the older undoing the newer. The server's copy is taken only from the last
  // save waiting (an earlier answer lacks the changes still to go).
  // Each save draws the panel again once nothing presses on it (panels.js Views): a row gone moves the rows below it
  // up, and under a finger the next symbol's − would take its tap. What waits on that drawing (`after`: the add box's
  // focus back) runs after it, whichever redraw comes first.
  let saving = Promise.resolve();
  let waiting = 0;
  let afterDraw = null;
  function draw() {
    render();
    const waited = afterDraw;
    afterDraw = null;
    if (waited) waited();
  }
  function save(after) {
    waiting += 1;
    saving = saving.then(async () => {
      try {
        const res = await apiWrite('PUT', '/api/watchlists', { lists });
        if (waiting === 1) lists = res.lists;
      } catch (err) { showError(err); } finally { waiting -= 1; }
      if (after) afterDraw = after;
      window.Views.redraw($('pWatch'), draw);
    });
    return saving;
  }

  // Live price where the symbol is streaming; otherwise the last session's close and change.
  function quote(sym) {
    const close = sym.market?.close ?? null, pct = sym.market?.changePct ?? sym.changePct ?? null;
    let live = null;
    for (const s of Object.values(window.Activity?.data?.liveFeeds?.streams || {})) {
      const p = s.pairs?.[sym.ticker];
      if (s.connected && p?.price != null && (live == null || p.last > live.last)) live = p;
    }
    const isLiveNow = (window.Activity?.data?.liveFeeds?.liveTickers || []).includes(sym.ticker);
    const vol = sym.market?.volume ?? null;  // the last session's (shares)
    if (isLiveNow && live) {
      const prev = close != null && pct != null ? close / (1 + pct / 100) : null;
      return { last: live.price, pct: prev ? (live.price / prev - 1) * 100 : null, vol, live: true };
    }
    return { last: close, pct, vol, live: false };
  }

  const fmtPrice = (v) => (v == null ? '–' : Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 2, maximumFractionDigits: Math.abs(v) < 1 ? 6 : Math.abs(v) < 100 ? 4 : 2 }));
  const fmtPct = (v) => (v == null ? '' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`);
  // the last session's volume, as TradingView's watchlist shows it (the user, 2026-10-31: "lets add volume to the watchlist")
  const fmtVol = (v) => (v == null ? '' : v >= 1e9 ? `${(v / 1e9).toFixed(2)}B` : v >= 1e6 ? `${(v / 1e6).toFixed(2)}M`
    : v >= 1e3 ? `${(v / 1e3).toFixed(1)}K` : String(Math.round(v)));

  function compare(a, b) {
    const k = sort.key;
    const va = k === 'symbol' ? a.sym.ticker : k === 'last' ? a.q.last : k === 'vol' ? a.q.vol : a.q.pct;
    const vb = k === 'symbol' ? b.sym.ticker : k === 'last' ? b.q.last : k === 'vol' ? b.q.vol : b.q.pct;
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    return (typeof va === 'string' ? va.localeCompare(vb) : va - vb) * sort.dir;
  }

  // A row: − at its left, shown while the pointer is on the row (the user, 2026-10-31: "lets keep the
  // minuses under the arrow", "Hidden until hover"), the symbol, its last price, change and volume. On a touch
  // screen the − is always shown, a finger's size (the user, 2026-11-10; views-touch.css).
  function row(entry) {
    const { sym, q } = entry;
    const on = sym.ticker === state.ticker;
    return `<tr class="${on ? 'on' : ''}" data-ticker="${esc(sym.ticker)}" title="${esc(sym.name || sym.ticker)}${sym.exchangeName ? ` · ${esc(sym.exchangeName)}` : ''}">
      <td class="w-x"><button type="button" data-remove="${esc(sym.ticker)}" title="Remove ${esc(sym.ticker)} from this list" aria-label="Remove ${esc(sym.ticker)}">−</button></td>
      <td class="w-sym">${symbolIcon(sym, 18)}<span><b>${esc(sym.ticker)}</b>${q.live ? '<span class="w-live" title="Streaming live"></span>' : ''}
        <span class="w-ex">${esc(sym.exchange || sym.asset || '')}</span></span></td>
      <td class="num">${fmtPrice(q.last)}</td>
      <td class="num ${q.pct == null ? '' : q.pct >= 0 ? 'up' : 'down'}">${fmtPct(q.pct)}</td>
      <td class="num w-vol" title="The last session's volume">${fmtVol(q.vol)}</td>
    </tr>`;
  }

  // The map of a list: stocks by sector and industry, the other classes in groups of their own (futures
  // by family and type), each tile coloured by its move (live where it streams, as the table's).
  function mapGroup(sym) {
    const M = window.MarketMap;
    if (sym.asset === 'stock') return [sym.sector || 'Not classified yet', sym.industry || ''];
    if (sym.asset === 'future') return [`Futures · ${M.futuresFamily(sym.futuresType)}`, M.futuresTypeName(sym.futuresType)];
    return [{ index: 'Indices', fx: 'Currencies', crypto: 'Crypto' }[sym.asset] || 'Other', ''];
  }

  function renderMap(box, head, syms, empty) {
    const M = window.MarketMap;
    const body = frame(box, head);  // the head as it stands (its fields kept), the map below it
    if (!syms.length) {  // no blank map: why it is empty, and the market's map to pick from
      body.innerHTML = `<div class="w-map-empty p-muted">${esc(empty)}
        <button type="button" id="watchPick" title="The symbol picker's map: every Nasdaq and NYSE company by exchange and sector">Pick from the market map</button></div>`;
      mapDrawn = Date.now();
      return;
    }
    const items = syms.map((sym) => {
      const q = quote(sym), [group, kind] = mapGroup(sym);
      return { ticker: sym.ticker, name: sym.name, icon: sym.icon, asset: sym.asset, sector: group, industry: kind,
        marketCap: sym.marketCap, dollarVolume: sym.market?.dollarVolume ?? null, changePct: q.pct, close: q.last };
    });
    const volumes = items.map((s) => s.dollarVolume).filter((v) => v > 0).sort((a, b) => a - b);
    const median = volumes.length ? volumes[Math.floor(volumes.length / 2)] : 1;
    const size = items.length <= MAP_EQUAL ? () => 1 : (s) => (s.asset === 'stock' ? s.dollarVolume || 0 : median);
    body.innerHTML = `<div id="watchMap" class="market-map watch-map" role="listbox" aria-label="Watchlist map"></div>
      <div class="map-foot"><span class="map-legend">${[-3, -1, 0, 1, 3].map((p) => `<span style="background:${M.changeColor(p)}">${p > 0 ? '+' : ''}${p}%</span>`).join('')}</span>
        <span class="p-muted small">${items.length.toLocaleString()} · ${items.length <= MAP_EQUAL ? 'equal tiles' : 'stocks by dollar volume'} · by sector</span></div>`;
    const el = $('watchMap');
    if (el.clientWidth) M.render(el, items, { current: state.ticker, levels: [(s) => s.sector, (s) => s.industry], size });
    mapDrawn = Date.now();
  }

  // The panel's head (the list picker, Map or List, +, 🗑, the add box and the filter) is drawn when what it holds
  // changes (the view, the add box opened or closed), else brought up to date in place: its fields are never
  // replaced, so the filter as it is typed draws only what it filters, an on-screen keyboard is not closed nor its
  // word cut. What follows it (the table or the map) is the part frame() returns, drawn as the list changes.
  let shownHead = null;  // what the head was drawn for (the view, the add box), null: drawn again
  let headOptions = '', optionsDrawn = '';  // the list picker's options, as wanted and as drawn
  let shownTable = null;  // the table's head (its sort) as drawn
  function frame(box, head) {
    const key = `${view}|${adding}`;
    let body = box.querySelector(':scope > .w-body');
    const mine = shownList();
    if (shownHead !== key || !body || !$('watchFilter')) {
      box.innerHTML = `${head}<div class="w-body"></div>`;
      shownHead = key;
      optionsDrawn = headOptions;
      body = box.querySelector(':scope > .w-body');
      if (adding) openAdd(false);
    } else {
      if (optionsDrawn !== headOptions) { $('watchList').innerHTML = headOptions; optionsDrawn = headOptions; }
      if ($('watchAdd')) $('watchAdd').title = `Add a symbol to ${mine.name}`;
      if (adding) fillAdd();  // the hits marked again (✓ once on the list); the box and what is typed in it as they are
    }
    $('watchList').value = mine.name;
    return body;
  }

  // The rows as they stand brought up to date, each cell's contents and classes where they changed, the row itself
  // kept: a tap on it lands, a finger's fling over the list goes on. False when the rows are not the same symbols in
  // the same order (the body is then drawn again).
  function updateRows(table, entries) {
    const rows = [...table.querySelectorAll('tbody tr[data-ticker]')];
    if (!entries.length || rows.length !== entries.length || rows.some((r, i) => r.dataset.ticker !== entries[i].sym.ticker)) return false;
    const tpl = document.createElement('template');
    tpl.innerHTML = `<table><tbody>${entries.map(row).join('')}</tbody></table>`;
    const fresh = tpl.content.querySelectorAll('tbody tr');
    rows.forEach((r, i) => {
      const n = fresh[i];
      if (!n) return;
      for (const a of ['class', 'title']) if (r.getAttribute(a) !== n.getAttribute(a)) r.setAttribute(a, n.getAttribute(a) || '');
      [...n.cells].forEach((c, j) => {
        const d = r.cells[j];
        if (!d) return;
        if (d.className !== c.className) d.className = c.className;
        if (d.innerHTML !== c.innerHTML) d.innerHTML = c.innerHTML;
      });
    });
    return true;
  }

  // Adding and removing, TradingView's way (the user, 2026-10-31: "i need away to add and get rid of
  // watchlist items", "lets use the tradingview way", "a plus at the top and minus next to each entry"):
  // + at the top opens a search of the symbols collected here, a click (or Enter) adds one to the list
  // shown, and the search stays open for the next; − on each row takes it off.
  let adding = false;
  let addQuery = '';

  function addHits() {
    const q = addQuery.trim().toUpperCase();
    const syms = state.symbols || [];
    if (!q) return state.ticker ? syms.filter((s) => s.ticker === state.ticker) : [];  // the open symbol first
    const score = (s) => (s.ticker === q ? 0 : s.ticker.startsWith(q) ? 1 : s.ticker.includes(q) ? 2
      : (s.name || '').toUpperCase().includes(q) ? 3 : 9);
    return syms.map((s) => [score(s), s]).filter(([k]) => k < 9)
      .sort((a, b) => a[0] - b[0] || a[1].ticker.localeCompare(b[1].ticker)).slice(0, 30).map(([, s]) => s);
  }

  let addDrawn = null;  // the add list's hits as drawn
  function fillAdd() {
    const list = document.querySelector('#pWatch .w-addlist');
    if (!list) return;
    const mine = shownList();
    const hits = addHits();
    const html = hits.length ? hits.map((s) => {
      const has = mine.tickers.includes(s.ticker);
      return `<button type="button" class="w-add-hit" data-add="${esc(s.ticker)}"${has ? ' disabled' : ''}
          title="${esc(has ? `${s.ticker} is on ${mine.name}` : `Add ${s.ticker} to ${mine.name}`)}">
        ${symbolIcon(s, 16)}<b>${esc(s.ticker)}</b><span class="w-add-name">${esc(s.name || '')}</span>
        <span class="w-ex">${esc(s.exchange || s.asset || '')}</span><span class="w-add-mark">${has ? '✓' : '+'}</span></button>`;
    }).join('') : `<div class="p-muted small w-add-hint">${addQuery.trim() ? 'No symbol collected here matches.' : 'Type a ticker or a company name.'}</div>`;
    if (list.firstChild && html === addDrawn) return;  // as it was: its hits left as they are (a tap on one lands)
    list.innerHTML = html;
    addDrawn = html;
  }

  function openAdd(focus = true) {
    const input = $('watchAddInput');
    if (!input) return;
    input.value = addQuery;
    fillAdd();
    if (focus) focusBox(input);  // a mouse's or a keyboard's +: ready to type in; a finger taps the box for its keyboard
  }

  function addSymbol(ticker) {
    const mine = shownList();
    if (!mine.tickers.includes(ticker)) mine.tickers.push(ticker);
    prefs.set('watchlist', current);
    // drawn in place, the search still open; a hit clicked took the focus and is drawn again: the box gets it back
    // (a mouse's; a finger's keyboard stays down)
    return save(() => {
      const input = $('watchAddInput');
      if (adding && input && (!document.activeElement || document.activeElement === document.body)) focusBox(input);
    });
  }

  const addHead = (mine) => `<button type="button" id="watchAdd" aria-expanded="${adding}"
      title="${esc(`Add a symbol to ${mine.name}`)}">+</button>`;
  const addBox = () => (adding ? `<div class="w-addbox"><input id="watchAddInput" type="search" placeholder="Add a symbol: ticker or company"
      aria-label="Add a symbol" ${kb('ticker')}><div class="w-addlist" role="listbox"></div></div>` : '');
  const filterBox = () => `<input id="watchFilter" type="search" placeholder="Filter symbols" aria-label="Filter symbols" value="${esc(query)}" ${kb('search')}>`;

  function render() {
    if (!shown()) return;
    const box = $('pWatch');
    const bySym = new Map((state.symbols || []).map((s) => [s.ticker, s]));
    const q = query.trim().toUpperCase();
    const match = (s) => !q || s.ticker.includes(q) || (s.name || '').toUpperCase().includes(q) || (s.exchange || '').includes(q);
    const mine = shownList();
    const th = (key, label, cls = '') => `<th class="${cls}" data-sort="${key}">${label}${sort.key === key ? (sort.dir > 0 ? ' ▲' : ' ▼') : ''}</th>`;
    const options = [...lists.map((l) => `<option value="${esc(l.name)}">${esc(l.name)} (${l.tickers.length})</option>`),
      '<option value="__new__">New list…</option>'].join('');
    headOptions = options;
    const toggle = `<button type="button" id="watchView" title="${view === 'map' ? 'Show the list as a table' : 'Show the list as a heat map, by sector'}"
      aria-pressed="${view === 'map'}">${view === 'map' ? 'List' : 'Map'}</button>`;
    const empty = !mine.tickers.length ? 'Empty: press + to add a symbol.'
      : state.symbolsLoaded ? 'Nothing matches.' : 'Loading the symbols…';
    if (view === 'map') {
      const syms = mine.tickers.map((t) => bySym.get(t)).filter(Boolean);
      const head = `<h3 class="w-head">Watchlist <select id="watchList" aria-label="Watchlist">${options}</select>${toggle}
          ${addHead(mine)}</h3>${addBox()}
        ${filterBox()}`;
      // an empty list (the user, 2026-10-29: "even empty watch list"): said as the table says it
      renderMap(box, head, syms.filter(match), empty);
      return;
    }
    const entries = mine.tickers.map((t) => bySym.get(t)).filter((s) => s && match(s)).map((sym) => ({ sym, q: quote(sym) }));
    if (sort.key !== 'order') entries.sort(compare);
    const body = entries.length ? `<tbody>${entries.map(row).join('')}</tbody>`
      : `<tbody><tr><td colspan="5" class="p-muted w-empty">${empty}</td></tr></tbody>`;
    const head = `<h3 class="w-head">Watchlist
        <select id="watchList" aria-label="Watchlist">${options}</select>${toggle}
        ${addHead(mine)}<button type="button" id="watchDelete" title="Delete this list">🗑</button></h3>${addBox()}
      ${filterBox()}`;
    const thead = `<thead><tr><th class="w-x"></th>${th('symbol', 'Symbol')}${th('last', 'Last', 'num')}${th('pct', 'Chg%', 'num')}${th('vol', 'Vol', 'num')}</tr></thead>`;
    const host = frame(box, head);
    const table = host.querySelector('.w-table');
    if (!table || shownTable !== thead) {  // back from the map, or sorted otherwise: the table drawn whole
      host.innerHTML = `<div class="table-wrap w-wrap"><table class="w-table">${thead}${body}</table></div>`;
      shownTable = thead;
      return;
    }
    if (updateRows(table, entries)) return;  // the same rows: their prices, changes and live dots in place
    const wrap = host.querySelector('.w-wrap'), top = wrap?.scrollTop;  // rows came or went: the body drawn again
    table.querySelectorAll('tbody').forEach((t) => t.remove());
    table.insertAdjacentHTML('beforeend', body);
    if (wrap && wrap.scrollTop !== top) wrap.scrollTop = top;
  }

  // A light refresh of prices: the rows in place, never under a finger (a tap on one lands) nor while a finger's
  // scroll moves (panels.js Views), the filter and the add box left as they are.
  function refresh() {
    if (!shown()) return;
    if (document.activeElement?.id === 'watchAddInput') return;  // searching to add: the prices wait
    if (view === 'map' && Date.now() - mapDrawn < MAP_REDRAW_MS && document.activeElement?.id !== 'watchFilter') return;
    window.Views.redraw($('pWatch'), draw);
  }

  $('pWatch').addEventListener('input', (e) => {
    if (e.target.id === 'watchAddInput') { addQuery = e.target.value; fillAdd(); return; }
    if (e.target.id !== 'watchFilter') return;
    query = e.target.value;
    refresh();
  });
  $('pWatch').addEventListener('change', async (e) => {
    if (e.target.id !== 'watchList') return;
    if (e.target.value === '__new__') {
      const name = (window.prompt('Name of the new list') || '').trim().slice(0, 40);
      if (name && !lists.some((l) => l.name === name)) {
        lists.push({ name, tickers: [] });
        current = name;
        prefs.set('watchlist', current);
        await save();
        return;
      }
      shownHead = null;  // cancelled, or a name in use: drawn whole, the picker back on the list shown
    } else {
      current = e.target.value;
      prefs.set('watchlist', current);
    }
    render();
  });
  $('pWatch').addEventListener('click', async (e) => {
    if (e.target.closest('#watchView')) {
      view = view === 'map' ? 'list' : 'map';
      prefs.set('watchView', view);
      return render();
    }
    const tile = e.target.closest('.map-tile[data-ticker]');
    if (tile) { window.dispatchEvent(new CustomEvent('liminal:open-symbol', { detail: tile.dataset.ticker })); return; }
    if (e.target.closest('#watchPick')) {  // the picker on Nasdaq & NYSE's map (app.js)
      state.switcherScope = 'stocks';
      state.switcherView = 'map';
      prefs.set('switcherScope', 'stocks');
      prefs.set('switcherView', 'map');
      return $('symbolBtn').click();
    }
    const sortBy = e.target.closest('th[data-sort]');
    if (sortBy) {
      sort = { key: sortBy.dataset.sort, dir: sort.key === sortBy.dataset.sort ? -sort.dir : 1 };
      prefs.set('watchSort', sort);
      return render();
    }
    const mine = shownList();
    if (e.target.closest('#watchAdd')) {
      adding = !adding;
      addQuery = '';
      shownHead = null;
      render();
      if (adding) openAdd();
      return;
    }
    const hit = e.target.closest('button[data-add]');
    if (hit) return addSymbol(hit.dataset.add);
    if (e.target.closest('#watchDelete') && window.confirm(`Delete the list "${mine.name}"?`)) {
      lists = lists.filter((l) => l !== mine);
      shownList();  // the first left, or an empty Watchlist
      prefs.set('watchlist', current);
      return save();
    }
    const remove = e.target.closest('button[data-remove]');
    if (remove) {
      mine.tickers = mine.tickers.filter((t) => t !== remove.dataset.remove);
      return save();
    }
    const item = e.target.closest('tr[data-ticker]');
    if (item) window.dispatchEvent(new CustomEvent('liminal:open-symbol', { detail: item.dataset.ticker }));
  });

  $('pWatch').addEventListener('keydown', (e) => {
    if (e.target.id === 'watchFilter') {  // an on-screen keyboard's Search: put away, so the rows it found show
      if (e.key === 'Enter' && window.Views.softKeyboard()) e.target.blur();
      return;
    }
    if (e.target.id !== 'watchAddInput') return;
    if (e.key === 'Escape') {
      e.stopPropagation();
      adding = false;
      shownHead = null;
      render();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const first = document.querySelector('#pWatch .w-add-hit:not([disabled])');
      if (first) addSymbol(first.dataset.add);
    }
  });

  window.addEventListener('liminal:view', (e) => { if (e.detail?.pane === 'watchlist') render(); });
  window.addEventListener('liminal:symbols', refresh);  // the symbols arrived: the list's rows
  window.addEventListener('liminal:symbol', refresh);
  window.addEventListener('liminal:activity', refresh);  // live prices with every activity poll
  window.Watchlist = { render, load, lists: () => lists };  // the lists: the symbol picker maps them too
  load();
})();
