'use strict';
/* Trading (the rail; docs/ARCHITECTURE.md, "Trading"): a chart view of its own (interval, chart
   type, indicators and drawings, like Stock and Analysis), with the broker's account beside it in
   the side panel: the brokers, each a plugin; the account, an order ticket, positions and orders.
   On the chart: the paper position's average cost and each working order's price, for the symbol
   shown. Paper is simulated here: orders fill from the stored bars by the backtests' rules
   (trading/paper.py), matched each time this view asks. Interactive Brokers is planned; live
   trading is off, so nothing here reaches a real account. */
(() => {
  const esc = window.LiminalIcons.esc;
  const POLL_MS = 5000;
  const T = window.Trading = { data: null, timer: null };
  const box = $('pTrading');
  const shown = () => document.body.dataset.pane === 'trading' && document.body.dataset.view === 'chart';
  const money = (v) => (v == null ? '–' : v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const signed = (v) => (v == null ? '–' : `${v >= 0 ? '+' : ''}${money(v)}`);
  const px = (v) => (v == null ? '–' : String(Number(Number(v).toPrecision(8))));
  const when = (t) => (t == null ? '' : esc(fmtTime(t)));
  let typedSymbol = false;  // the ticket follows the chart's symbol until another is typed
  // Each field's keyboard on a phone or a tablet (the user, 2026-11-10: "Support for virtual keyboard on tablets and
  // phones"): a ticker's capitals, a quantity's or a price's digits and point, the Enter key labelled Next or Done
  // (touch.js; none without it). A quantity may be a fraction of a share. The quantity's key says Done whatever the
  // type: Chrome on Android moves a Next key's focus itself, to the next control in the markup (the type's select),
  // never to the limit or the stop; a Done key sends Enter, which moves on to them here as a hardware keyboard's does.
  const kb = (kind, more) => (window.LiminalTouch ? window.LiminalTouch.keyboard(kind, more) : '');

  box.innerHTML = `
    <h3>Trading <button id="trReset" title="Start a new paper account (the old one's record is kept)">New account…</button></h3>
    <form id="trNew" class="tr-ticket tr-new" autocomplete="off" hidden>
      <div class="p-muted small">A new paper account: the current one is closed, its record kept.</div>
      <label>Starting cash <input id="trCash" type="number" min="0" step="any" value="100000" required ${kb('decimal', 'next')}></label>
      <label>Fee per trade, basis points of what is traded (0 for none) <input id="trFee" type="number" min="0" step="any"
        value="0" ${kb('decimal', 'go')}></label>
      <div class="tr-new-act"><button type="button" id="trNewCancel">Cancel</button><button type="submit" class="primary">Open the new account</button></div>
    </form>
    <div id="trBrokers" class="tr-brokers" role="tablist" aria-label="Broker"></div>
    <div class="p-muted small">Paper: simulated, filled from the stored bars. Live trading is off: nothing here reaches a real account.</div>
    <div id="trTiles" class="tr-tiles"></div>
    <div class="p-sub">Order <span class="p-muted">paper</span></div>
    <form id="trTicket" class="tr-ticket" autocomplete="off">
      <label>Symbol <input id="trSym" required maxlength="24" ${kb('ticker', 'next')}></label>
      <label>Quantity <input id="trQty" type="number" min="0" step="any" value="100" required ${kb('decimal', 'done')}></label>
      <label>Type <select id="trType"><option value="market">Market</option><option value="limit">Limit</option><option value="stop">Stop</option></select></label>
      <label>Time in force <select id="trTif"><option value="day">Day</option><option value="gtc">Until cancelled</option></select></label>
      <label class="tr-limit" hidden>Limit <input id="trLimit" type="number" min="0" step="any" ${kb('decimal', 'done')}></label>
      <label class="tr-stop" hidden>Stop <input id="trStop" type="number" min="0" step="any" ${kb('decimal', 'done')}></label>
      <div class="tr-sides"><button type="button" class="tr-buy" data-side="buy">Buy</button><button type="button" class="tr-sell" data-side="sell">Sell</button></div>
      <div id="trMsg" class="small"></div>
    </form>
    <div class="p-muted small">Fills: market at the next bar's open; a limit or stop at its price, or the open when a bar opens beyond it; from 1-second bars where held, else minutes; US stocks in regular hours. Day orders expire at the close.</div>
    <div class="p-sub">Positions</div>
    <div class="table-wrap"><table class="act-table tr-table"><thead><tr><th>Symbol</th><th class="num">Qty</th><th class="num">Avg</th><th class="num">Last</th><th class="num">Unrealized</th></tr></thead><tbody id="trPositions"></tbody></table></div>
    <div class="p-sub">Orders</div>
    <div class="table-wrap tr-orders"><table class="act-table tr-table"><thead><tr><th>Placed</th><th>Order</th><th>Status</th><th></th></tr></thead><tbody id="trOrders"></tbody></table></div>`;

  // A part drawn again only when what it shows changed: a poll every few seconds that changes nothing leaves its rows
  // as they are, so a tap on one (its ✕, say) is not lost to a redraw under the finger.
  const shownHtml = new Map();  // an element's id -> the markup last put in it
  function put(id, html) {
    if (shownHtml.get(id) === html) return;
    shownHtml.set(id, html);
    $(id).innerHTML = html;
  }

  function render() {
    const d = T.data;
    if (!d) return;
    put('trBrokers', d.brokers.map((b) => `<button type="button" role="tab" data-broker="${esc(b.key)}"
      class="${b.key === d.broker ? 'active' : ''}" ${b.status !== 'ready' ? 'disabled' : ''} title="${esc(b.status)}">${esc(b.label)}${b.status !== 'ready' ? ' · planned' : ''}</button>`).join(''));
    const a = d.account;
    const tile = (label, value, c = '') => `<div class="tile"><div class="p-muted small">${label}</div><div class="tr-big ${c}">${value}</div></div>`;
    put('trTiles', tile('Equity', money(a.equity)) + tile('Cash', money(a.cash))
      + tile('Unrealized', signed(a.unrealized), cls(a.unrealized)) + tile('Realized, after fees', signed(a.realized), cls(a.realized)));
    // what a mouse reads in a row's title (a position's realized gain, an order's time in force and when it was done),
    // a touch screen shows under it
    put('trPositions', d.positions.length ? d.positions.map((p) => `<tr data-ticker="${esc(p.ticker)}" title="Realized ${signed(p.realized)}">
      <td>${esc(p.ticker)}<div class="p-muted small touch-only">realized ${signed(p.realized)}</div></td><td class="num ${p.qty < 0 ? 'down' : ''}">${p.qty.toLocaleString('en-US')}</td><td class="num">${px(p.avg)}</td>
      <td class="num">${px(p.last)}</td><td class="num ${cls(p.unrealized)}">${signed(p.unrealized)}</td></tr>`).join('')
      : '<tr><td colspan="5" class="p-muted">No positions.</td></tr>');
    put('trOrders', d.orders.length ? d.orders.map((o) => {
      const price = o.type === 'limit' ? ` @ ${px(o.limit_px)}` : o.type === 'stop' ? ` stop ${px(o.stop_px)}` : '';
      const tif = o.tif === 'gtc' ? 'until cancelled' : 'day';
      return `<tr title="${tif}${o.done_at ? ` · ${esc(o.status)} ${esc(fmtTime(o.done_at))}` : ''}">
        <td>${when(o.placed_at)}</td>
        <td><span class="${o.side === 'buy' ? 'up' : 'down'}">${esc(o.side)}</span> ${o.qty.toLocaleString('en-US')} ${esc(o.ticker)} ${esc(o.type)}${price}</td>
        <td class="tr-${esc(o.status)}">${esc(o.status)}${o.fill_px != null ? ` ${px(o.fill_px)}` : ''}<div class="p-muted small touch-only">${tif}${o.done_at ? ` · ${when(o.done_at)}` : ''}</div></td>
        <td>${o.status === 'working' ? `<button data-cancel="${o.id}" title="Cancel this paper order" aria-label="Cancel this paper order">✕</button>` : ''}</td></tr>`;
    }).join('') : '<tr><td colspan="4" class="p-muted">No orders yet.</td></tr>');
  }

  async function load() {
    if (!shown()) return;
    try {
      T.data = await api('/api/trading');
      render();
      const sig = JSON.stringify([T.data.positions.map((x) => [x.ticker, x.qty, x.avg]),
                                  T.data.orders.filter((o) => o.status === 'working').map((o) => o.id)]);
      if (sig !== T.drawn) {  // the chart repaints on its own events only: once more for the new levels
        T.drawn = sig;
        chart.overrideIndicator({ name: 'TRADING_MARKS', extendData: sig });
      }
    } catch (err) { showError(err); }
  }

  function setType() {
    const t = $('trType').value;
    box.querySelector('.tr-limit').hidden = t !== 'limit';
    box.querySelector('.tr-stop').hidden = t !== 'stop';
  }
  $('trType').addEventListener('change', setType);
  $('trSym').addEventListener('input', () => { typedSymbol = true; });

  // Enter in the ticket moves to the next field and never places an order (the user, 2026-11-10): on the last field it
  // puts the keyboard away. Buy and Sell place orders, and only they (the ticket has no submit button, so no Enter in
  // a field submits it): an order is never a key pressed to move on or to close a phone's keyboard.
  const TICKET = ['trSym', 'trQty', 'trLimit', 'trStop'];
  $('trTicket').addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.isComposing || !(e.target instanceof HTMLInputElement)) return;
    e.preventDefault();
    const fields = TICKET.map((id) => $(id)).filter((el) => el && !el.closest('[hidden]'));  // those the type shows
    const next = fields.includes(e.target) ? fields[fields.indexOf(e.target) + 1] : null;
    if (next) next.focus();
    else e.target.blur();
  });
  $('trTicket').addEventListener('submit', (e) => e.preventDefault());  // nothing submits it: Buy and Sell are buttons
  $('trTicket').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-side]');
    if (b) place(b.dataset.side);
  });

  async function place(side) {
    const msg = $('trMsg');
    if (!$('trTicket').reportValidity()) return;  // what a submit checks: a symbol, a quantity of 0 or more
    const sym = $('trSym').value.trim().toUpperCase();
    const body = { ticker: sym, source: sym === state.ticker ? state.source : '*', side,
                   qty: Number($('trQty').value), type: $('trType').value, limit: $('trLimit').value || null,
                   stop: $('trStop').value || null, tif: $('trTif').value };
    try {
      const o = await apiWrite('POST', '/api/trading/orders', body);
      msg.className = 'small up';
      msg.textContent = `Paper order ${o.id}: ${o.side} ${o.qty} ${o.ticker}, ${o.status}${o.fill_px != null ? ` at ${px(o.fill_px)}` : ''}`;
      load();
    } catch (err) {
      msg.className = 'small down';
      msg.textContent = err.message || String(err);
    }
  }

  // A new paper account. A finger's tap (a pen's too) opens a form in the panel for its starting cash and fee, each
  // with a phone's keypad of digits and a point (a browser's prompt only ever gives a phone the letters' keyboard):
  // Enter in the cash moves on to the fee, in the fee it opens the account, as the prompts' OK did; Cancel or Escape
  // puts the form away. No field is focused as it opens: a keyboard comes up when a field is tapped. A mouse and a
  // keyboard are asked in the browser's two prompts, as before.
  const fingered = () => !!window.LiminalTouch && ['touch', 'pen'].includes(window.LiminalTouch.lastInput);
  function openNew() {
    $('trCash').value = '100000';
    $('trFee').value = String(T.data?.account?.feeBps ?? 0);
    $('trNew').hidden = false;
    $('trReset').setAttribute('aria-controls', 'trNew');
    $('trReset').setAttribute('aria-expanded', 'true');
  }
  function closeNew() {
    $('trNew').hidden = true;
    $('trReset').removeAttribute('aria-expanded');
  }
  $('trNew').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeNew(); $('trReset').focus(); return; }
    if (e.key === 'Enter' && !e.isComposing && e.target.id === 'trCash') { e.preventDefault(); $('trFee').focus(); }
  });
  // One account asked for at a time: the prompts it replaced could not be answered twice, a form can (a double tap on
  // Open, Enter pressed twice); until the answer comes, its Open is off and a second submit asks nothing.
  let opening = false;
  $('trNew').addEventListener('submit', async (e) => {
    e.preventDefault();
    if (opening) return;
    opening = true;
    const go = $('trNew').querySelector('button[type="submit"]');
    go.disabled = true;
    const cash = Number($('trCash').value), fee = Number($('trFee').value);
    try {
      await apiWrite('POST', '/api/trading/reset', { cash, feeBps: fee });
      closeNew();
      load();
    } catch (err) { showError(err); } finally {
      opening = false;
      go.disabled = false;
    }
  });

  box.addEventListener('click', async (e) => {
    const c = e.target.closest('button[data-cancel]');
    if (c) {
      try { await apiWrite('DELETE', `/api/trading/orders/${c.dataset.cancel}`); load(); } catch (err) { showError(err); }
      return;
    }
    const row = e.target.closest('tr[data-ticker]');
    if (row && row.dataset.ticker !== state.ticker) selectSymbol(row.dataset.ticker).catch(showError);
    if (e.target.id === 'trNewCancel') { closeNew(); return; }
    if (e.target.id !== 'trReset') return;
    if (!$('trNew').hidden) { closeNew(); return; }  // the form a finger opened: put away, whatever presses New account now
    if (fingered()) { openNew(); return; }
    const cash = prompt('Starting cash for a new paper account (the current one is closed, its record kept):', '100000');
    if (cash == null) return;
    const fee = prompt('Fee per trade, in basis points of what is traded (0 for none):', String(T.data?.account?.feeBps ?? 0));
    if (fee == null) return;
    try { await apiWrite('POST', '/api/trading/reset', { cash: Number(cash), feeBps: Number(fee) }); load(); } catch (err) { showError(err); }
  });

  // -- on the chart: the position's average cost and the working orders, for the symbol shown ----
  kc.registerIndicator({
    name: 'TRADING_MARKS',
    shortName: '',
    series: 'price',
    figures: [],
    calc: (list) => list.map(() => ({})),
    createTooltipDataSource: () => ({ name: '', calcParamsText: '', features: [], legends: [] }),
    draw: ({ ctx, bounding, yAxis }) => {
      const d = T.data;
      if (!d || !shown()) return true;
      const pos = d.positions.find((p) => p.ticker === state.ticker);
      const levels = d.orders.filter((o) => o.status === 'working' && o.ticker === state.ticker && o.type !== 'market')
        .map((o) => ({ price: o.type === 'limit' ? o.limit_px : o.stop_px, color: o.side === 'buy' ? '#26a69a' : '#ef5350',
                       text: `${o.side} ${o.qty} ${o.type}`, dash: [4, 3] }));
      if (pos) levels.push({ price: pos.avg, color: '#f5a623', text: `${pos.qty > 0 ? 'long' : 'short'} ${Math.abs(pos.qty)} @ ${px(pos.avg)}`, dash: [] });
      ctx.save();
      ctx.font = '11px sans-serif';
      for (const lv of levels) {
        const y = yAxis.convertToPixel(lv.price);
        if (!Number.isFinite(y) || y < 0 || y > bounding.height) continue;
        ctx.strokeStyle = lv.color; ctx.lineWidth = 1; ctx.setLineDash(lv.dash);
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(bounding.width, y); ctx.stroke();
        ctx.setLineDash([]);
        const w = ctx.measureText(lv.text).width + 8;
        ctx.fillStyle = lv.color; ctx.fillRect(4, y - 8, w, 16);
        ctx.fillStyle = '#fff'; ctx.fillText(lv.text, 8, y + 4);
      }
      ctx.restore();
      return true;
    },
  });

  function activate(pane, view) {
    clearInterval(T.timer);
    chart.removeIndicator({ name: 'TRADING_MARKS' });
    if (pane !== 'trading' || view !== 'chart') return;
    chart.createIndicator({ name: 'TRADING_MARKS', paneId: 'candle_pane' }, true);
    T.drawn = null;
    if (!typedSymbol && state.ticker) $('trSym').value = state.ticker;
    setType();
    load();
    T.timer = setInterval(load, POLL_MS);
  }
  window.addEventListener('liminal:view', (e) => activate(e.detail.pane, e.detail.view));
  window.addEventListener('liminal:symbol', () => { if (!typedSymbol && state.ticker) $('trSym').value = state.ticker; });
  T.load = load;
  activate(document.body.dataset.pane, document.body.dataset.view);  // opened on this view: its event came first
})();
