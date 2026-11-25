'use strict';
/* Desk (the rail: the Trading Desk; Monday list item 37, the user's design Trading_Desk.docx, built read-only and paper
   first on the user's yes of 2026-11-25). A workspace over the account, laid out as every app's page: on the left what
   there is (the accounts, the decision records saved, the latest events); in the middle the evaluation picked (the
   account overview, the comparison against cash and a benchmark, the horizon and targets, the scenarios at the size
   asked and the size feasible, the position's impact, every constraint in its own unit with its owner, the evidence
   labelled and timed, the gaps); on the right the tool on top (a new evaluation; a preview and a paper order for the
   one shown) and the middle's pick beneath (the constraint picked: its owner, version, dates, and a review to propose).
   Everything is the client API's (/v1/..., gui/desk.py), the same answers any other client gets: the evaluation drawn
   is read back by its id (GET /v1/evaluations/{id}), so the page and the API show one set of decision facts. Paper
   orders only: real orders are off (the user's rule), so no other account is offered an order. Asked while the view
   is shown, every 10 s for the list and the events; nothing while it is hidden. */
(() => {
  const box = document.getElementById('viewDesk');
  if (!box) return;
  const { esc } = window.LiminalIcons;
  const POLL_MS = 10_000;
  let accounts = [], acct = null, overview = null, evals = [], ev = null, rules = null, events = [], cursor = 0;
  let pick = null;  // the constraint picked in the middle (its id): its specifics beneath the tool
  let preview = null, orderKey = null, said = null, busy = false, timer = null, failed = null;

  const shown = () => document.body.dataset.view === 'desk' && !document.hidden;
  const money = (v) => (v == null ? 'unknown' : `$${Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
  const signed = (v) => (v == null ? 'unknown' : `${v >= 0 ? '+' : '−'}${money(Math.abs(v))}`);
  const num = (v) => (v == null ? 'unknown' : Number(v).toLocaleString('en-US'));
  const pct = (v, scale = 100) => (v == null ? 'unknown' : `${(v * scale).toFixed(2)}%`);
  const when = (t) => (t == null ? '–' : esc(new Date(t).toLocaleString()));
  const tile = (title, value, small = '') => `<div class="tile"><h5>${esc(title)}</h5><div class="big">${value}</div><small>${small}</small></div>`;
  const headers = () => (typeof WRITE_HEADERS !== 'undefined' ? WRITE_HEADERS : { 'Content-Type': 'application/json', 'X-Liminal-Charts': '1' });

  async function get(path) {
    const r = await fetch(path);
    const j = await r.json().catch(() => null);
    if (!r.ok) throw new Error(j?.error || `${r.status}`);
    return j;
  }
  async function send(method, path, body, extra = {}) {
    const r = await fetch(path, { method, headers: { ...headers(), ...extra }, body: body == null ? undefined : JSON.stringify(body) });
    const j = await r.json().catch(() => null);
    return { ok: r.ok, status: r.status, body: j };
  }

  async function load() {
    clearTimeout(timer);
    if (!shown()) return;
    try {
      accounts = (await get('/v1/accounts')).accounts || [];
      acct = acct && accounts.some((a) => a.id === acct) ? acct : (accounts.find((a) => a.kind === 'paper') || {}).id || null;
      const [ov, list, cons, evs] = await Promise.all([
        acct ? get(`/v1/accounts/${encodeURIComponent(acct)}/overview`) : null,
        get(`/v1/evaluations?account=${encodeURIComponent(acct || '')}`),
        get('/v1/constraints'),
        get(`/v1/events?account=${encodeURIComponent(acct || '')}&cursor=${cursor}`)]);
      overview = ov; evals = list.evaluations || []; rules = cons;
      if (evs.gap) { events = []; cursor = 0; } else { events = events.concat(evs.events || []).slice(-40); cursor = evs.cursor; }
      failed = null;
    } catch (err) {
      failed = String(err.message || err);
    }
    draw();
    if (shown()) timer = setTimeout(load, POLL_MS);
  }

  async function open(id) {  // the evaluation, read back from the API by its id: what any client gets
    try {
      ev = await get(`/v1/evaluations/${encodeURIComponent(id)}`);
      preview = null; orderKey = null; said = null; pick = (ev.sizing.binding || [])[0] || null;
    } catch (err) { said = { ok: false, text: String(err.message || err) }; }
    draw();
  }

  // -- the left: what there is ----------------------------------------------------------------------------------
  function listHtml() {
    const accts = accounts.map((a) => `<button class="ex-venue${a.id === acct ? ' active' : ''}" data-acct="${esc(a.id)}"
      ${a.connected ? '' : 'disabled'}><span class="ex-name">${esc(a.label)}</span><span class="ex-state">${esc(a.kind)}</span>
      <span class="ex-count">${esc(a.connected ? a.id : a.status || 'not connected')}</span></button>`).join('');
    const recs = evals.map((e) => `<button class="ex-venue${ev && ev.id === e.id ? ' active' : ''}" data-ev="${esc(e.id)}">
      <span class="ex-name">${esc(e.symbol)} ${esc(e.side)} ${num(e.quantity)}</span><span class="ex-state">${esc(e.id)}</span>
      <span class="ex-count">${esc(e.horizon)} · feasible ${num(e.feasibleQty)} · ${esc(e.decision)} · ${when(e.createdAt)}</span></button>`).join('')
      || '<div class="p-muted small">None saved yet: evaluate on the right.</div>';
    const evs = events.slice(-12).reverse().map((e) => `<div class="small"><b>${esc(e.kind)}</b> <span class="p-muted">${when(e.at)}</span></div>`).join('')
      || '<div class="p-muted small">No events yet.</div>';
    return `<div class="ex-group">Accounts</div>${accts}<div class="ex-group">Decision records</div>${recs}
      <div class="ex-group">Events</div>${evs}`;
  }

  // -- the middle: the evaluation picked ----------------------------------------------------------------------------
  function accountHtml(a, title) {
    if (!a) return '';
    return `<h4>${esc(title)}</h4><div class="tiles">${tile('Settled cash', money(a.settledCash), esc(a.source))}
      ${tile('Reserved', money(a.reserved), `${a.commitments.length} commitments`)}${tile('Working buys', money(a.workingBuys), `${a.workingOrders.length} orders`)}
      ${tile('Available', money(a.available), 'settled cash less both')}${tile('Loans', money(a.loans), esc(a.permissions.accountType) + ' account')}
      ${tile('Equity', money(a.equity), `as of ${when(a.asOf)}`)}</div>`;
  }
  function dataHtml() {
    if (failed) return `<div class="warn">The desk did not answer: ${esc(failed)}</div>`;
    if (!ev) {
      const o = overview;
      return `<h3>Trading Desk</h3><div class="p-muted small">Pick a decision record, or evaluate an idea on the right.
        Paper first: real orders are off.</div>${o ? accountHtml(o.account, 'Account overview') : ''}
        ${o ? `<div class="small p-muted">Market: ${esc(o.session.market)}, ${esc(o.session.session)} · policy version ${o.policyVersion}</div>` : ''}
        ${o && o.exposures.length ? `<h4>Exposures</h4><table class="act-table"><thead><tr><th>Symbol</th><th class="num">Value</th><th class="num">Share</th></tr></thead><tbody>
        ${o.exposures.map((x) => `<tr><td>${esc(x.symbol)}</td><td class="num">${money(x.value)}</td><td class="num">${pct(x.share)}</td></tr>`).join('')}</tbody></table>` : ''}`;
    }
    const s = ev.sizing, q = ev.quote;
    const quote = q.status === 'current' ? money(q.price) : `unknown <small>(${esc(q.status)}${q.lastSeen != null ? `: last seen ${money(q.lastSeen)} at ${when(q.asOf)}` : ''})</small>`;
    const head = `<h3>${esc(ev.symbol)} <span class="p-muted">${esc(ev.id)}</span></h3>
      <div class="small p-muted">${esc(ev.account)} · ${esc(ev.side)} · ${esc(ev.horizon.text)} · policy version ${ev.policyVersion}
      ${ev.current.policyChangedSince ? ' · <span class="warn">the policy changed since: shown as evaluated</span>' : ''} · ${when(ev.createdAt)}</div>
      <div class="tiles">${tile('Quote', quote, esc(q.source || ''))}${tile('Asked', num(s.requestedQty), 'shares')}
      ${tile('Feasible', num(s.feasibleQty), s.binding.length ? `binding: ${esc(s.binding.join(', '))}` : '')}
      ${tile('Executable', num(s.executableQty), esc(s.indeterminate.join('; ') || 'checked now'))}</div>`;
    const comp = `<h4>Opportunity comparison <span class="p-muted">benchmark ${esc(ev.benchmark)}</span></h4><table class="act-table"><thead><tr>
      <th>Instrument</th><th>Role</th><th class="num">Quote</th><th class="num">Recent</th></tr></thead><tbody>${ev.comparison.map((c) => `<tr>
      <td>${esc(c.symbol)}</td><td>${esc(c.kind)}</td><td class="num">${c.quote ? (c.quote.status === 'current' ? money(c.quote.price) : esc(c.quote.status)) : '–'}</td>
      <td class="num">${c.kind === 'cash' ? `${pct(c.return)} <small>${esc(c.label)}</small>` : c.performance.return == null ? `unknown <small>${esc(c.performance.gap || '')}</small>`
        : `${pct(c.performance.return)} <small>${c.performance.days} days</small>`}</td></tr>`).join('')}</tbody></table>`;
    const a = ev.assumptions, r = ev.returns;
    const horizon = `<h4>Horizon and targets</h4><div class="small">${esc(ev.horizon.kind)} horizon (${esc(ev.horizon.text)}): review ${when(ev.horizon.reviewAt)},
      ${esc(ev.horizon.reviewWhy)}. Entry ${money(a.entry)}, target ${money(a.target)}, invalidation ${money(a.invalidation)}.
      ${r.annualized != null ? ` Annualized ${pct(r.annualized)}${r.real != null ? `, real ${pct(r.real)} at ${pct(r.inflation)} inflation` : ''}.` : ''}
      ${r.holdingPeriod ? ` Over the holding period ${r.holdingPeriod.pct.toFixed(2)}%.` : ''} <span class="p-muted">${esc(a.note)}</span></div>`;
    const scen = `<h4>Scenarios <span class="p-muted">${ev.beforeCosts ? 'before costs' : 'costs stated apart'}</span></h4><table class="act-table"><thead><tr>
      <th>Scenario</th><th class="num">Price</th><th class="num">At ${num(s.requestedQty)}</th><th class="num">At ${num(s.feasibleQty)}</th></tr></thead><tbody>
      ${ev.scenarios.map((x) => `<tr><td>${esc(x.name)} <small class="p-muted">${esc(x.label)}</small></td><td class="num">${money(x.price)}</td>
      <td class="num">${x.requested ? signed(x.requested.gross) : 'unknown'}</td><td class="num">${x.feasible ? signed(x.feasible.gross) : 'unknown'}</td></tr>`).join('')}</tbody></table>`;
    const im = ev.impact;
    const impact = `<h4>Position and portfolio impact</h4><table class="act-table"><thead><tr><th></th><th class="num">Cash</th><th class="num">Borrowing</th>
      <th class="num">Position value</th><th class="num">Concentration</th></tr></thead><tbody><tr><td>Before</td><td class="num">${money(im.cashBefore)}</td>
      <td class="num">${money(im.borrowingBefore)}</td><td class="num">${money(im.valueBefore)}</td><td class="num">–</td></tr>
      ${['requested', 'feasible'].map((k) => `<tr><td>After, ${k}</td>${im[k] ? `<td class="num">${money(im[k].cashAfter)}</td><td class="num">${money(im[k].borrowingAfter)}</td>
      <td class="num">${money(im[k].valueAfter)}</td><td class="num">${pct(im[k].concentration)}</td>` : '<td colspan="4">unknown</td>'}</tr>`).join('')}</tbody></table>
      <div class="small p-muted">${esc(ev.financing.note)}</div>`;
    const cons = `<h4>Constraints <span class="p-muted">each in its own unit; binding: ${esc(s.binding.join(', ') || 'none')}</span></h4>
      <table class="act-table ex-table"><thead><tr><th>Rule</th><th>Owner</th><th>Unit</th><th class="num">Threshold</th><th class="num">Used</th>
      <th class="num">Feasible</th><th>Status</th></tr></thead><tbody>${s.constraints.map((c) => `<tr data-rule="${esc(c.id)}" class="${pick === c.id ? 'active' : ''}">
      <td>${esc(c.id)} <small class="p-muted">v${c.version}</small></td><td>${esc(c.owner)}</td><td>${esc(c.unit)}</td><td class="num">${num(c.threshold)}</td>
      <td class="num">${num(c.used)}</td><td class="num">${c.limits ? num(c.feasibleQty) : '–'}</td>
      <td>${esc(c.status)}${c.severity !== 'block' ? ` <small>(${esc(c.severity)})</small>` : ''}</td></tr>`).join('')}</tbody></table>`;
    const evid = `<h4>Evidence</h4><table class="act-table"><thead><tr><th>Label</th><th>What</th><th>Value</th><th>When</th></tr></thead><tbody>
      ${ev.evidence.map((x) => `<tr><td>${esc(x.label)}</td><td>${esc(x.what)}</td><td>${esc(x.value == null ? 'unknown' : x.value)}${x.status ? ` <small>${esc(x.status)}</small>` : ''}</td>
      <td>${when(x.asOf)}</td></tr>`).join('')}</tbody></table>
      <h4>Gaps</h4><ul class="small">${ev.gaps.map((g) => `<li>${esc(g)}</li>`).join('')}</ul>`;
    const d = ev.decision;
    const rec = `<h4>Decision record</h4><div class="small">Chosen: <b>${esc(d.choice)}</b>${d.rationale ? ` — ${esc(d.rationale)}` : ''}.
      Alternatives: ${esc(d.alternatives.join(', '))}. Saving or evaluating never submits an order.</div>`;
    return head + accountHtml(ev.accountSnapshot, 'Account, as evaluated') + comp + horizon + scen + impact + cons + evid + rec;
  }

  // -- the right: the tool on top, the middle's pick beneath ----------------------------------------------------------
  function sideHtml() {
    const paper = (accounts.find((a) => a.id === acct) || {}).kind === 'paper';
    const form = `<h3>Evaluate</h3><form id="dkEval" class="tr-ticket" autocomplete="off">
      <label>Symbol <input name="symbol" required maxlength="24" value="${esc(ev ? ev.symbol : '')}"></label>
      <label>Side <select name="side"><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
      <label>Quantity <input name="quantity" type="number" min="0" step="1" value="${ev ? ev.assumptions.quantity : 100}"></label>
      <label>Entry <input name="entry" type="number" min="0" step="any" value="${ev && ev.assumptions.entry != null ? ev.assumptions.entry : ''}"></label>
      <label>Target <input name="target" type="number" min="0" step="any" value="${ev && ev.assumptions.target != null ? ev.assumptions.target : ''}"></label>
      <label>Invalidation <input name="invalidation" type="number" min="0" step="any" value="${ev && ev.assumptions.invalidation != null ? ev.assumptions.invalidation : ''}"></label>
      <label>Gap scenario <input name="gap" type="number" min="0" step="any"></label>
      <label>Horizon <input name="horizon" value="${esc(ev ? ev.horizon.text : '1_session')}" title="1_session, 5_sessions, 30_days, 6_months, 20_years"></label>
      <label>Benchmark <input name="benchmark" value="${esc(ev ? ev.benchmark : 'cash')}"></label>
      <label>Alternatives <input name="alternatives" placeholder="SPY, AAPL"></label>
      <label>Inflation (long horizons) <input name="inflation" type="number" step="any" placeholder="0.03"></label>
      <label>Rationale <input name="rationale" maxlength="400"></label>
      <label>Decision <select name="decision"><option value="save">Save</option><option value="watch">Watch</option>
        <option value="paper_trade">Paper trade</option><option value="request_review">Request review</option><option value="keep_cash">Keep cash</option></select></label>
      <div class="tr-new-act"><button type="submit" class="primary" ${busy || !acct ? 'disabled' : ''}>Evaluate and save</button></div></form>`;
    let order = '';
    if (ev && paper) {
      const qty = ev.sizing.executableQty ?? ev.sizing.feasibleQty ?? '';
      order = `<h4>Preview and paper order</h4><form id="dkPreview" class="tr-ticket" autocomplete="off">
        <label>Quantity <input name="quantity" type="number" min="1" step="1" value="${qty}"></label>
        <label>Limit (empty: market) <input name="price" type="number" min="0" step="any" value="${ev.assumptions.entry ?? ''}"></label>
        <div class="tr-new-act"><button type="submit" ${busy ? 'disabled' : ''}>Preview</button></div></form>
        ${preview ? `<div class="small">Preview ${esc(preview.id)}: <b>${esc(preview.status)}</b>, feasible ${num(preview.sizing.feasibleQty)},
          expires ${when(preview.expiresAt)}${preview.sizing.indeterminate.length ? ` · ${esc(preview.sizing.indeterminate.join('; '))}` : ''}</div>
          ${preview.status === 'valid' ? `<label class="small"><input type="checkbox" id="dkAuth"> I authorize this paper order</label>
          <button id="dkOrder" class="primary" ${busy ? 'disabled' : ''}>Place the paper order</button>` : ''}` : ''}`;
    } else if (ev) {
      order = '<div class="small p-muted">Real orders are off: only the paper account takes orders.</div>';
    }
    const msg = said ? `<div class="small ${said.ok ? '' : 'warn'}">${esc(said.text)}</div>` : '';
    let beneath = '';
    const c = ev && pick ? ev.sizing.constraints.find((x) => x.id === pick) : null;
    if (c) {
      beneath = `<h4>${esc(c.id)}</h4><div class="small">${esc(c.note || '')}</div><table class="act-table ex-mini"><tbody>
        <tr><td>Owner</td><td>${esc(c.owner)}</td></tr>${c.delegatedTo ? `<tr><td>Delegated to</td><td>${esc(c.delegatedTo)}</td></tr>` : ''}
        ${c.parent ? `<tr><td>Parent</td><td>${esc(c.parent)}</td></tr>` : ''}<tr><td>Version</td><td>${c.version}</td></tr>
        <tr><td>Effective</td><td>${when(c.effectiveFrom)} – ${c.effectiveTo ? when(c.effectiveTo) : 'now'}</td></tr>
        <tr><td>Measures</td><td>${esc(c.measure)} in ${esc(c.unit)}</td></tr><tr><td>Severity</td><td>${esc(c.severity)}: ${esc(c.response)}</td></tr>
        <tr><td>Headroom</td><td>${num(c.headroom)} ${esc(c.unit)}</td></tr>
        ${c.toFit ? `<tr><td>To fit</td><td>reduce to ${num(c.toFit.reduceTo)}, or raise by ${num(c.toFit.raiseBy)} ${esc(c.toFit.unit)} (${esc(c.toFit.owner)})</td></tr>` : ''}
        ${c.exception ? `<tr><td>Exception</td><td>review ${esc(c.exception)}</td></tr>` : ''}</tbody></table>
        ${c.broker ? '<div class="small p-muted">The broker\'s limit: it stays binding.</div>' : `<form id="dkReview" class="tr-ticket" autocomplete="off">
          <label>Propose <select name="kind"><option value="change">A change</option><option value="exception">An exception for this symbol</option></select></label>
          <label>Threshold (${esc(c.unit)}) <input name="threshold" type="number" min="0" step="any" value="${c.threshold ?? ''}"></label>
          <label>As <select name="requested_by">${(rules ? rules.roles : ['Client']).map((r) => `<option>${esc(r)}</option>`).join('')}</select></label>
          <label>Rationale <input name="rationale" required maxlength="400"></label>
          <div class="tr-new-act"><button type="submit" ${busy ? 'disabled' : ''}>Send to its owner</button></div>
          <div class="small p-muted">Viewing a role gives no authority: a change beyond the role's delegation goes to the owner.</div></form>`}`;
    }
    return form + msg + order + beneath;
  }

  function draw() {
    const run = () => {
      const keep = [...box.querySelectorAll('.ex-venues, .ex-list, .ex-side')].map((el) => el.scrollTop);
      box.innerHTML = `<div class="ex-venues">${listHtml()}</div><div class="ex-list">${dataHtml()}</div><aside class="ex-side">${sideHtml()}</aside>`;
      box.querySelectorAll('.ex-venues, .ex-list, .ex-side').forEach((el, i) => { el.scrollTop = keep[i] || 0; });
    };
    if (box.contains(document.activeElement) && document.activeElement.matches('input, select')) return;  // not under the typing
    if (window.Views) window.Views.redraw(box, run); else run();
  }

  const field = (form, name) => form.elements[name] && form.elements[name].value.trim();
  box.addEventListener('click', async (e) => {
    const a = e.target.closest('[data-acct]'), r = e.target.closest('[data-ev]'), row = e.target.closest('tr[data-rule]');
    if (a) { acct = a.dataset.acct; ev = null; cursor = 0; events = []; return load(); }
    if (r) return open(r.dataset.ev);
    if (row) { pick = row.dataset.rule; draw(); return; }
    if (e.target.id === 'dkOrder' && preview) {
      if (!box.querySelector('#dkAuth')?.checked) { said = { ok: false, text: 'Tick the authorization first: nothing is sent without it.' }; return draw(); }
      busy = true;
      orderKey = orderKey || `desk-${preview.id}-${Math.random().toString(36).slice(2, 10)}`;  // one key per attempt: a retry reuses it
      const o = preview.order;
      const res = await send('POST', '/v1/orders', { account_id: o.account_id, symbol: o.symbol, side: o.side, quantity: o.quantity,
        price: o.price, preview_id: preview.id, authorized: true }, { 'Idempotency-Key': orderKey });
      busy = false;
      said = res.ok ? { ok: true, text: `Paper order ${res.body.order ? res.body.order.id : ''}: ${res.body.status} (${res.body.orderStatus || ''})` }
        : { ok: false, text: res.body?.error || `refused (${res.status})` };
      if (!res.ok && res.status === 409) preview = null;
      return load();
    }
  });
  box.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = e.target;
    busy = true;
    if (f.id === 'dkEval') {
      const body = { account_id: acct, symbol: field(f, 'symbol'), side: field(f, 'side'), quantity: field(f, 'quantity'),
        entry: field(f, 'entry'), target: field(f, 'target'), invalidation: field(f, 'invalidation'), gap: field(f, 'gap'),
        horizon: field(f, 'horizon'), benchmark: field(f, 'benchmark'), rationale: field(f, 'rationale') || null,
        decision: field(f, 'decision'), alternatives: (field(f, 'alternatives') || '').split(',').map((s) => s.trim()).filter(Boolean),
        assumptions: field(f, 'inflation') ? { inflation: Number(field(f, 'inflation')) } : {} };
      const res = await send('POST', '/v1/evaluations', body);
      busy = false;
      if (res.ok) { said = { ok: true, text: `Saved ${res.body.id}.` }; await open(res.body.id); return load(); }
      said = { ok: false, text: res.body?.error || `refused (${res.status})` };
    } else if (f.id === 'dkPreview' && ev) {
      const res = await send('POST', '/v1/previews', { account_id: ev.account, symbol: ev.symbol, side: ev.side,
        quantity: field(f, 'quantity'), price: field(f, 'price') || null, evaluation_id: ev.id });
      busy = false; orderKey = null;
      if (res.ok) { preview = res.body; said = null; } else said = { ok: false, text: res.body?.error || `refused (${res.status})` };
    } else if (f.id === 'dkReview' && pick) {
      const res = await send('POST', '/v1/reviews', { rule_id: pick, kind: field(f, 'kind'), threshold: field(f, 'threshold'),
        requested_by: field(f, 'requested_by'), rationale: field(f, 'rationale'),
        scope: field(f, 'kind') === 'exception' && ev ? { account: ev.account, symbol: ev.symbol } : {} });
      busy = false;
      said = res.ok ? { ok: true, text: `Review ${res.body.id}: ${res.body.status}, ${res.body.status === 'applied' ? 'within the delegated authority' : `routed to ${res.body.routedTo}`}.` }
        : { ok: false, text: res.body?.error || `refused (${res.status})` };
    }
    busy = false;
    if (document.activeElement && box.contains(document.activeElement)) document.activeElement.blur();
    draw();
  });

  window.addEventListener('liminal:view', () => { if (shown()) load(); else clearTimeout(timer); });
  document.addEventListener('visibilitychange', () => { if (shown()) load(); });
  if (shown()) load();  // the page opened on this view
})();
